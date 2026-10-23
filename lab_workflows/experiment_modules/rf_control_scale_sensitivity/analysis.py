"""复用 DirectAW 的分析修订、指纹、归档和分阶段执行协议。"""
import hashlib
import json
import math
from ..rf_sensitivity_direct_aw import analysis as shared
from . import stages, run_format, analysis_steps

ANALYSIS_OPTIONS = {"NOISE_NPERSEG": "noise_nperseg", "NOISE_OUTLIER_POLICY": "noise_outlier_policy",
    "SENSITIVITY_BAND_MIN_HZ": "band_min_hz", "SENSITIVITY_BAND_MAX_HZ": "band_max_hz"}


class AnalysisWorkflow(shared.AnalysisWorkflow):
    identity = run_format
    stages = stages.ANALYSIS
    steps_module = analysis_steps
    analysis_version = "rf-control-scale-v6-scale-range-factors"

    def __init__(self, run_dir, analysis_parameters=None):
        # 保留旧分析请求的兼容入口，但局部点数不再参与全曲线色散拟合。
        overrides = {key: value for key, value in (analysis_parameters or {}).items() if key != "SLOPE_FIT_POINTS"}
        if overrides.keys() - ANALYSIS_OPTIONS.keys():
            raise ValueError("包含不支持的分析参数")
        super().__init__(run_dir, {k: v for k, v in overrides.items() if k == "NOISE_NPERSEG"})
        self.options.pop("slope_fit_points", None)
        for external, name in ANALYSIS_OPTIONS.items():
            fallback = self.config["parameters"].get(external, "mad_high") if name == "noise_outlier_policy" else self.config["parameters"][external]
            old = self.options.setdefault(name, fallback)
            value = overrides.get(external, old)
            if name == "noise_outlier_policy":
                if value not in ("mad_high", "none"):
                    raise ValueError("NOISE_OUTLIER_POLICY 必须为 mad_high 或 none")
            elif isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{external} 必须为有限数")
            if value != old:
                stages.invalidate(self.stages, self.states, "B03")
            self.options[name] = value
        if not 0 < self.options["band_min_hz"] < self.options["band_max_hz"]:
            raise ValueError("公共统计频带必须为递增正数")
        self.config["analysis_options"] = dict(self.options)

    def _archive_from(self, item):
        super()._archive_from(item)
        for key in ("controls", "points", "plot_labels", "comparison_band_Hz", "common_band_valid", "noise_enabled",
                    "slope_definition", "dispersive_linewidth_definition", "gyromagnetic_ratio_Hz_per_nT",
                    "full_analysis_panels", "raw_format_version", "phase_calibration_scope", "noise_rejection",
                    "rf_phase_mode", "rf_manual_phase_deg",
                    "sensitivity_statistic", "sensitivity_assumption", "uncertainty"):
            self.summary.pop(key, None)

    def input_fingerprint(self):
        result = {}
        for path in sorted(self.run_dir.rglob("*")):
            relative = path.relative_to(self.run_dir)
            if path.is_file() and ("raw" in relative.parts or path.name == "experiment_config.yaml"):
                if self.run_dir not in path.resolve().parents:
                    raise ValueError("原始输入越出运行目录")
                if path.name == "experiment_config.yaml":
                    import yaml
                    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
                    cfg = {key: value for key, value in cfg.items() if key not in ("analysis_status", "analysis_error")}
                    result[relative.as_posix()] = hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                else:
                    result[relative.as_posix()] = self._file_signature(path)
        return result


def analyze(run_dir):
    return shared.analyze(run_dir, workflow_type=AnalysisWorkflow)


def main():
    shared.main(workflow_type=AnalysisWorkflow)


if __name__ == "__main__":
    main()
