"""采集、离线分析与历史共用的实验身份及原始格式资格。"""
EXPERIMENT_ID = "noise-spectrum-xy-demod3-r"
DATA_TYPE = "Noise_Spectrum_XY_Demod3_R"
RAW_FORMAT_VERSION = 1
RAW_FILE = "demod3_r_mean_matrix.npz"
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", "X_magnetic_field", "Y_magnetic_field",
    "X_magnetic_field_AM", "Y_magnetic_field_AM", "Z_magnetic_field", "Time_sequence_2",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r",
)


def format_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    for key, expected in (("experiment_id", EXPERIMENT_ID), ("experiment_type", DATA_TYPE), ("data_type", DATA_TYPE)):
        if config.get(key) not in (None, expected):
            return "运行目录属于其他实验"
    if not any(config.get(key) == value for key, value in (
            ("experiment_id", EXPERIMENT_ID), ("experiment_type", DATA_TYPE), ("data_type", DATA_TYPE))):
        return "无法识别为 Demod3 R 历史数据"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    # 原 schema 1/2 均使用相同四键均值矩阵；仍须检查实际文件。
    if version is None and (type(config.get("schema_version")) is not int or config["schema_version"] not in (1, 2)):
        return "无法识别未标记原始格式的参数版本"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集仍在执行，请先结束会话"
    return ""
