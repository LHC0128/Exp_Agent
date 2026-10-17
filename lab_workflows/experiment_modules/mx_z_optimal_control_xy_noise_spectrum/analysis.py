"""噪声网格的分阶段分析；复用内容指纹、产物归档与历史参数隔离。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import os
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state
from ..mx_z_optimal_control_xy_rf_sensitivity.analysis import AnalysisWorkflow as GridAnalysisWorkflow
from . import analysis_steps, run_format
from .models import MxZOptimalControlXYNoiseSpectrumParams
from .stages import ANALYSIS, ANALYSIS_KEYS, initial_states, invalidate, validate_selection


class AnalysisWorkflow(GridAnalysisWorkflow):
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        base, self.config = analysis_steps._load_params(self.run_dir)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {key: base.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items() if key in ANALYSIS_KEYS})
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        self.refresh_products()
        if analysis_parameters:
            if unknown := analysis_parameters.keys() - set(ANALYSIS_KEYS):
                raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
            if any(self.options[key] != value for key, value in analysis_parameters.items()):
                invalidate(self.stages, self.states, "B02")
                self.options.update(analysis_parameters)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in self.options.values()):
            raise ValueError("分析覆盖参数必须为有限数值")
        self.params = MxZOptimalControlXYNoiseSpectrumParams.from_external(base.to_external() | self.options)
        if errors := self.params.validate_noise_parameters():
            raise ValueError("；".join(errors))

    def input_fingerprint(self):
        return super().input_fingerprint() | {"analysis_version": "mx-z-xy-noise-spectrum-v1"}

    def execute(self, selected, expected_revision=None):
        base, self.config = analysis_steps._load_params(self.run_dir)
        self.params = MxZOptimalControlXYNoiseSpectrumParams.from_external(base.to_external() | self.options)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.fingerprint = fingerprint
            self.revision += 1
        self.refresh_products()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary = {"status": "running", "options": dict(self.options)}
            failure = None
            try:
                self.persist()
                # 验收当前输入后才归档旧产物，坏数据不能搬走上次完整结果。
                analysis_steps.load_inputs(self.run_dir, self.params)
                self._archive_from(item)
                if item == "B02":
                    self.summary.update(analysis_steps.evaluate(self.run_dir, self.params, self.config))
                elif item == "B03":
                    self.summary.update(analysis_steps.write_report(self.results))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = self.product_files(stage)
                if missing := set(stage.artifacts) - set(files):
                    raise RuntimeError("分析阶段缺少产物：" + "、".join(sorted(missing)))
                self.states[item] = {"status": "completed", "error": "", "artifacts": files,
                    "files": {name: self._file_signature(self.results / name) for name in files},
                    "input_fingerprint": deepcopy(self.fingerprint), "parameters": dict(self.options),
                    "parameter_fingerprint": hashlib.sha256(json.dumps(self.options, sort_keys=True).encode()).hexdigest(),
                    "revision": self.revision}
                self.summary["status"] = "completed" if item == "B03" else "partial"
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


def analyze(run_dir=None, *, low_freq_skip_hz=None, noise_band_max_hz=None, output_dir=None):
    run_dir = Path(run_dir).resolve() if run_dir is not None else runtime_run_dir()
    options = {key: value for key, value in zip(ANALYSIS_KEYS, (low_freq_skip_hz, noise_band_max_hz)) if value is not None}
    config = analysis_steps._load_yaml(run_dir / "experiment_config.yaml")
    if output_dir is not None or config.get("measurement_mode") == "xy_bias_grid_rf_sensitivity":
        params, config = analysis_steps._load_params(run_dir, low_freq_skip_hz=low_freq_skip_hz,
            noise_band_max_hz=noise_band_max_hz)
        analysis_steps.load_inputs(run_dir, params, allow_legacy=True)
        result_dir = Path(output_dir).resolve() if output_dir is not None else run_dir / "results"
        analysis_steps.evaluate(run_dir, params, config, result_dir)
        return analysis_steps.write_report(result_dir)
    return AnalysisWorkflow(run_dir, options).execute([stage.id for stage in ANALYSIS])


def analyze_legacy_xy_rf_noise_run(old_run_dir, output_dir=None, *, low_freq_skip_hz=3., noise_band_max_hz=200.):
    return analyze(old_run_dir, low_freq_skip_hz=low_freq_skip_hz, noise_band_max_hz=noise_band_max_hz,
        output_dir=output_dir)


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
