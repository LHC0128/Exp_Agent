"""采集、独立分析与历史接口共用的三维网格资格。"""
EXPERIMENT_ID = "mx-z-optimal-control-dg4000-bias-xyz-balance"
DATA_TYPE = "Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance"
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
        return "无法识别为 XYZ 平衡场数据"
    if "raw_format_version" in config and (
            type(config["raw_format_version"]) is not int or config["raw_format_version"] != RAW_FORMAT_VERSION):
        return "不支持该原始数据格式版本"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if "raw_format_version" not in config and (
            type(config.get("schema_version")) is not int or config.get("schema_version") not in (1, 2, 3)):
        return "无原始格式标记的历史仅支持已验证的 schema v1/v2/v3 三维网格"
    if config.get("scan_mode") != "nested_scan":
        return "运行目录不是 XYZ 平衡场三维网格"
    if config.get("completion_status") != "completed":
        return "本轮尚未完整安全封存，不能分析；已读原始数据仍保留"
    for key, flag, label in (("safety_shutdown", "completed", "安全恢复"),
                             ("xyz_restore", "success", "X/Y 原态恢复"),
                             ("z_channel_restore", "success", "Z 原态恢复"),
                             ("trigger_channel_restore", "success", "触发原态恢复")):
        if key not in config:
            continue
        record = config[key]
        if not isinstance(record, dict) or (flag in record and type(record[flag]) is not bool):
            return f"{label}记录无效，不能分析"
        if record.get(flag) is False:
            return f"{label}未成功，不能分析"
    return ""
