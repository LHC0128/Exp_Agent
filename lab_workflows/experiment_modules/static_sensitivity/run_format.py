"""静磁场原始格式资格，独立于参数模型版本。"""

EXPERIMENT_ID = "static-sensitivity"
RAW_FORMAT_VERSION = 3


def format_block_reason(config):
    if not isinstance(config, dict) or config.get("acquisition_method") != "dc_step":
        return "仅支持连续逐点色散原始格式 3；旧格式目录仅可查看已有结果"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID):
        return "运行目录与静磁场实验不匹配"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的静磁场原始格式"
    elif config.get("schema_version") not in (3, 4):
        return "缺少已验证的原始格式标记；旧格式目录仅可查看已有结果"
    return None
