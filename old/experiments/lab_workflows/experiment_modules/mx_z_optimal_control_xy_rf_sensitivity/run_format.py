"""采集、分析与历史接口共用的 XY 网格资格。"""
EXPERIMENT_ID = "mx-z-optimal-control-xy-rf-sensitivity"
DATA_TYPE = "Mx_Z_Optimal_Control_XY_RF_Sensitivity"
RAW_FORMAT_VERSION = 1


def format_block_reason(config):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID):
        return "运行目录属于其他实验"
    data_type = config.get("data_type", config.get("experiment_type"))
    if data_type not in (None, DATA_TYPE):
        return "运行目录实验类型不匹配"
    if config.get("experiment_id") != EXPERIMENT_ID and data_type != DATA_TYPE:
        return "无法识别为 XY 补偿偏置 RF 灵敏度数据"
    if "raw_format_version" in config and (
            type(config["raw_format_version"]) is not int or config["raw_format_version"] != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if "raw_format_version" not in config and (
            type(config.get("schema_version")) is not int or config["schema_version"] not in (1, 2)):
        return "无原始格式标记的历史仅支持已验证的 schema v1/v2 网格"
    if config.get("measurement_mode") != "xy_bias_grid_rf_sensitivity":
        return "运行目录不是 XY 补偿偏置网格"
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮尚未安全封存，不能分析"
    if config.get("safety_shutdown", {}).get("completed") is False:
        return "安全恢复未成功，不能分析"
    return ""
