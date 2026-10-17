"""波形验证的身份与原始格式资格，参数版本不参与数据格式判断。"""

EXPERIMENT_ID = "z-aw-waveform-scope-check"
DATA_TYPE = "Z_AW_Waveform_Scope_Check"
RAW_FORMAT_VERSION = 1


def analysis_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Z 任意波线圈波形验证实验"
    version = config.get("raw_format_version")
    if version is None:
        # 已发布的旧采集器明确写入身份、测量模式和 data_type。
        if config.get("data_type") != DATA_TYPE or config.get("measurement_mode") != "coil_voltage":
            return "缺少已验证的历史波形格式标识"
    elif type(version) is not int or version != RAW_FORMAT_VERSION:
        return "不支持的波形原始数据格式"
    if config.get("completion_status") == "running":
        return "本轮采集仍在写入数据，请等待采集结束"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return ""
