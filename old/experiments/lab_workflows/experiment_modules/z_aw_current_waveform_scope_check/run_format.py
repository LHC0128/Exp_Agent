"""实际电流波形的身份、原始格式和离线分析资格。"""

EXPERIMENT_ID = "z-aw-current-waveform-scope-check"
DATA_TYPE = "Z_AW_Current_Waveform_Scope_Check"
RAW_FORMAT_VERSION = 1


def format_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Z 任意波实际电流波形验证实验"
    if config.get("data_type") != DATA_TYPE or config.get("measurement_mode") != "current":
        return "缺少已验证的电流波形格式标识"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持的电流波形原始数据格式"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") == "running":
        return "本轮采集仍在写入数据，请等待采集结束"
    return ""
