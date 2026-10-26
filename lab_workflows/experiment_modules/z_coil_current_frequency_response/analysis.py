"""独立离线分析的阶段编排、输入指纹与产物归档。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from lab_workflows.workflow_state import read_state, write_state
from . import analysis_steps as steps
from .run_format import EXPERIMENT_ID, analysis_block_reason
from .stages import ANALYSIS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states
# 保留已发布的科学函数导入路径。
from .analysis_steps import (_builtin, _load_yaml, _as_scalar, _circular_std_deg, _load_capture,
    falling_edge_time, fit_sine, select_post_settle_cycles, _phasor, _fit_swept_capture,
    _aggregate_points, _plot_transfer, _analyze_swept_captures, FORMAT_VERSION, OUTPUT_PROTOCOL,
    COMMAND_VOLTAGE_REFERENCE, PHASE_REFERENCE_MEASURED, PHASE_REFERENCE_COMMANDED,
    FIT_CORRELATION_MIN, DRIVE_REFERENCE_CORRELATION_MIN, PHASE_STD_MAX_DEG)

ANALYSIS_VERSION = "z-coil-current-frequency-stages-v3-exact-frequency-repeats"


class AnalysisWorkflow:
    """每次分析绑定具体运行目录；不读取当前默认值或连接仪器。"""

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = _load_yaml(self.run_dir / "experiment_config.yaml")
        if reason := analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("本实验没有可独立覆盖的分析参数：" + "、".join(sorted(analysis_parameters)))
        self.options = {}
        previous = read_state(self.state_path) or {}
        self.states = initial_states(ANALYSIS) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != record.get("files", {}).get(name)
                    for name in stage.artifacts)):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if self.states["B03"]["status"] != "completed":
            self.summary["status"] = "stale" if previous else "not_started"

    def input_fingerprint(self):
        config = _load_yaml(self.run_dir / "experiment_config.yaml")
        if reason := analysis_block_reason(config):
            raise ValueError(reason)
        clean = {key: value for key, value in config.items()
                 if key not in ("analysis_status", "analysis_error")}
        return dict(analysis_version=ANALYSIS_VERSION,
            config_sha256=hashlib.sha256(json.dumps(clean, sort_keys=True,
                ensure_ascii=False, default=str).encode("utf-8")).hexdigest(),
            raw_files={path.name: self._file_signature(path)
                for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()})

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, *digest.digest()]

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options,
                    steps=self.states, fingerprint=self.fingerprint, summary=self.summary)

    def persist(self):
        write_state(self.state_path, self.snapshot())
        write_state(self.results / "analysis_summary.json", self.summary)
        report_runtime_progress("analysis", f"电流频响分析：{self.summary.get('status', 'not_started')}",
                                data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(ANALYSIS, item) for name in stage.artifacts}
        paths = [self.results / name for name in names if (self.results / name).is_file()]
        if not paths:
            return
        summary = self.results / "analysis_summary.json"
        if summary.is_file():
            paths.append(summary)
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in paths:
            shutil.move(str(path), str(backup / path.name))
        return backup.name

    def execute(self, selected, expected_revision=None):
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "completed" and any(self._file_signature(self.results / name) !=
                    record.get("files", {}).get(name) for name in stage.artifacts):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(ANALYSIS, self.states, selected)
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        for item in selected:
            check_cancelled()
            backup = self._archive_from(item)
            invalidate(ANALYSIS, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.update(status="running")
            self.summary.pop("error", None)
            self.persist()
            try:
                if item == "B01":
                    paths = steps.load_inputs(self.run_dir)
                    write_state(self.results / "analysis_inputs.json", dict(
                        captures=[path.name for path in paths], fingerprint=self.fingerprint))
                    self.summary = dict(experiment_id=EXPERIMENT_ID, run_dir=str(self.run_dir),
                        capture_count=len(paths), status="partial")
                elif item == "B02":
                    self.summary = steps.calculate_response(self.run_dir)
                    self.summary["status"] = "partial"
                elif item == "B03":
                    self.summary = steps.export_report(self.run_dir)
                    self.summary["status"] = "completed"
                if backup:
                    self.summary["backup_directory"] = backup
                stage = next(stage for stage in ANALYSIS if stage.id == item)
                signatures = {name: self._file_signature(self.results / name) for name in stage.artifacts}
                if any(value is None for value in signatures.values()):
                    raise RuntimeError(f"{item} 缺少应当输出的分析产物")
                self.states[item] = {"status": "completed", "error": "", "files": signatures}
            except BaseException as exc:
                status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
                          "quality_failed" if isinstance(exc, ValueError) else "failed")
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    return ctx.execute(step_ids or [stage.id for stage in ANALYSIS], expected_revision)


def main() -> int:
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
