"""采集、离线分析及历史共用的身份与原始格式资格。"""
EXPERIMENT_ID = "mx-xy-residual-field-calibration"
DATA_TYPE = "Mx_XY_Residual_Field_Calibration"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "scope_waveform", "temperature",
)


def format_block_reason(config):
    """历史文件浏览只检查身份/格式；运行中仍允许读取已有摘要与产物。"""
    return analysis_block_reason(config | {"completion_status": None} if isinstance(config, dict) else config)


def analysis_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("data_type") not in (None, DATA_TYPE):
        return "运行目录属于其他实验"
    if config.get("experiment_id") != EXPERIMENT_ID and config.get("data_type") != DATA_TYPE:
        return "无法识别为 XY 剩磁二维校准历史数据"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # v1/v2/v3 的参数变更未改变 grid_X/Y/R 文件结构，仍须逐文件检查。
    if version is None and config.get("schema_version") not in (1, 2, 3):
        return "无法识别未标记原始格式的参数版本"
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
