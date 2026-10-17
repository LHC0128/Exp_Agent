"""电流频响采集、分析和历史共用的原始格式资格。"""
EXPERIMENT_ID = "z-coil-current-frequency-response"
DATA_TYPE = "Z_Coil_Current_Frequency_Response"
RAW_FORMAT_VERSION = 1
OUTPUT_PROTOCOL = "coherent_swept_sine_current_response"
COMMAND_VOLTAGE_REFERENCE = "50_ohm"


def analysis_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录不属于 Z 线圈实际电流频率响应实验"
    version = config.get("raw_format_version")
    if version is None:
        drive = config.get("drive")
        if (config.get("data_type") != DATA_TYPE or config.get("scan_mode") != "point_by_point"
                or not isinstance(drive, dict) or drive.get("waveform") != "SINusoid"
                or drive.get("output_protocol") != OUTPUT_PROTOCOL):
            return "缺少已验证的历史对数扫频格式标识；旧 AW 网格标定需要重新采集"
    elif type(version) is not int or version != RAW_FORMAT_VERSION:
        return "不支持的电流扫频原始数据格式"
    if config.get("completion_status") == "running":
        return "本轮扫频仍在写入数据，请等待采集结束"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return ""
