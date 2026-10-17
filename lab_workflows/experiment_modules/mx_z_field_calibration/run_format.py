"""本实验采集、历史与离线分析共用的格式资格。"""
EXPERIMENT_ID = "mx-z-field-calibration"
DATA_TYPE = "Mx_Z_Field_Calibration"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "rf_coil",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "lockin_r", "temperature",
)


def analysis_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID):
        return "运行目录属于其他实验"
    version = config.get("raw_format_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 历史采集明确写过本实验 ID；缺少身份的任意目录不猜测为旧格式。
    if config.get("experiment_id") != EXPERIMENT_ID and config.get("data_type") != DATA_TYPE:
        return "无法识别为 Mx 高主场 Z 频率标定 历史数据"
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
