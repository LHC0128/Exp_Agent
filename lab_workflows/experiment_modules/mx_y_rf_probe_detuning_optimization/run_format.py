"""采集、分析与历史共用的身份和原始格式资格。"""
EXPERIMENT_ID = "mx-y-rf-probe-detuning-optimization"
DATA_TYPE = "Mx_Y_RF_Probe_Detuning_Optimization"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "rf_coil",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "lockin_r", "probe_laser",
)


def analysis_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID):
        return "运行目录属于其他实验"
    if config.get("experiment_id") != EXPERIMENT_ID and config.get("data_type") != DATA_TYPE:
        return "无法识别为 Probe/PZT 优化历史数据"
    if config.get("raw_format_version") is None:
        if config.get("schema_version") not in (1, 2):
            return "不支持该无格式标记的历史参数版本"
    elif type(config["raw_format_version"]) is not int or config["raw_format_version"] != RAW_FORMAT_VERSION:
        return "不支持该原始数据格式版本"
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
