"""完整/分步分析编排、内容指纹与受影响产物归档；采集快照只读。"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import os
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from lab_workflows.workflow_state import read_state, write_state
from . import analysis_steps as steps, analysis_plotting
from .stages import ANALYSIS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states
# 保留既有科学函数公开导入。
from .fitting import Bloch2DFitResult, DispersiveFitResult, bloch_2d_surface, dispersive_line, fit_bloch_2d, fit_dispersive_line

PLOTS = ("pd_mean_voltage_map", "pd_drift_corrected_map", "pd_mean_surface", "bloch_2d_fit",
         "bloch_2d_residual_map", "fitted_time_drift", "dispersive_fit_metrics", "best_y_dispersive_fit")


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = steps.contained(self.run_dir / "results", self.run_dir)
        self.state_path = steps.contained(self.run_dir / "analysis_state.json", self.run_dir)
        self.config, self.base_params = steps.load_config(self.run_dir)
        self.params = steps.resolve_options(self.base_params, analysis_parameters or {})
        self.options = {}
        previous = read_state(self.state_path) or {}
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or steps.fingerprint(self.run_dir)
        self._refresh()

    def _signature(self, name):
        return steps.signature(steps.contained(self.results / name, self.run_dir))

    def _refresh(self):
        current = steps.fingerprint(self.run_dir)
        if current != self.fingerprint:
            self.fingerprint = current
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            files = record.get("files", {})
            if record["status"] == "running" or (record["status"] == "completed" and (
                    not set(stage.artifacts).issubset(files) or any(self._signature(name) != value for name, value in files.items()))):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if any(record["status"] == "stale" for record in self.states.values()):
            self.summary.update(status="stale")
        elif not self.summary:
            self.summary.update(status="not_started")

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options={},
            steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=steps.plain(deepcopy(self.summary)))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        report_runtime_progress("analysis", "XY 剩磁分析状态已保存", data={"analysis_workflow": self.snapshot(), "estimated_remaining_seconds": None})

    def _outputs(self, item):
        if item == "B01":
            names = ["xy_residual_inputs.json"]
        elif item == "B02":
            names = ["calibration_results.npz", "xy_residual_calibration.csv", "y_dispersive_fits.csv", "xy_residual_fit.json"]
        else:
            names = ["analysis.json", "analysis.yaml", *(f"{name}.{ext}" for name in PLOTS for ext in ("png", "pdf"))]
        return [self.results / name for name in names if (self.results / name).is_file()]

    def _archive(self, item):
        files = sorted({path for stage in invalidated_stages(self.stages, item) for path in self._outputs(stage.id)})
        if not files:
            return
        for path in files:
            steps.contained(path, self.run_dir)
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in files:
            path.replace(backup / path.name)
        self.summary["backup_directory"] = backup.name

    def execute(self, selected, expected_revision=None):
        self._refresh()
        if expected_revision is not None and self.revision != expected_revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        item = "B01"
        self.revision += 1
        try:
            check_cancelled()
            # 完整结构检查先于归档，坏 raw 不会破坏可查看的旧结果。
            data = steps.load_data(self.run_dir)
            if steps.fingerprint(self.run_dir) != self.fingerprint:
                raise steps.AnalysisQualityError("分析输入在读取期间发生变化，请重新执行 B01")
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
                    result = steps.input_summary(data)
                    write_state(self.results / "xy_residual_inputs.json", result | {"fingerprint": self.fingerprint})
                elif item == "B02":
                    result = steps.evaluate(self.run_dir, data)
                else:
                    result = analysis_plotting.render(self.run_dir)
                if steps.fingerprint(self.run_dir) != self.fingerprint:
                    raise steps.AnalysisQualityError("分析输入在计算期间发生变化，请重新执行 B01")
                self.summary.update(result, status="completed" if item == "B03" else "partial")
                artifacts = [path.relative_to(self.results).as_posix() for path in self._outputs(item)]
                stage = next(stage for stage in self.stages if stage.id == item)
                if not set(stage.artifacts).issubset(artifacts):
                    raise RuntimeError(f"{item} 缺少必需分析产物")
                self.states[item] = dict(status="completed", error="", artifacts=artifacts,
                    files={name: self._signature(name) for name in artifacts}, parameters={}, revision=self.revision,
                    input_fingerprint=deepcopy(self.fingerprint))
                self.persist()
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else (
                "quality_failed" if isinstance(exc, (steps.AnalysisQualityError, ValueError)) else "failed")
            invalidate(self.stages, self.states, item)
            self.states[item] = dict(status=status, error=str(exc))
            self.summary.update(status=status, error=str(exc))
            try:
                self.persist()
            except Exception as save_error:
                if hasattr(exc, "add_note"):
                    exc.add_note(f"分析诊断保存另失败: {save_error}")
            raise
        return deepcopy(self.summary)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None) -> dict:
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    return ctx.execute([s.id for s in ANALYSIS] if step_ids is None else step_ids, expected_revision)


def main():
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
