import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from sds_acquisition.config import AcquisitionResult
from sds_acquisition.waveform import (
    WaveformPreamble,
    convert_raw_to_voltage,
    build_time_axis,
    extract_waveform_data,
)

logger = logging.getLogger(__name__)


def _timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _resolve_path(directory: str, suffix: str) -> str:
    if os.path.isdir(directory) or not directory.endswith(suffix):
        os.makedirs(directory, exist_ok=True)
        ts = _timestamp_str()
        return os.path.join(directory, f"acquisition_{ts}{suffix}")
    return directory


# ------------------------------------------------------------------
# 存储格式与空间对比
#
# 每通道每采样点的字节数：
#   CSV:     ~25 bytes (文本, 依赖精度)
#   MAT:      18 B   (float64 time + float64 voltage + int16 raw)
#   NPZ旧:    18 B   (同上, 但经 DEFLATE 压缩)
#   HDF5:     2-3 B  (仅存 raw int16 + gzip 压缩)
#   紧凑NPZ:  2-3 B  (仅存 raw int16 + DEFLATE 压缩)
#   原始BIN:  2 B    (仅 raw int16, 无元数据)
#
# 紧凑策略: time/voltage 可从 raw + preamble 重建, 无需存储。
# ------------------------------------------------------------------


# ==================================================================
# 紧凑 NPZ (推荐: 无额外依赖, 体积小)
# ==================================================================

def save_to_npz(filepath: str,
                results: List[AcquisitionResult],
                config_dict: Optional[Dict] = None,
                save_mode: str = "raw") -> str:
    """
    多通道数据保存为 NPZ 压缩包。

    Parameters
    ----------
    save_mode : str
        "raw" (默认) — 仅存 raw int16 + 元数据, 体积最小 (~2 B/点)。
        "voltage_time" — 存 voltage + time, 加载更快。
        "voltage_only" — 仅存 voltage。
        "all" — 存 raw + voltage + time, 体积最大。
    """
    _VALID_MODES = ("raw", "voltage_time", "voltage_only", "all")
    if save_mode not in _VALID_MODES:
        raise ValueError(f"save_mode 必须是 {_VALID_MODES}, 收到 '{save_mode}'")

    path = _resolve_path(filepath, ".npz")

    arrays: Dict[str, np.ndarray] = {}
    for r in results:
        prefix = f"ch{r.channel}"
        if save_mode in ("raw", "all"):
            arrays[f"{prefix}_raw"] = r.raw_data
        if save_mode in ("voltage_time", "voltage_only", "all"):
            arrays[f"{prefix}_voltage"] = r.voltage
        if save_mode in ("voltage_time", "all"):
            arrays[f"{prefix}_time"] = r.time

    # 元数据
    preamble_info = {}
    for r in results:
        preamble_info[f"ch{r.channel}"] = r.preamble_dict
        preamble_info[f"ch{r.channel}_ts"] = r.timestamp

    metadata = {
        "timestamp": _timestamp_str(),
        "num_channels": len(results),
        "save_mode": save_mode,
        "preamble": preamble_info,
        "channels": [r.channel for r in results],
    }
    if config_dict:
        metadata["config"] = config_dict
    arrays["metadata"] = np.array([json.dumps(metadata)])

    np.savez_compressed(path, **arrays)
    logger.info("NPZ 已保存: %s (mode=%s, %.1f MB)", path, save_mode,
                os.path.getsize(path) / 1e6)
    return path


