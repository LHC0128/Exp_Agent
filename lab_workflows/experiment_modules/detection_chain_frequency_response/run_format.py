"""探测链路频响的原始格式资格；兼容已验证的无版本四键历史数据。"""

from pathlib import Path

import numpy as np

EXPERIMENT_ID = "detection-chain-frequency-response"
DATA_TYPE = "Detection_Chain_Frequency_Response"
RAW_FORMAT_VERSION = 1


def validate_run_identity(config):
    """仅核对身份和版本，供任何分析写入前复用，不读取原始波形。"""
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("运行目录的 experiment_id 不属于探测链路频率响应实验")
    identities = [config[key] for key in ("data_type", "experiment_type") if key in config]
    if not identities or any(value != DATA_TYPE for value in identities):
        raise ValueError("运行目录的 data_type / experiment_type 与探测链路频率响应不一致")
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        raise ValueError("不支持的 raw_format_version")


def load_frequency_response(run_dir, config):
    """先核对实验身份和数据结构，再返回分析使用的频率与谱峰。"""
    validate_run_identity(config)
    # 已有运行未标记原始格式，资格仍由身份及实际四键结构共同确认。
    names = ("am_frequencies_hz", "peak_amplitudes", "peak_frequencies_hz", "actual_rate")
    with np.load(Path(run_dir) / "raw/frequency_response.npz", allow_pickle=False) as data:
        if missing := set(names) - set(data.files):
            raise ValueError("频率响应原始数据缺少字段：" + "、".join(sorted(missing)))
        freqs, peaks, peak_freqs, rate = (np.asarray(data[name], dtype=float) for name in names)
    if freqs.ndim != 1 or not freqs.size or peaks.shape != freqs.shape or peak_freqs.shape != freqs.shape:
        raise ValueError("频率响应数组必须为非空、等长的一维数据")
    if not np.all(np.isfinite(freqs)) or np.any(freqs <= 0):
        raise ValueError("AM 频率必须为有限正数")
    if rate.shape != () or not np.isfinite(rate) or rate <= 0:
        raise ValueError("频率响应实际采样率必须为有限正标量")
    return freqs, peaks
