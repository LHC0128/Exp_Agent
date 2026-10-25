"""独立批次分析；不连接硬件、不重算或改写子运行。"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_plotting, analysis_steps, run_format
from .stages import ANALYSIS, initial_states, invalidate, invalidated_stages, validate_selection


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return [path.stat().st_size, digest.hexdigest()]


class AnalysisWorkflow:
    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.config = run_format.read_config(self.run_dir)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("批次分析无独立覆盖参数；不会重算静磁场子运行")
        self.options = {}
        previous = read_state(self.run_dir / "analysis_state.json") or {}
        self.revision = previous.get("revision", 0)
        self.states = deepcopy(previous.get("steps") or initial_states(ANALYSIS))
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running" or record["status"] == "completed" and any(
                    signature(self.results / name) != record.get("files", {}).get(name) for name in stage.artifacts):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
        if self.states["B03"]["status"] != "completed":
            self.summary = dict(status="not_started", success=False)

    def input_fingerprint(self):
        current = run_format.read_config(self.run_dir)
        if reason := run_format.analysis_block_reason(current):
            raise ValueError(reason)
        config = {key: value for key, value in current.items() if key not in ("analysis_status", "analysis_error")}
        source = run_format.MANIFEST if config["raw_format_version"] == 1 else run_format.LEGACY_SUMMARY
        return dict(version="local-dc-batch-v1", config=hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            source=signature(self.run_dir / source))

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options={},
            steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.run_dir / "analysis_state.json", self.snapshot())
        report_runtime_progress("analysis", "局部优化批次分析", data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        files = {self.results / name for stage in invalidated_stages(ANALYSIS, item) for name in stage.artifacts}
        existing = [path for path in files if path.is_file()]
        if existing:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in existing:
                path.replace(backup / path.name)

    def execute(self, selected, expected_revision=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        if self.input_fingerprint() != self.fingerprint:
            raise ValueError("分析输入已改变，请重新加载并执行 B01")
        selected = validate_selection(ANALYSIS, self.states, selected)
        # 输入先校验，再归档旧结果；读取失败不挪走仍可查看的旧报告。
        try:
            inputs = analysis_steps.load_inputs(self.run_dir)
            if self.input_fingerprint() != self.fingerprint:
                raise ValueError("分析输入在读取期间发生变化，请重新执行 B01")
        except BaseException as exc:
            invalidate(ANALYSIS, self.states, "B01")
            self.states["B01"] = dict(status="quality_failed" if isinstance(exc, ValueError) else "failed", error=str(exc))
            self.summary = dict(status=self.states["B01"]["status"], success=False, error=str(exc))
            self.revision += 1
            self.persist()
            raise
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        for item in selected:
            check_cancelled()
            self._archive_from(item)
            invalidate(ANALYSIS, self.states, item)
            self.states[item] = dict(status="running", error="")
            self.summary = dict(status="running", success=False)
            self.persist()
            try:
                if item == "B01":
                    write_state(self.results / "analysis_inputs.json", inputs)
                elif item == "B02":
                    frozen = read_state(self.results / "analysis_inputs.json")
                    write_state(self.results / "optimization_decision.json", analysis_steps.decide_best_result(frozen["rows"]))
                else:
                    frozen = read_state(self.results / "analysis_inputs.json")
                    decision = read_state(self.results / "optimization_decision.json")
                    analysis_plotting.plot_summary(self.run_dir, frozen, decision)
                    self.summary = analysis_steps.export_report(self.run_dir, frozen, decision)
                stage = next(stage for stage in ANALYSIS if stage.id == item)
                files = {name: signature(self.results / name) for name in stage.artifacts}
                if any(value is None for value in files.values()):
                    raise RuntimeError("分析缺少应当生成的产物")
                self.states[item] = dict(status="completed", error="", files=files,
                    input_fingerprint=deepcopy(self.fingerprint), parameters={}, revision=self.revision)
                if item != "B03":
                    self.summary = dict(status="partial", success=False)
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = dict(status=status, error=str(exc))
                self.summary = dict(status=status, success=False, error=str(exc))
                raise
            finally:
                self.persist()
        return deepcopy(self.summary)


def analyze(run_dir):
    return AnalysisWorkflow(run_dir).execute([stage.id for stage in ANALYSIS])


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    ctx = AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters"))
    ctx.execute(payload.get("step_ids") or [stage.id for stage in ANALYSIS], payload.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
