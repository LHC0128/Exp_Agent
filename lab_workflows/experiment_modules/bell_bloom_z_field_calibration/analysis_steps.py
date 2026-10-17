"""Bell Bloom 原始资格与阶段产物，拟合和三图复用既有 Z 标定算法。"""
from pathlib import Path
import json
import numpy as np
import yaml
from ..mx_z_field_calibration import analysis_steps as shared
from .models import BellBloomZFieldCalibrationParams
from .run_format import analysis_block_reason

plain = shared.plain


def load_params(run_dir):
    config = yaml.safe_load((Path(run_dir) / "experiment_config.yaml").read_text(encoding="utf-8"))
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    return BellBloomZFieldCalibrationParams.from_external(config["parameters"],
        schema_version=int(config.get("schema_version", 1))), config


def load_inputs(run_dir, config, params):
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    root = Path(run_dir).resolve()
    entries = shared._load_scan_index(root / "raw")
    if not entries:
        raise ValueError("没有已完成的 Z 频率扫描")
    for entry in entries:
        path = (root / entry["summary_file"]).resolve()
        if root / "raw" not in path.parents or not path.is_file():
            raise ValueError("扫描索引引用了无效的原始数据路径")
        if not np.isfinite([entry["z_bias_v"], entry["predicted_center_hz"]]).all():
            raise ValueError("Z 扫描索引含有非有限坐标")
        with np.load(path, allow_pickle=False) as data:
            response_key = "r_mean_v" if "r_mean_v" in data else "r_scalar_mean_v"
            if any(key not in data for key in ("frequency_hz", "r_std_v", response_key)):
                raise ValueError("原始频扫缺少频率、R 响应或标准差")
            frequency = np.asarray(data["frequency_hz"], dtype=float)
            response = np.asarray(data[response_key], dtype=float)
            deviation = np.asarray(data["r_std_v"], dtype=float)
            if frequency.ndim != 1 or response.shape != frequency.shape or deviation.shape != frequency.shape:
                raise ValueError("原始频扫数组形状不一致")
            if frequency.size < 5 or not np.isfinite(frequency).all() or np.any(np.diff(frequency) <= 0):
                raise ValueError("原始频扫需要至少五个递增的有限频点")
    return entries


def fit_curves(run_dir, params, config):
    curves = [shared.analyze_curve(run_dir, entry) for entry in load_inputs(run_dir, config, params)]
    (run_dir / "results/curve_fits.json").write_text(json.dumps(shared._builtin(curves), ensure_ascii=False), encoding="utf-8")
    return {"curve_count": len(curves), "accepted_curve_count": sum(curve["fit"]["success"] for curve in curves)}


def evaluate(run_dir, params, config):
    return shared.evaluate(run_dir, params, config)


def report(run_dir, params, config):
    curves = json.loads((run_dir / "results/curve_fits.json").read_text(encoding="utf-8"))
    payload = json.loads((run_dir / "results/calibration_evaluation.json").read_text(encoding="utf-8"))
    return shared.write_report(run_dir, curves, payload, frequency_label="Pump modulation frequency (kHz)")
