"""示波器采集的身份与原始格式资格；参数版本不参与数据格式判断。"""

EXPERIMENT_ID = "scope-capture"
DATA_TYPE = "Scope_Capture"
RAW_FORMAT_VERSION = 1


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须是映射"
    identity = config.get("experiment_id")
    if identity is not None and identity != EXPERIMENT_ID:
        return "运行目录不属于示波器采集实验"
    if identity is None:
        # 已发布的旧采集器只写入 experiment_type 与参数快照。
        if config.get("experiment_type") != DATA_TYPE:
            return "缺少已验证的历史示波器采集标识"
    if config.get("experiment_type", DATA_TYPE) != DATA_TYPE:
        return "运行类型不属于示波器采集实验"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持的示波器原始数据格式"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") == "running":
        return "本轮采集仍在写入数据，请等待采集结束"
    return ""