def load_npz(filepath: str) -> Tuple[List[AcquisitionResult], Optional[Dict]]:
    """
    加载 NPZ 文件, 重建 AcquisitionResult 列表。
    自动兼容所有 save_mode。
    """
    data = np.load(filepath, allow_pickle=True)
    metadata = json.loads(data["metadata"].item())
    config_dict = metadata.get("config")
    save_mode = metadata.get("save_mode", "raw")  # 旧文件兼容

    results = []
    for ch in metadata["channels"]:
        prefix = f"ch{ch}"
        ch_preamble = metadata["preamble"][f"ch{ch}"]
        ts = metadata["preamble"].get(f"ch{ch}_ts", 0)

        # 根据 save_mode 读取不同的数据
        raw = None
        voltage = None
        time_axis = None

        if f"{prefix}_raw" in data:
            raw = data[f"{prefix}_raw"].astype(np.int16)

        if f"{prefix}_voltage" in data:
            voltage = data[f"{prefix}_voltage"]
        elif raw is not None:
            # 从 raw 重建 voltage
            voltage = convert_raw_to_voltage(
                raw, ch_preamble["vertical_gain"],
                ch_preamble["vertical_offset"],
                ch_preamble["code_per_div"],
            )

        if f"{prefix}_time" in data:
            time_axis = data[f"{prefix}_time"]
        elif raw is not None and voltage is not None:
            # 重建时间轴
            tb = 1.0
            hdiv = 10
            if config_dict:
                tb = config_dict.get("timebase_scale", ch_preamble.get("horiz_interval", 1e-9) * 50)
                hdiv = config_dict.get("horizontal_divisions", 10)
            else:
                tb = ch_preamble.get("horiz_interval", 1e-9) * 50
            time_axis = build_time_axis(
                len(raw) if raw is not None else len(voltage),
                ch_preamble["horiz_interval"],
                ch_preamble.get("horiz_offset", 0), tb, hdiv,
            )

        if voltage is None:
            raise ValueError(f"无法重建 voltage: 文件未包含 voltage 且没有 raw 数据")

        if time_axis is None:
            time_axis = np.arange(len(voltage), dtype=np.float64) * ch_preamble.get("horiz_interval", 1e-9)

        result = AcquisitionResult(
            channel=ch,
            raw_data=raw if raw is not None else np.array([], dtype=np.int16),
            voltage=voltage,
            time=time_axis,
            preamble_dict=ch_preamble,
            config_snapshot=config_dict or {},
            timestamp=ts,
        )
        results.append(result)

    return results, config_dict


# ==================================================================
# HDF5 (需 h5py, gzip 压缩, 适合超大数据集)
# ==================================================================

def save_to_h5(filepath: str,
               results: List[AcquisitionResult],
               config_dict: Optional[Dict] = None,
               compression_level: int = 6) -> str:
    """
    多通道数据保存为 HDF5 格式。

    - 仅存 raw int16, 不存冗余的 time/voltage
    - 使用 gzip 压缩 (level 1-9, 默认 6)
    - 适合 10M 点以上的大采集

    需要安装 h5py: pip install h5py
    """
    try:
        import h5py
    except ImportError:
        logger.error("保存 HDF5 需要 h5py，请安装: pip install h5py")
        raise

    path = _resolve_path(filepath, ".h5")

    with h5py.File(path, "w") as f:
        for r in results:
            grp = f.create_group(f"ch{r.channel}")
            grp.create_dataset("raw", data=r.raw_data,
                               compression="gzip", compression_opts=compression_level)
            # 存 preamble 和配置为属性
            for k, v in r.preamble_dict.items():
                grp.attrs[k] = v
            grp.attrs["channel"] = r.channel
            grp.attrs["timestamp"] = r.timestamp

        # 全局属性
        f.attrs["timestamp"] = _timestamp_str()
        f.attrs["num_channels"] = len(results)
        f.attrs["format"] = "sds_acquisition_h5_v1"
        if config_dict:
            f.attrs["config"] = json.dumps(config_dict)

    logger.info("HDF5 已保存: %s (gzip=%d, %.1f MB)", path, compression_level,
                os.path.getsize(path) / 1e6)
    return path


def load_h5(filepath: str) -> Tuple[List[AcquisitionResult], Optional[Dict]]:
    """
    加载 HDF5 文件, 重建 AcquisitionResult 列表。
    """
    try:
        import h5py
    except ImportError:
        raise ImportError("需要 h5py: pip install h5py")

    config_dict = None
    results = []

    with h5py.File(filepath, "r") as f:
        config_raw = f.attrs.get("config")
        if config_raw:
            config_dict = json.loads(config_raw)

        for ch_name in f:
            if not ch_name.startswith("ch"):
                continue
            grp = f[ch_name]
            raw = grp["raw"][:].astype(np.int16)

            # 重建 preamble dict
            preamble_dict = {}
            for attr_name in grp.attrs:
                preamble_dict[attr_name] = grp.attrs[attr_name]
            ch = preamble_dict.pop("channel", int(ch_name[2:]))
            ts = preamble_dict.pop("timestamp", 0.0)

            voltage = convert_raw_to_voltage(
                raw, preamble_dict["vertical_gain"],
                preamble_dict["vertical_offset"],
                preamble_dict["code_per_div"],
            )

            if config_dict:
                tb = config_dict.get("timebase_scale", preamble_dict.get("horiz_interval", 1e-9) * 50)
                hdiv = config_dict.get("horizontal_divisions", 10)
            else:
                tb = preamble_dict.get("horiz_interval", 1e-9) * 50
                hdiv = 10

            time_axis = build_time_axis(
                len(raw), preamble_dict["horiz_interval"],
                preamble_dict.get("horiz_offset", 0), tb, hdiv,
            )

            results.append(AcquisitionResult(
                channel=ch, raw_data=raw, voltage=voltage,
                time=time_axis, preamble_dict=preamble_dict,
                config_snapshot=config_dict or {},
                timestamp=ts,
            ))

    return results, config_dict


