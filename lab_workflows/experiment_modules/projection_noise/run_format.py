"""采集、历史接口与离线分析共用的身份和原始数据格式资格。"""
EXPERIMENT_ID = "projection-noise"
DATA_TYPE = "Projection_noise"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "Pump_laser_power", "Probe_laser_power", "Temp_Switch", "temperature", "scope_waveform",
    # 只为保护保留通道而检查占用，不连接、不设置载波或门控。
    "Pump_modulation", "Time_sequence",
)


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    identities = (("experiment_id", EXPERIMENT_ID), ("experiment_type", DATA_TYPE), ("data_type", DATA_TYPE))
    if any(config.get(key) not in (None, expected) for key, expected in identities):
        return "运行目录属于其他实验"
    if not any(config.get(key) == expected for key, expected in identities):
        return "无法识别为投影噪声运行"
    if config.get("acquisition_backend") not in (None, "sds_pd_raw", "legacy_hf2_demod_xy"):
        return "无法识别投影噪声采集后端"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 既有 SDS 参数版本 2–6 与旧 HF2 双文件分支；仍须检查实际文件。
    if version is None and (type(config.get("schema_version", 1)) is not int
            or config.get("schema_version", 1) not in range(1, 7)):
        return "无法识别未标记原始格式的历史版本"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先结束会话"
    return ""
