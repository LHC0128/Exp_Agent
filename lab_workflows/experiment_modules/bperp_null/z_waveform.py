"""Z 脉冲任意波文件：读取与边界校验、拖尾补偿波形生成、文件写出。

文件格式（UTF-8 文本）：
- ``# key: value`` 注释行记录生成参数，必须含 ``period_us``（与脉冲周期一致）；
- 其后每行一个数，共 ``ARB_POINTS`` 行，是一个周期的归一化电流，从 +脉冲上升沿起算；
- 最大绝对值为 1，对应 ``Z_PEAK_CURRENT_MA``；DG4000 按 ±1 → ±Vpp/2 固定映射，不按文件内容重新缩放。

拖尾补偿（屏蔽筒慢响应模型 B = s_f·I + (1−s_f)·LP_τe[I]）：每个脉冲结束后接
−A·exp(−(t−w)/τc)，A = (1−s_f)/s_f · u（u 为脉冲结束时自身的低通值），τc = s_f·τe，
脉冲后原子处 z 场为零；尾按周期延拓叠加。推导见运行目录
``data/pi_width_scan/_pi_width_investigation_20261118/10_tail_compensation/结论.md``。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..z_chain_stability.processing import pulse_waveform

WAVEFORM_DIR = "data/z_waveforms"
PEAK_TOLERANCE = 1e-6
WRAP_JUMP_MAX = 0.05
PERIOD_RTOL = 1e-6


@dataclass(frozen=True)
class ZWaveform:
    name: str
    values: np.ndarray
    header: dict = field(default_factory=dict)
    sha256: str = ""

    @property
    def mean_square(self) -> float:
        """归一化电流平方的周期平均；电阻功耗 = I_peak² · mean_square · R。"""
        return float(np.mean(self.values ** 2))

    @property
    def antisymmetry_error(self) -> float:
        """后半周期与前半周期取反之差的最大值；±π 对称波形应接近 0。"""
        half = len(self.values) // 2
        return float(np.max(np.abs(self.values[:half] + self.values[half:2 * half])))

    def record(self) -> dict:
        return dict(source="file", file=self.name, sha256=self.sha256, points=int(len(self.values)),
                    min=float(self.values.min()), max=float(self.values.max()),
                    mean_square=self.mean_square, antisymmetry_error=self.antisymmetry_error,
                    header=dict(self.header))


def waveform_path(root, name: str) -> Path:
    """波形目录内的文件路径；只接受不含路径分隔符的 .csv 文件名。"""
    name = str(name).strip()
    if not name or Path(name).name != name or name in {".", ".."} or not name.lower().endswith(".csv"):
        raise ValueError(f"Z_WAVEFORM_FILE 必须是 {WAVEFORM_DIR}/ 下的 .csv 文件名：{name!r}")
    return Path(root) / WAVEFORM_DIR / name


def parse_waveform_text(text: str) -> tuple[dict, list[float]]:
    header, values = {}, []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            key, sep, value = stripped[1:].partition(":")
            if sep:
                header[key.strip()] = value.strip()
            continue
        try:
            values.append(float(stripped.split(",")[0]))
        except ValueError as exc:
            raise ValueError(f"波形文件第 {number} 行不是数值：{stripped[:40]!r}") from exc
    return header, values


def load_z_waveform(path, *, points: int, period_us: float) -> ZWaveform:
    """读取并校验波形文件；任何不满足格式或安全前提的文件都拒绝。"""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Z 波形文件不存在：{path}")
    raw = path.read_bytes()
    try:
        header, values = parse_waveform_text(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Z 波形文件不是 UTF-8 文本：{path.name}") from exc
    array = np.asarray(values, dtype=float)
    if array.size != points:
        raise ValueError(f"Z 波形文件 {path.name} 有 {array.size} 个点，要求正好 {points} 点")
    if not np.isfinite(array).all():
        raise ValueError(f"Z 波形文件 {path.name} 含非有限值")
    peak = float(np.max(np.abs(array)))
    if abs(peak - 1.0) > PEAK_TOLERANCE:
        raise ValueError(f"Z 波形文件 {path.name} 的最大绝对值为 {peak:.6g}，必须归一化到 1"
                         "（1 对应 Z_PEAK_CURRENT_MA）")
    if abs(array[0] - array[-1]) > WRAP_JUMP_MAX:
        raise ValueError(f"Z 波形文件 {path.name} 首尾跳变 {abs(array[0] - array[-1]):.3g} 超过 {WRAP_JUMP_MAX}，"
                         "周期拼接处不连续")
    if "period_us" not in header:
        raise ValueError(f"Z 波形文件 {path.name} 缺少 '# period_us:' 注释行")
    try:
        file_period = float(header["period_us"])
    except ValueError as exc:
        raise ValueError(f"Z 波形文件 {path.name} 的 period_us 不是数值") from exc
    if not math.isfinite(file_period) or abs(file_period - period_us) > PERIOD_RTOL * period_us:
        raise ValueError(f"Z 波形文件周期 {file_period:g} µs 与脉冲周期 {period_us:g} µs 不一致")
    return ZWaveform(name=path.name, values=array, header=header, sha256=hashlib.sha256(raw).hexdigest())


def exact_tail(width_us: float, edge_us: float, s_f: float, tau_e_us: float, n: int = 20001) -> tuple[float, float]:
    """单时间常数屏蔽模型下使脉冲后 z 场为零的尾 (A, τc)；A 相对主脉冲平顶。"""
    if not 0 < s_f < 1 or tau_e_us <= 0:
        raise ValueError("s_f 须在 (0, 1) 内，τe 须为正")
    t = np.linspace(0.0, width_us, n)
    p = np.ones_like(t)
    rise, fall = t < edge_us, t > width_us - edge_us
    p[rise] = 0.5 * (1 - np.cos(np.pi * t[rise] / edge_us))
    p[fall] = 0.5 * (1 + np.cos(np.pi * (t[fall] - (width_us - edge_us)) / edge_us))
    u = float(np.trapezoid(p * np.exp(-(width_us - t) / tau_e_us), t) / tau_e_us)
    return (1 - s_f) / s_f * u, s_f * tau_e_us


def tail_compensated_waveform(period_us: float, width_us: float, edge_us: float, tail_a: float,
                              tau_c_us: float, points: int, n_wrap: int = 8) -> tuple[np.ndarray, float]:
    """±π 升余弦脉冲 + 各自的反号指数尾（周期延拓叠加），归一化到最大绝对值 1。

    返回 (波形, 归一化前的最大绝对值)；后者 > 1 时主脉冲平顶对应 Z_PEAK_CURRENT_MA / 该值。
    """
    if tail_a < 0 or tau_c_us <= 0:
        raise ValueError("尾幅须非负、τc 须为正")
    base = pulse_waveform(period_us, width_us, edge_us, points=points)
    t = np.arange(points, dtype=float) * (period_us / points)
    tail = np.zeros(points)
    for k in range(n_wrap):
        for sign, start in ((1.0, 0.0), (-1.0, period_us / 2.0)):
            x = (t - start) % period_us + k * period_us
            tail += sign * np.where(x >= width_us, -tail_a * np.exp(-(x - width_us) / tau_c_us), 0.0)
    wave = base + tail
    scale = float(np.max(np.abs(wave)))
    return wave / scale, scale


def write_waveform_file(path, values, header: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {key}: {value}" for key, value in header.items()]
    lines += [f"{float(v):.8f}" for v in values]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
