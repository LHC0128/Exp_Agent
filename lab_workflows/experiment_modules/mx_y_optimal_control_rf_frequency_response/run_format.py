"""采集、历史列表和离线分析共用的原始格式资格。"""
EXPERIMENT_ID = "mx-y-optimal-control-rf-frequency-response"
DATA_TYPE = "Mx_Y_Optimal_Control_RF_Frequency_Response"
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
        return "无法识别为 Mx Y 最优控制频率响应历史数据"
    version = config.get("raw_format_version")
    if "raw_format_version" in config:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持该原始数据格式版本"
    elif type(config.get("schema_version")) is not int or config["schema_version"] not in (2, 3):
        return "无原始格式标记的历史仅支持已验证的参数版本 2、3"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "本轮采集尚未安全收尾，不能分析"
    return ""
