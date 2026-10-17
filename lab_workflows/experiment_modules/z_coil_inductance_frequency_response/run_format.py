"""采集与分析共用的实验身份和原始数据资格。"""

EXPERIMENT_ID = "z-coil-inductance-frequency-response"
DATA_TYPE = "Z_Coil_Inductance_Frequency_Response"
RAW_FORMAT_VERSION = 1


def analysis_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Z 线圈电感效应频率响应实验"
    version = config.get("raw_format_version")
    if version is None:
        # 旧工作流固定写入此身份、扫描模式和正弦驱动协议。
        drive = config.get("drive")
        if (config.get("data_type") != DATA_TYPE or config.get("scan_mode") != "point_by_point"
                or not isinstance(drive, dict) or drive.get("waveform") != "SINusoid"):
            return "缺少已验证的历史扫频格式标识"
    elif type(version) is not int or version != RAW_FORMAT_VERSION:
        return "不支持的扫频原始数据格式"
    if config.get("completion_status") == "running":
        return "本轮扫频仍在写入数据，请等待采集结束"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return ""
