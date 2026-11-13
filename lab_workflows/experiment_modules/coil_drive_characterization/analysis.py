"""完整/分步离线分析共用编排、输入指纹与产物归档。"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from lab_workflows.workflow_stages import initial_states
from lab_workflows.workflow_state import read_state, write_state

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from . import analysis_steps as steps
from .stages import ANALYSIS, ANALYSIS_KEYS, invalidate, invalidated_stages, validate_selection


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = steps.output_path(self.run_dir, "results")
        self.state_path = steps.output_path(self.run_dir, "analysis_state.json")
        self.config, self.base_params = steps.load_config(self.run_dir)
        previous = read_state(self.state_path) or {}
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or steps.fingerprint(self.run_dir)
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({k: v for k, v in previous.get("options", {}).items() if k in ANALYSIS_KEYS})
        before = dict(self.options)
        self.params = steps.resolve_options(self.base_params, self.options | (analysis_parameters or {}))
        self.options = {key: self.params.to_external()[key] for key in ANALYSIS_KEYS}
        for stage in self.stages:
            if any(self.options[key] != before[key] for key in stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self._refresh()

    def _signature(self, name):
        return steps.signature(steps.output_path(self.run_dir, "results/" + name))

    def _refresh(self):
        current = steps.fingerprint(self.run_dir)
        if current != self.fingerprint:
            self.fingerprint = current
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            names = record.get("files", {})
            if record["status"] == "running" or (record["status"] == "completed" and (
                    not set(stage.artifacts).issubset(names) or any(self._signature(n) != s for n, s in names.items()))):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if any(record["status"] == "stale" for record in self.states.values()):
            self.summary.update(status="stale")
        elif not self.summary:
            self.summary.update(status="not_started")

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
                    steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        report_runtime_progress("analysis", "线圈驱动分析状态已保存", data={"analysis_workflow": self.snapshot()})

    def _outputs(self, item):
        names = next(s.artifacts for s in self.stages if s.id == item)
        return [path for name in names if (path := steps.output_path(self.run_dir, "results/" + name)).is_file()]

    def _archive(self, item):
        paths = sorted({path for stage in invalidated_stages(self.stages, item) for path in self._outputs(stage.id)})
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
        try:
            self.results.mkdir(exist_ok=True)
            for item in selected:
                check_cancelled()
                self._archive(item)
                invalidate(self.stages, self.states, item)
                self.states[item] = dict(status="running", error="")
                self.summary.update(status="running")
                self.summary.pop("error", None)
                self.persist()
                if item == "B01":
                    result = steps.transfer(self.run_dir, self.params)
                else:
                    b01 = json.loads(steps.output_path(self.run_dir, "results/transfer.json").read_text(encoding="utf-8"))
                    result = steps.pulses(self.run_dir, self.params, b01)
                if steps.fingerprint(self.run_dir) != self.fingerprint:
                    raise ValueError("分析输入在计算期间发生变化，请重新执行 B01")
                artifacts = [p.name for p in self._outputs(item)]
                stage = next(s for s in self.stages if s.id == item)
                if not set(stage.artifacts).issubset(artifacts):
                    raise RuntimeError(f"{item} 缺少必需分析产物")
                parameters = {key: self.options[key] for key in stage.parameters}
                self.states[item] = dict(status="completed", error="", artifacts=artifacts,
                    files={name: self._signature(name) for name in artifacts}, parameters=parameters,
                    parameter_fingerprint=hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest(),
                    revision=self.revision, input_fingerprint=deepcopy(self.fingerprint))
                self.summary.update(result, status="completed" if item == "B02" else "partial")
                self.persist()
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else (
                "quality_failed" if isinstance(exc, ValueError) else "failed")
            invalidate(self.stages, self.states, item)
            self.states[item] = dict(status=status, error=str(exc))
            self.summary.update(status=status, error=str(exc))
            try:
                self.persist()
            except Exception as save_error:
                exc.add_note(f"分析诊断保存亦失败：{save_error}")
            raise
        return deepcopy(self.summary)


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
