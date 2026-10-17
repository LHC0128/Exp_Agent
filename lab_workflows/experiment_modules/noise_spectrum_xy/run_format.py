"""XY 正弦原始数据资格；参数版本不代替原始格式版本。"""

import math

import numpy as np


EXPERIMENT_ID = "noise-spectrum-xy"
DATA_TYPE = "Noise_Spectrum_XY_Ctrl"
RAW_FORMAT_VERSION = 1


def require_supported_format(config):
    """兼容已核实的早期 AMP 轴和 schema 1～4，拒绝其他实验和未知格式。"""
    if not isinstance(config, dict):
        raise ValueError("实验配置必须为映射")
    if (config.get("experiment_id") not in (None, EXPERIMENT_ID)
            or config.get("experiment_type") not in (None, DATA_TYPE)
            or not (config.get("experiment_id") == EXPERIMENT_ID or config.get("experiment_type") == DATA_TYPE)):
        raise ValueError("运行目录不是 XY 正弦控制噪声谱实验")
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            raise ValueError("不支持的 XY 正弦原始数据格式")
    else:
        schema = config.get("schema_version")
        if schema is None:
            scan = config.get("scan_params", {})
            if config.get("experiment_type") != DATA_TYPE or not isinstance(scan, dict) or not {
                    "XY_AMP_START_V", "XY_AMP_STOP_V", "XY_AMP_POINTS"}.issubset(scan):
                raise ValueError("缺少可识别的历史原始数据格式")
        elif type(schema) is not int or schema not in (1, 2, 3, 4):
            raise ValueError("缺少可识别的历史原始数据格式")


def voltage_axis(config):
    """复用真实扫描电压轴；无轴数组的早期运行沿用其已保存的 AMP 参数。"""
    try:
        scan = config["scan_params"]
        if "xy_envelope_voltage_V" in scan:
            axis = np.asarray(scan["xy_envelope_voltage_V"], dtype=float)
        else:
            count = scan["XY_AMP_POINTS"]
            if type(count) is not int or count < 2:
                raise ValueError("旧 AMP 扫描点数必须为至少 2 的整数")
            axis = np.linspace(scan["XY_AMP_START_V"], scan["XY_AMP_STOP_V"], count)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("缺少有效的控制电压轴") from exc
    if axis.ndim != 1 or axis.size < 2 or not np.isfinite(axis).all() or np.any(np.diff(axis) <= 0):
        raise ValueError("控制电压轴必须有限且严格递增")
    return axis


def validate_analysis_inputs(config):
    """在归档旧结果前验证配置，不从请求采样率推测实际值。"""
    voltage_axis(config)
    daq = config.get("hf2_daq", {})
    if not isinstance(daq, dict):
        raise ValueError("缺少有效的采集配置")
    for key, message in (("actual_rate_Sa_s", "缺少有效的硬件实际采样率"),
                         ("DAQ_duration_s", "缺少有效的采集时长")):
        value = daq.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(message)
    if int(config.get("schema_version", 1)) < 3:
        count = daq.get("nperseg")
        if type(count) is not int or count < 2:
            raise ValueError("旧数据缺少有效的 Welch 段长")


def analysis_block_reason(config):
    """历史列表和旧重分析复用正式分析的格式与配置资格。"""
    try:
        require_supported_format(config)
        validate_analysis_inputs(config)
    except (ValueError, TypeError, OverflowError) as exc:
        return str(exc)
    if config.get("completion_status") == "running":
        return "本轮扫描仍在写入数据，请等待扫描结束"
    return None
