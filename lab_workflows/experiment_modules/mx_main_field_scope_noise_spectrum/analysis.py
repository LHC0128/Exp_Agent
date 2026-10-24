"""内容指纹、独立覆盖值与分步分析编排；历史采集快照保持只读。"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_steps as steps
from .stages import ANALYSIS, ANALYSIS_KEYS, initial_states, invalidate, invalidated_stages, validate_selection

class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = steps.contained(self.run_dir / "results", self.run_dir)
        self.state_path = steps.contained(self.run_dir / "analysis_state.json", self.run_dir)
        self.config, self.base_params = steps.load_config(self.run_dir)
        previous = read_state(self.state_path) or {}
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items() if key in ANALYSIS_KEYS})
        resolved = self.options | (analysis_parameters or {})
        self.params = steps.resolve_options(self.base_params, resolved)
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or steps.fingerprint(self.run_dir)
        self._refresh()
        if resolved != self.options:
            invalidate(self.stages, self.states, "B02")
        self.options = resolved

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
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
            steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=steps.plain(deepcopy(self.summary)))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        report_runtime_progress("analysis", "主场示波器 分析状态已保存", data={"analysis_workflow": self.snapshot()})

    def _outputs(self, item):
        if item == "B01":
            return [self.results / "scope_noise_inputs.json"]
        patterns = ["analysis.*","psd_matrix.npz","popt_fit.npz","noise_spectra.npz","noise_spectra.csv","lorentzian_fit_parameters.csv","global_2d_noise_separation.npz","global_2d_noise_spectra.csv","global_2d_analysis.*","fit_example_*.png","fit_example_*.pdf","main_field_control_axis.*","noise_spectrum_2d.*","noise_spectra_extracted.*","lorentzian_fit_parameters.*","global_2d_noise_spectra.*","global_2d_control_background.*","global_2d_fractional_residual.*","global_2d_accuracy.*","global_2d_fit_examples.*"]
        return sorted({path for pattern in patterns for path in self.results.glob(pattern) if path.is_file()})

    def _archive(self, item):
        files = sorted({path for stage in invalidated_stages(self.stages, item)
            for path in self._outputs(stage.id) if path.is_file()})
        if not files:
            return
        for path in files:
            steps.contained(path, self.run_dir)
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in files:
            target = backup / path.relative_to(self.results)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
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
            # 在归档旧产物前完成全部结构验证，坏文件不会先破坏可查看的结果。
            inputs = steps.load_inputs(self.run_dir, self.params)
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
                    write_state(self.results / "scope_noise_inputs.json", inputs | {"fingerprint": self.fingerprint})
                    self.summary.update(inputs, status="partial")
                else:
                    result = steps.evaluate(self.run_dir, self.params)
                    if steps.fingerprint(self.run_dir) != self.fingerprint:
                        raise steps.AnalysisQualityError("分析输入在计算期间发生变化，请重新执行 B01")
                    self.summary.update(result, status="completed" if result["success"] else "quality_failed")
                artifacts = [path.relative_to(self.results).as_posix() for path in self._outputs(item) if path.is_file()]
                stage = next(stage for stage in self.stages if stage.id == item)
                if not set(stage.artifacts).issubset(artifacts):
                    raise RuntimeError(f"{item} 缺少必需分析产物")
                self.states[item] = dict(status="quality_failed" if item == "B02" and not result["success"] else "completed", error="", artifacts=artifacts,
                    files={name: self._signature(name) for name in artifacts},
                    parameters=dict(self.options), revision=self.revision, input_fingerprint=deepcopy(self.fingerprint))
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
    return ctx.execute([stage.id for stage in ANALYSIS] if step_ids is None else step_ids, expected_revision)


def main() -> int:
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
