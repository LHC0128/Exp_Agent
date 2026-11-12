"""采集、离线分析和历史接口共用的身份与原始格式资格。"""
EXPERIMENT_ID = "mx-main-field-scope-noise-spectrum"
DATA_TYPE = "Mx_Main_Field_Scope_Noise_Spectrum"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "scope_waveform", "temperature",
)


def format_block_reason(config):
    """身份与原始格式资格；历史列表据此筛选，不看采集是否已收尾。"""
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("data_type") not in (None, DATA_TYPE):
        return "运行目录属于其他实验"
    if config.get("experiment_id") != EXPERIMENT_ID and config.get("data_type") != DATA_TYPE:
        return "无法识别为主场示波器噪声谱历史数据"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 旧采集有明确身份及同形 waveform_I*.npz，参数版本与 raw 版本独立。
    if version is None and config.get("schema_version") not in range(1, 9):
        return "无法识别未标记原始格式的参数版本"
    return ""


def analysis_block_reason(config):
    """离线分析资格：格式合格且本轮采集已安全收尾。"""
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先完成安全收尾"
    return ""
