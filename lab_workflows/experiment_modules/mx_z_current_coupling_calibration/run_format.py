"""电流耦合采集与离线分析资格；参数版本独立于原始数据格式。"""

EXPERIMENT_ID = "mx-z-current-coupling-calibration"
DATA_TYPE = "Mx_Z_Current_Coupling_Calibration"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "rf_coil",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r", "scope_waveform",
)


def analysis_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Mx Z 实际电流耦合标定实验"
    if config.get("data_type") not in (None, DATA_TYPE):
        return "运行目录的数据类型不属于实际电流耦合标定"
    version = config.get("raw_format_version")
    if version is None:
        geometry = config.get("geometry", {})
        if (config.get("data_type") != DATA_TYPE or not isinstance(geometry, dict)
                or any(geometry.get(key) != value for key, value in
                       (("main_field", "Z"), ("pump", "Z"), ("probe", "X"), ("rf_field", "Y")))):
            return "缺少已验证的历史实际电流标定格式标识"
    elif type(version) is not int or version != RAW_FORMAT_VERSION:
        return "不支持该实际电流标定原始数据格式版本"
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
