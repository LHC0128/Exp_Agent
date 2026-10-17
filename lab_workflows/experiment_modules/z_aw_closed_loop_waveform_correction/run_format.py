"""闭环采集、历史接口与分析共用的身份和格式资格。"""

EXPERIMENT_ID = "z-aw-closed-loop-waveform-correction"
DATA_TYPE = "Z_AW_Closed_Loop_Waveform_Correction"
RAW_FORMAT_VERSION = 1
METHODS = {"time_domain", "response_filtered_feedback", "harmonic_jacobian"}


def format_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Z 任意波实际电流闭环实验"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的闭环原始数据格式"
        if config.get("data_type") != DATA_TYPE or config.get("correction_method") not in METHODS:
            return "闭环原始数据缺少明确的方法和类型标识"
        return ""
    # 仅兼容实际历史已验证的结构，不按当前默认参数猜测算法。
    schema, method = config.get("schema_version"), config.get("correction_method")
    if config.get("data_type") not in (None, DATA_TYPE):
        return "历史数据类型与闭环实验不匹配"
    if ((type(schema) is int and schema in (2, 3) and method is None) or
            (schema == 10 and method == "static_current_feedback") or
            (schema == 11 and method == "response_filtered_feedback") or
            (schema in (12, 13) and method in METHODS)):
        return ""
    return "未验证的历史闭环格式；需要明确运行版本和方法"


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") == "running":
        return "本轮闭环仍在写入数据，请等待采集结束"
    return ""
