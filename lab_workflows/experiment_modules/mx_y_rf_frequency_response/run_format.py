"""本实验采集、历史与离线分析共用的格式资格。"""

EXPERIMENT_ID = "mx-y-rf-frequency-response"
DATA_TYPE = "Mx_Y_RF_Frequency_Response"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "rf_coil",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "lockin_r", "temperature",
)


def format_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID):
        return "运行目录属于其他实验"
    if any(config.get(key, DATA_TYPE) != DATA_TYPE for key in ("data_type", "experiment_type")):
        return "运行数据类型属于其他实验"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 历史采集明确写过本实验身份；缺少身份的任意目录不猜测为旧格式。
    if config.get("experiment_id") != EXPERIMENT_ID and config.get(
        "data_type", config.get("experiment_type")
    ) != DATA_TYPE:
        return "无法识别为 Mx Y RF 频率响应历史数据"
    if version is None and (type(config.get("schema_version", 1)) is not int
            or config.get("schema_version", 1) not in (1, 2)):
        return "缺少已验证的历史原始数据格式标记"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
