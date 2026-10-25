"""采集、独立分析与历史共用身份/原始资格；不改写历史运行。"""
from pathlib import Path
import hashlib
import json
import numpy as np
import yaml
from .models import RFDirectAWFrequencyParams

EXPERIMENT_ID, DATA_TYPE = "rf-sensitivity-direct-aw-frequency", "RF_Field_Sensitivity_AW_FreqSweep_DirectAW"
RESULT_FILES = ('analysis.json', 'analysis.yaml', 'freq_phase_heatmap.png', 'freq_response_amplitude.png', 'freq_response_amplitude_robust.png', 'freq_response_phase.png', 'phase_scan_cv.png')


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    identity = config.get("experiment_id")
    kind = config.get("experiment_type", config.get("data_type"))
    if identity not in (None, EXPERIMENT_ID) or kind not in (None, DATA_TYPE) or identity is None and kind is None:
        return "运行目录与实验身份不匹配"
    if config.get("raw_format_version") is not None:
        return "此实验未发布带版本标记的新原始格式"
    return None


def analysis_block_reason(config):
    return format_block_reason(config) or ("采集仍在写入，请先安全结束会话" if config.get("completion_status") == "running" else None)


def safe_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if root not in path.parents:
        raise ValueError("输入或产物路径不能越出运行目录")
    return path


def load_config(run_dir):
    path = safe_path(run_dir, "experiment_config.yaml")
    # 早期只有 raw 的运行，仍必须通过下面的精确字段与形状检查。
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {"experiment_id": EXPERIMENT_ID}
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    return config


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def raw_files(run_dir):
    path = safe_path(run_dir, "raw/freq_phase_scan.npz")
    return [path] if path.is_file() else []


def fingerprint(run_dir):
    config = dict(load_config(run_dir))
    for key in ("analysis_status", "analysis_error"):
        config.pop(key, None)
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()
    return dict(analysis_version=EXPERIMENT_ID + "-stages-v1", config=config_digest,
        raw={p.relative_to(Path(run_dir).resolve()).as_posix(): signature(p) for p in raw_files(run_dir)})


def validate_inputs(run_dir):
    load_config(run_dir)
    with np.load(safe_path(run_dir, "raw/freq_phase_scan.npz"), allow_pickle=False) as data:
        f, phase, r = (np.asarray(data[k], dtype=float) for k in ("freq_Hz", "phase_deg", "r_matrix_V"))
        if f.ndim != 1 or phase.ndim != 1 or not f.size or not phase.size or r.shape != (len(f), len(phase)):
            raise ValueError("频率、相位与 R 矩阵形状不一致")
        if not np.isfinite(f).all() or not np.isfinite(phase).all() or not np.all(np.diff(f) > 0):
            raise ValueError("频率与相位索引无效")
        for key in ("x_matrix_V", "y_matrix_V"):
            if key in data and data[key].shape != r.shape:
                raise ValueError("X/Y 矩阵与 R 矩阵形状不一致")
        if not np.isfinite(r).any():
            raise ValueError("r_matrix_V 中没有任何有效 R 数据")
    return dict(n_frequencies=len(f), n_phases=len(phase))


def historical_parameters(config):
    values = config.get("parameters")
    if values is not None:
        if not isinstance(values, dict):
            raise ValueError("历史参数快照必须为映射")
        return RFDirectAWFrequencyParams.migrate_external(dict(values), config.get("schema_version", 1))
    # 只返回实际记录的条件，未保存的参数不拿当前默认值冒充。
    values = {"RUN_TAG": config["run_tag"]} if "run_tag" in config else {}
    for key, value in config.get("fixed_params", {}).items():
        values["FIXED_PARAMS." + key] = value
    for group in ("freq_sweep", "phase_sweep", "xy_direct_aw", "xy_trigger", "pump_modulation"):
        for name, value in config.get(group, {}).items():
            if name in set(RFDirectAWFrequencyParams().to_external()):
                values[name] = value
    return RFDirectAWFrequencyParams.migrate_external(values, config.get("schema_version", 1))
