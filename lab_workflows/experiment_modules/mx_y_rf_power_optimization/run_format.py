"""光功率网格的实验身份、原始格式与历史兼容资格。"""
EXPERIMENT_ID = "mx-y-rf-power-optimization"
DATA_TYPE = "Mx_Y_RF_Power_Optimization"
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
    if config.get("data_type") not in (None, DATA_TYPE):
        return "运行数据类型不匹配"
    if config.get("experiment_id") != EXPERIMENT_ID and config.get("data_type") != DATA_TYPE:
        return "无法识别为光功率网格历史数据"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 无格式标记的旧运行还必须通过原网格清单与逐点文件结构检查。
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