# ==================================================================
# 原始二进制 (极致精简, 裸 ADC 数据)
# ==================================================================

def save_to_bin(filepath: str,
                results: List[AcquisitionResult],
                config_dict: Optional[Dict] = None) -> str:
    """
    保存为原始二进制格式。

    每个通道生成: .bin (raw int16) + .json (元数据)
    这是体积最小的无损格式, 但非自包含。
    """
    base = _resolve_path(filepath, ".bin")
    if base.endswith(".bin"):
        base = base[:-4]

    for r in results:
        bin_path = f"{base}_ch{r.channel}.bin"
        r.raw_data.astype(np.int16).tofile(bin_path)

        meta = {
            "channel": r.channel,
            "num_points": len(r.raw_data),
            "dtype": "int16",
            "preamble": r.preamble_dict,
            "timestamp": r.timestamp,
        }
        if config_dict:
            meta["config"] = config_dict

        json_path = f"{base}_ch{r.channel}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        bin_size = os.path.getsize(bin_path)
        logger.info("BIN 已保存: %s (%.1f MB)", bin_path, bin_size / 1e6)

    return base


def load_bin(bin_path: str) -> AcquisitionResult:
    """
    加载 .bin + .json 文件对。
    bin_path 是 .bin 文件路径, 同名的 .json 自动加载。
    """
    json_path = bin_path.replace(".bin", ".json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"元数据文件不存在: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    raw = np.fromfile(bin_path, dtype=np.int16)
    pd = meta["preamble"]
    config_dict = meta.get("config", {})

    voltage = convert_raw_to_voltage(
        raw, pd["vertical_gain"], pd["vertical_offset"], pd["code_per_div"],
    )

    if config_dict:
        tb = config_dict.get("timebase_scale", pd.get("horiz_interval", 1e-9) * 50)
        hdiv = config_dict.get("horizontal_divisions", 10)
    else:
        tb = pd.get("horiz_interval", 1e-9) * 50
        hdiv = 10

    time_axis = build_time_axis(
        len(raw), pd["horiz_interval"], pd.get("horiz_offset", 0), tb, hdiv,
    )

    return AcquisitionResult(
        channel=meta["channel"], raw_data=raw, voltage=voltage,
        time=time_axis, preamble_dict=pd,
        config_snapshot=config_dict,
        timestamp=meta.get("timestamp", 0),
    )


# ==================================================================
# 旧格式兼容 (CSV, MAT)
# ==================================================================

def save_to_csv(filepath: str, result: AcquisitionResult) -> str:
    """单通道数据保存为 CSV（体积大, 仅适合少量数据查看用）。"""
    path = _resolve_path(filepath, ".csv")
    base, ext = os.path.splitext(path)
    path = f"{base}_ch{result.channel}{ext}"

    np.savetxt(path,
               np.column_stack([result.time, result.voltage]),
               delimiter=",", header="time_s,voltage_V", comments="")
    logger.info("CSV 已保存: %s (%.1f MB)", path, os.path.getsize(path) / 1e6)
    return path


def save_to_mat(filepath: str,
                results: List[AcquisitionResult],
                config_dict: Optional[Dict] = None) -> str:
    """多通道数据保存为 MAT (MATLAB) 格式。"""
    try:
        from scipy.io import savemat
    except ImportError:
        raise ImportError("保存 MAT 需要 scipy: pip install scipy")

    path = _resolve_path(filepath, ".mat")

    md: Dict[str, np.ndarray] = {}
    for r in results:
        prefix = f"ch{r.channel}"
        md[f"{prefix}_time"] = r.time
        md[f"{prefix}_voltage"] = r.voltage
        md[f"{prefix}_raw"] = r.raw_data

    metadata = {
        "timestamp": _timestamp_str(),
        "num_channels": len(results),
        "preamble": {f"ch{r.channel}": r.preamble_dict for r in results},
    }
    if config_dict:
        metadata["config"] = config_dict
    md["metadata"] = metadata

    savemat(path, md)
    logger.info("MAT 已保存: %s (%.1f MB)", path, os.path.getsize(path) / 1e6)
    return path
