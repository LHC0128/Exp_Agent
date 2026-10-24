"""新 raw v1 与已核实的旧手工/GUI 配置共用身份和参数迁移。"""
from .models import PhotonShotNoiseParams

EXPERIMENT_ID = "photon-shot-noise"
DATA_TYPE = "Photon_shot_noise"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = ("main_magnetic_field", "Pump_laser_power", "Probe_laser_power",
                        "Temp_Switch", "temperature", "lockin_r")
LEGACY_SECTIONS = {
    "scan_params": {"start": "PROBE_POWER_START", "stop": "PROBE_POWER_STOP",
                    "points": "PROBE_POWER_POINTS", "settle_time": "SYSTEM_SETTLE_S"},
    "acquisition_params": {"duration": "ACQ_DURATION", "repeats": "ACQ_REPEATS"},
    "fixed_params": {key: "FIXED_PARAMS." + key for key in
                    ("temperature", "SHOT_NOISE_FIELD_mA", "Pump_laser_power", "Temp_Switch")},
    "hf2_demod": {"demod_idx": "HF2_DEMOD_IDX", "osc_freq": "HF2_OSC_FREQ",
                  "signal_range": "HF2_SIGNAL_RANGE", "order": "HF2_DEMOD_ORDER",
                  "tc": "HF2_DEMOD_TC", "rate": "HF2_DEMOD_RATE"},
    "psd_params": {"nperseg": "PSD_NPERSEG"},
}


def parameters(config):
    if isinstance(config.get("parameters"), dict):
        return PhotonShotNoiseParams.from_external(config["parameters"],
            schema_version=config.get("schema_version", 1))
    values = {}
    for section, keys in LEGACY_SECTIONS.items():
        for old, external in keys.items():
            values[external] = config[section][old]
    return PhotonShotNoiseParams.from_external(values)


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    identities = (("experiment_id", EXPERIMENT_ID), ("experiment_type", DATA_TYPE), ("data_type", DATA_TYPE))
    if any(config.get(key) not in (None, expected) for key, expected in identities):
        return "运行目录属于其他实验"
    if not any(config.get(key) == expected for key, expected in identities):
        return "无法识别光子散粒噪声运行身份"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    if version is None and "parameters" in config and (
            type(config.get("schema_version")) is not int or config["schema_version"] not in (1, 2)):
        return "无法识别未标记 raw 格式的参数版本"
    try:
        parameters(config)
    except (KeyError, TypeError, ValueError) as exc:
        return f"历史参数快照无效：{exc}"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先结束本轮"
    return ""
