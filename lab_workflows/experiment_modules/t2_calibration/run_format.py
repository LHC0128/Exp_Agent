"""采集、独立分析与历史共用身份/原始资格；不改写历史运行。"""
from pathlib import Path
import hashlib
import json
import numpy as np
import yaml
from .models import T2CalibrationParams

EXPERIMENT_ID, DATA_TYPE = "t2-calibration", "T2_Calibration"
RESULT_FILES = ("T2_fid_analysis.png", "T2_results.yaml", "T2_vs_probe_power.png", "inv_T2_vs_probe_power.png", "T2_all_fid_curves.png", "T2_all_fid_curves_0_10ms.png", "analysis.yaml")


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
    paths = [safe_path(run_dir, "raw/" + name) for name in ("fid_waveforms.npz", "power_scan_waveforms.npz", "fid_p00.npz")]
    return [p for p in paths if p.is_file()]


def fingerprint(run_dir):
    config = dict(load_config(run_dir))
    for key in ("analysis_status", "analysis_error"):
        config.pop(key, None)
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()
    return dict(analysis_version=EXPERIMENT_ID + "-stages-v1", config=config_digest,
        raw={p.relative_to(Path(run_dir).resolve()).as_posix(): signature(p) for p in raw_files(run_dir)})


def validate_inputs(run_dir):
    load_config(run_dir)
    raw = safe_path(run_dir, "raw")
    scan = safe_path(run_dir, "raw/power_scan_waveforms.npz")
    if scan.is_file():
        with np.load(scan, allow_pickle=False) as data:
            t, stack, powers = (np.asarray(data[k], dtype=float) for k in ("t_axis", "avg_stack", "probe_power"))
        if powers.ndim != 1 or not powers.size or not np.isfinite(powers).all() or stack.shape != (len(powers), len(t)):
            raise ValueError("功率扫描数组形状或索引无效")
        for wave in stack:
            _wave(t, wave)
        return dict(mode="scan", n_points=len(powers))
    with np.load(safe_path(run_dir, "raw/fid_waveforms.npz"), allow_pickle=False) as data:
        t, average, shots = (np.asarray(data[k], dtype=float) for k in ("time", "avg_waveform", "waveforms"))
        frequency = float(data["rf_gate_freq"])
        if not np.isfinite(frequency) or frequency <= 0 or shots.ndim != 2 or shots.shape[1] != len(t):
            raise ValueError("单点波形或门控频率无效")
        _wave(t, average)
        for wave in shots:
            _wave(t, wave)
    return dict(mode="single", n_points=1)


def historical_parameters(config):
    values = config.get("parameters")
    if values is not None:
        if not isinstance(values, dict):
            raise ValueError("历史参数快照必须为映射")
        return T2CalibrationParams.migrate_external(dict(values), config.get("schema_version", 1))
    # 只返回实际记录的条件，未保存的参数不拿当前默认值冒充。
    values = {"RUN_TAG": config["run_tag"]} if "run_tag" in config else {}
    for key, value in config.get("fixed_params", {}).items():
        values["FIXED_PARAMS." + key] = value
    aliases = {"pump_power_V": "PUMP_POWER", "probe_power_V": "PROBE_POWER", "main_field_mA": "MAIN_FIELD_mA", "temperature_C": "TEC_TEMPERATURE", "rf_gate_freq_Hz": "RF_GATE_FREQ", "rf_gate_duty_pct": "RF_GATE_DUTY", "burst_ncycles": "BURST_NCYCLES", "burst_period_s": "BURST_PERIOD", "aom_carrier_amplitude_V": "AOM_CARRIER_AMPLITUDE"}
    values.update({external: config[name] for name, external in aliases.items() if name in config})
    for name, external in {"sample_rate_Hz": "SCOPE_SAMPLE_RATE", "duration_s": "SCOPE_DURATION", "acq_repeats": "ACQ_REPEATS"}.items():
        if name in config.get("scope", {}):
            values[external] = config["scope"][name]
    return T2CalibrationParams.migrate_external(values, config.get("schema_version", 1))


def _wave(t, wave):
    if t.ndim != 1 or len(t) < 8 or wave.shape != t.shape or not np.isfinite(t).all() or not np.isfinite(wave).all() or not np.all(np.diff(t) > 0):
        raise ValueError("FID 时间轴必须严格递增，且波形必须有限并与时间轴等长")
