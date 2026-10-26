"""完整/分步共用离线分析；显式目录、内容指纹、重算归档和独立诊断状态。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import math
import os

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from lab_workflows.workflow_state import read_state, write_state
from . import run_format, analysis_steps
from .stages import ANALYSIS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states



def _json_value(value):
    """只有状态协议把非有限科学诊断值转为 null；原 NPZ/YAML 和返回值保持不变。"""
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        if analysis_parameters:
            raise ValueError("此实验没有可覆盖的离线参数；分析读取所选运行的原始配置")
        self.run_dir = Path(run_dir).resolve()
        run_format.load_config(self.run_dir)
        self.results = run_format.safe_path(self.run_dir, "results")
        self.state_path = run_format.safe_path(self.run_dir, "analysis_state.json")
        previous = read_state(self.state_path) or {}
        self.states = {s.id: deepcopy(previous.get("steps", {}).get(s.id, initial_states(self.stages)[s.id])) for s in self.stages}
        self.revision = previous.get("revision", 0)
        self.options = {}
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or run_format.fingerprint(self.run_dir)
        self._refresh()

    def _signature(self, name):
        return run_format.signature(run_format.safe_path(self.run_dir, "results/" + name))

    def _refresh(self):
        fingerprint = run_format.fingerprint(self.run_dir)
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            files = record.get("files", {})
            if record["status"] == "running" or record["status"] == "completed" and (
                    not files or any(self._signature(name) != signature for name, signature in files.items())):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if any(state["status"] == "stale" for state in self.states.values()):
            self.summary["status"] = "stale"
        elif not self.summary:
            self.summary["status"] = "not_started"

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options={},
            steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        report_runtime_progress("analysis", "分析阶段状态已保存", data={"analysis_workflow": self.snapshot()})

    def _archive(self, item):
        names = {"input_manifest.json"} if item == "B01" else set()
        names.update(run_format.RESULT_FILES)
        paths = [run_format.safe_path(self.run_dir, "results/" + name) for name in names]
        paths = [path for path in paths if path.is_file()]
        if paths:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in paths:
                path.replace(backup / path.name)
            self.summary["backup_directory"] = backup.name

    def execute(self, selected, expected_revision=None):
        self._refresh()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        item = selected[0]
        result = {}
        try:
            for item in selected:
                check_cancelled()
                self.results.mkdir(exist_ok=True)
                self._archive(item)
                invalidate(self.stages, self.states, item)
                self.states[item] = dict(status="running", error="")
                self.summary.update(status="running")
                self.summary.pop("error", None)
                self.persist()
                validated = run_format.validate_inputs(self.run_dir)
                if item == "B01":
                    result = validated
                    write_state(self.results / "input_manifest.json", dict(input_fingerprint=self.fingerprint, **validated))
                    names = ["input_manifest.json"]
                else:
                    result = analysis_steps.evaluate(self.run_dir)
                    names = [name for name in run_format.RESULT_FILES if (self.results / name).is_file()]
                    if not names:
                        raise RuntimeError("离线算法没有生成结果文件")
                    result["artifacts"] = [str(self.results / name) for name in names]
                if run_format.fingerprint(self.run_dir) != self.fingerprint:
                    raise ValueError("分析期间原始输入发生变化，请重新执行 B01")
                self.states[item] = dict(status="completed", error="", artifacts=names,
                    files={name: self._signature(name) for name in names}, parameters={}, revision=self.revision,
                    input_fingerprint=deepcopy(self.fingerprint))
                self.summary.update(_json_value(result), status="completed" if item == "B02" else "partial")
                self.persist()
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
            invalidate(self.stages, self.states, item)
            self.states[item] = dict(status=status, error=str(exc))
            self.summary.update(status=status, error=str(exc))
            try:
                self.persist()
            except Exception as secondary:
                getattr(exc, "add_note", print)(f"分析诊断保存失败：{secondary}")
            raise
        return result


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    return ctx.execute([s.id for s in ANALYSIS] if step_ids is None else step_ids, expected_revision)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=payload.get("step_ids"), analysis_parameters=payload.get("analysis_parameters"),
        expected_revision=payload.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
