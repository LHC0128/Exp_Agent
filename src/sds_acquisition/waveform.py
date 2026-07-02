import logging
import struct
import numpy as np
from typing import Dict, Any

logger = logging.getLogger(__name__)


class WaveformPreamble:
    """解析波形 preamble 二进制块（SDS 编程手册 §5.23）。"""

    # (字节偏移, 格式字符, 字段名)
    FIELDS = [
        (0x3C, "i", "data_bytes"),       # byte count of waveform data
        (0x74, "i", "point_num"),        # number of points
        (0x9C, "f", "vertical_gain"),    # V/count
        (0xA0, "f", "vertical_offset"),  # offset in V
        (0xA4, "f", "code_per_div"),     # ADC codes per division
        (0xAC, "h", "adc_bit"),          # ADC resolution in bits
        (0xB0, "f", "horiz_interval"),   # s/sample
        (0xB4, "d", "horiz_offset"),     # s (trigger offset)
        (0x144, "h", "tdiv_index"),      # time/div enum index
        (0x148, "f", "probe_atten"),     # probe attenuation
    ]

    def __init__(self, raw_bytes: bytes):
        self.raw_data = raw_bytes
        self._parse()

    def _parse(self) -> None:
        for offset, fmt, name in self.FIELDS:
            size = struct.calcsize(fmt)
            raw = self.raw_data[offset:offset + size]
            setattr(self, name, struct.unpack(fmt, raw)[0])

    def to_dict(self) -> Dict[str, Any]:
        return {name: getattr(self, name)
                for _, _, name in self.FIELDS}

    def __repr__(self) -> str:
        return f"WaveformPreamble({self.to_dict()})"


def convert_raw_to_voltage(
    raw_data: np.ndarray,
    vertical_gain: float,
    vertical_offset: float,
    code_per_div: float,
) -> np.ndarray:
    """
    将 ADC 原始码转换为电压值。
    V = raw / code_per_div * vertical_gain - vertical_offset
    """
    return (raw_data.astype(np.float64) / code_per_div
            * vertical_gain - vertical_offset)


def build_time_axis(
    num_points: int,
    horiz_interval: float,
    horiz_offset: float,
    horiz_divisions: int = 10,
    timebase_scale: float = None,
) -> np.ndarray:
    """
    构建时间轴.
    [关键] timebase_scale 不传时, 由 horiz_interval * num_points / horiz_divisions
    从 preamble 字段自动推算, 等价于 scope 实际运行的 (memory+rate+timebase) 协调后的
    真实时间基准 — 即使 SDS 内部仲裁后实际值与我们请求不同, 时间轴仍准确.
    """
    if timebase_scale is None:
        timebase_scale = horiz_interval * num_points / horiz_divisions
    t0 = -timebase_scale * horiz_divisions / 2 + horiz_offset
    return t0 + np.arange(num_points, dtype=np.float64) * horiz_interval


def extract_waveform_data(raw_bytes: bytes, width: str) -> np.ndarray:
    """
    将原始波形数据二进制块解析为 ADC 码数组。
    BYTE → int8, WORD → int16 (大端序)。
    """
    if width == "WORD":
        # WORD 模式默认 MSB first（大端序）
        data = np.frombuffer(raw_bytes, dtype=np.dtype(">i2"))
    else:
        data = np.frombuffer(raw_bytes, dtype=np.int8)
    return data.astype(np.int16)
