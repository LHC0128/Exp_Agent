"""采集、独立分析与历史共用身份/原始资格；不改写历史运行。"""
from pathlib import Path
import hashlib
import json
import numpy as np
import yaml
from .models import XYDirectAWDCCalibrationParams

EXPERIMENT_ID, DATA_TYPE = "xy-direct-aw-dc-calibration", "XY_DirectAW_DC_Calibration"
RESULT_FILES = ('analysis.json', 'analysis.yaml', 'direct_aw_dc_calibration_curve.png', 'direct_aw_dc_calibration_residual.png', 'direct_aw_dc_calibration_results.npz', 'direct_aw_frequency_response_examples.png', 'direct_aw_sign_symmetry.png')


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
    raw = safe_path(run_dir, "raw")
    return sorted(safe_path(run_dir, p.relative_to(Path(run_dir).resolve())) for p in raw.rglob("*")
        if p.is_file() and p.suffix in (".json", ".npz") and "recovery" not in p.relative_to(raw).parts)


def fingerprint(run_dir):
    config = dict(load_config(run_dir))
    for key in ("analysis_status", "analysis_error"):
        config.pop(key, None)
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()
    return dict(analysis_version=EXPERIMENT_ID + "-stages-v1", config=config_digest,
        raw={p.relative_to(Path(run_dir).resolve()).as_posix(): signature(p) for p in raw_files(run_dir)})


def validate_inputs(run_dir):
    load_config(run_dir)
    index = safe_path(run_dir, "raw/frequency_index.json")
    if index.is_file():
        records = json.loads(index.read_text(encoding="utf-8"))
    else:
        with np.load(safe_path(run_dir, "raw/frequency_index.npz"), allow_pickle=True) as data:
            records = data["records"].tolist()
    if not isinstance(records, list) or not records:
        raise ValueError("频点索引必须是非空记录列表")
    for record in records:
        if not isinstance(record, dict) or not all(k in record for k in ("subdir", "file", "z_freq_Hz", "xy_envelope_voltage_V")):
            raise ValueError("频点索引缺少必要字段")
        name = Path("raw") / record["subdir"] / record["file"]
        point = safe_path(run_dir, name)
        raw = safe_path(run_dir, "raw")
        if raw not in point.parents:
            raise ValueError("频点索引不能越出 raw")
        if not np.isfinite(float(record["xy_envelope_voltage_V"])) or not np.isfinite(float(record["z_freq_Hz"])) or float(record["z_freq_Hz"]) <= 0:
            raise ValueError("频率或包络索引无效")
        with np.load(point, allow_pickle=False) as data:
            if "demod3_r_vector_mean_V" not in data and not {"y_V", "actual_rate_Sa_s"}.issubset(data.files):
                raise ValueError("频点缺少硬件解调或时域原始数据")
            if "y_V" in data:
                y = np.asarray(data["y_V"], dtype=float)
                rate = float(data["actual_rate_Sa_s"])
                if y.ndim != 1 or y.size < 4 or not np.isfinite(y).all() or not np.isfinite(rate) or rate <= 0:
                    raise ValueError("时域数据或实际采样率无效")
    return dict(n_points=len(records))


def historical_parameters(config):
    values = config.get("parameters")
    if values is not None:
        if not isinstance(values, dict):
            raise ValueError("历史参数快照必须为映射")
        return XYDirectAWDCCalibrationParams.migrate_external(dict(values), config.get("schema_version", 1))
    # 只返回实际记录的条件，未保存的参数不拿当前默认值冒充。
    values = {"RUN_TAG": config["run_tag"]} if "run_tag" in config else {}
    for key, value in config.get("fixed_params", {}).items():
        values["FIXED_PARAMS." + key] = value
    for group in ("xy_direct_aw_scan", "z_frequency_scan", "pump_modulation"):
        for name, value in config.get(group, {}).items():
            if name in set(XYDirectAWDCCalibrationParams().to_external()):
                values[name] = value
    return XYDirectAWDCCalibrationParams.migrate_external(values, config.get("schema_version", 1))
