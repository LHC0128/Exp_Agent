"""分阶段分析的输入校验、原有数值评估与结果生成。"""
from dataclasses import asdict
import json
import math
from pathlib import Path
import numpy as np
import yaml
from .analysis_core import FitResult
from .analysis_plotting import analyze_core, _plot_measured_amplitude_response
from .point_analysis import evaluate_mx_y_rf_point
from . import run_format


class AnalysisQualityError(ValueError):
    """拟合门槛失败，诊断已经保留。"""


def plain(value):
    """会话 JSON 不允许 NaN；科学数组在数值缓存中原样保存。"""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [plain(item) for item in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _encode(value):
    if isinstance(value, FitResult):
        return {"__fit__": asdict(value)}
    if isinstance(value, np.ndarray):
        return {"__array__": value.tolist(), "dtype": str(value.dtype)}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _decode(value):
    if isinstance(value, dict):
        if "__fit__" in value:
            return FitResult(**value["__fit__"])
        if "__array__" in value:
            return np.asarray(value["__array__"], dtype=value["dtype"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def load_inputs(directory, config, params):
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    raw = Path(directory) / "raw"
    required = {"amplitude_scan.npz": ("signed_amplitude_vpp", "r_std_v")}
    if params.linewidth_mode == "frequency_sweep":
        required["frequency_scan.npz"] = ("frequency_hz", "r_scalar_mean_v")
    required.update({f"noise_{index:03d}.npz": ("r_v", "actual_rate_sa_s") for index in range(params.noise_n_avg)})
    for name, keys in required.items():
        path = raw / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少原始数据：{name}")
        with np.load(path, allow_pickle=False) as data:
            if missing := set(keys) - set(data.files):
                raise ValueError(f"{name} 缺少字段：" + "、".join(sorted(missing)))
            if name == "amplitude_scan.npz" and not {"r_mean_v", "r_scalar_mean_v"}.intersection(data.files):
                raise ValueError("幅度汇总缺少 R 均值")


def evaluate(directory, params):
    results = Path(directory) / "results"
    results.mkdir(exist_ok=True)
    value = evaluate_mx_y_rf_point(Path(directory) / "raw", params)
    (results / "evaluation.json").write_text(json.dumps(_encode(value), ensure_ascii=False), encoding="utf-8")
    diagnostics = plain({key: value.get(key) for key in (
        "valid", "invalid_reasons", "warnings", "response_result", "linewidth_result", "zero_point_invalid_reasons")})
    (results / "evaluation_diagnostics.yaml").write_text(
        yaml.safe_dump(diagnostics, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if not value["response_fit"].success:
        _plot_measured_amplitude_response(results_dir=results, amplitude_vpp=value["amplitude_vpp"],
            r_mean_v=value["r_mean_v"], r_std_v=value["r_std_v"], bad_point_mask=value["bad_point_mask"])
        raise AnalysisQualityError("幅度色散拟合质量不合格：" + "；".join(value["response_fit"].rejection_reasons))
    if "sensitivity" not in value:
        raise AnalysisQualityError("；".join(value["invalid_reasons"]))
    return diagnostics


def report(directory, params, config):
    value = _decode(json.loads((Path(directory) / "results/evaluation.json").read_text(encoding="utf-8")))
    return analyze_core(Path(directory), params, config, ignore_relative_gamma_uncertainty=False,
        initial_center=None, evaluation=value)
