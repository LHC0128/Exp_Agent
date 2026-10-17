"""采集、历史与分析共用的 Bell Bloom Z 原始格式资格。"""
EXPERIMENT_ID = "bell-bloom-z-field-calibration"
DATA_TYPE = "Bell_Bloom_Z_Field_Calibration"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "Time_sequence_2",
    "X_magnetic_field", "Y_magnetic_field", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r",
)
DG_KEYS = tuple(key for key in REQUIRED_MAPPING_KEYS if key not in {"main_magnetic_field", "temperature", "lockin_r"})
OFF_KEYS = ("Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field", "Time_sequence_2")


def analysis_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不是 Bell Bloom Z 磁场频率标定"
    if config.get("data_type") not in (None, DATA_TYPE):
        return "运行目录的数据类型与本实验不匹配"
    version = config.get("raw_format_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 旧采集已显式保存本实验 ID 和相同 NPZ 键，缺版本号沿用已验证的旧格式。
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
