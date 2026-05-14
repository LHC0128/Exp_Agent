"""解调器相关操作模块.

涵盖四个硬件模块的配置：
  - 信号输入模块 (sigins)    — 2 个，索引 0~1
  - 振荡器模块 (oscs)        — 2 个，索引 0~1
  - 解调器模块 (demods)      — 6 个，索引 0~5
  - 信号输出模块 (sigouts)   — 2 个，索引 0~1

以及单个参数的独立设置函数。
"""

import math
import time
import logging
from typing import Optional

from .instrument import HF2Instrument
from .config import (
    SignalInputConfig,
    OscillatorConfig,
    DemodulatorConfig,
    SignalOutputConfig,
)

logger = logging.getLogger(__name__)

# ======================================================================
# 信号输入模块 (sigins)
# ======================================================================


def configure_signal_input(
    instr: HF2Instrument,
    config: SignalInputConfig,
) -> None:
    """配置信号输入模块.

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    config : SignalInputConfig
        信号输入配置
    """
    path = instr.sig_in_path(config.input_index)
    instr.set_double(f"{path}/range", config.range)
    instr.set_int(f"{path}/ac", 1 if config.ac_coupling else 0)
    instr.set_int(f"{path}/diff", 1 if config.diff else 0)
    instr.set_int(f"{path}/imp50", 1 if config.impedance == 50 else 0)
    instr.sync()
    logger.info(
        "信号输入 %d: range=%.3f V, ac=%s, diff=%s, imp=%s",
        config.input_index, config.range,
        "AC" if config.ac_coupling else "DC",
        "差分" if config.diff else "单端",
        f"{config.impedance}Ω",
    )


def set_input_range(
    instr: HF2Instrument,
    range_v: float,
    input_idx: int = 0,
) -> None:
    """设置输入量程.

    参数
    ----------
    instr : HF2Instrument
    range_v : float
        量程 (V)
    input_idx : int
        输入模块索引 (0 或 1)
    """
    instr.set_double(f"{instr.sig_in_path(input_idx)}/range", range_v)
    instr.sync()
    logger.info("输入 %d 量程已设置为 %.3f V", input_idx, range_v)


# ======================================================================
# 振荡器模块 (oscs)
# ======================================================================


def configure_oscillator(
    instr: HF2Instrument,
    config: OscillatorConfig,
) -> None:
    """配置振荡器模块.

    注意：HF2 的 OS 节点仅有 freq。外参考需要通过 /system/extclk 设置。
    source 字段仅作为配置记录，外参考下需配合 PLL 使用。

    参数
    ----------
    instr : HF2Instrument
    config : OscillatorConfig
    """
    path = instr.osc_path(config.osc_index)
    instr.set_double(f"{path}/freq", config.frequency)
    instr.sync()
    logger.info(
        "振荡器 %d: freq=%.4f Hz (source=%s)",
        config.osc_index, config.frequency, config.source,
    )


def set_reference_frequency(
    instr: HF2Instrument,
    freq: float,
    osc_idx: int = 0,
) -> None:
    """设置参考频率.

    参数
    ----------
    instr : HF2Instrument
    freq : float
        频率 (Hz)
    osc_idx : int
        振荡器索引 (0 或 1)
    """
    instr.set_double(f"{instr.osc_path(osc_idx)}/freq", freq)
    instr.sync()
    logger.info("参考频率已设置为 %.4f Hz (osc %d)", freq, osc_idx)


# ======================================================================
# 解调器模块 (demods)
# ======================================================================


def configure_demodulator(
    instr: HF2Instrument,
    config: DemodulatorConfig,
) -> float:
    """批量配置解调器参数.

    返回设置生效后的实际采样率（可能与请求值不同，因 HF2 仅支持特定速率）。

    参数
    ----------
    instr : HF2Instrument
    config : DemodulatorConfig

    返回
    -------
    float
        实际采样率 (Sa/s)
    """
    base = instr.demod_path(config.demod_index)

    instr.set_int(f"{base}/enable", 1 if config.enable else 0)
    instr.set_double(f"{base}/rate", config.rate)
    instr.set_int(f"{base}/adcselect", config.input_channel)
    instr.set_int(f"{base}/oscselect", config.osc_select)
    instr.set_int(f"{base}/harmonic", config.harmonic)
    instr.set_double(f"{base}/timeconstant", config.time_constant)
    instr.set_int(f"{base}/order", config.order)
    instr.set_double(f"{base}/phaseshift", config.phase)
    instr.sync()
    # 读回实际采样率
    actual_rate = instr.get_double(f"{base}/rate")
    logger.info(
        "解调器 %d 已配置: rate=%.1f → %.1f Sa/s, input_ch=%d, osc=%d, "
        "harmonic=%d, tau=%.2e s, order=%d, phase=%.2f deg",
        config.demod_index, config.rate, actual_rate,
        config.input_channel, config.osc_select,
        config.harmonic, config.time_constant,
        config.order, config.phase,
    )
    return actual_rate


def set_demod_enable(
    instr: HF2Instrument,
    demod_idx: int,
    enable: bool,
) -> None:
    """使能/禁用解调器数据传输（将解调结果传输到计算机）.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
        解调器索引 (0-7)
    enable : bool
    """
    instr.set_int(f"{instr.demod_path(demod_idx)}/enable", 1 if enable else 0)
    instr.sync()
    logger.info("解调器 %d 数据传输 %s", demod_idx, "已使能" if enable else "已禁用")


def set_demod_rate(
    instr: HF2Instrument,
    demod_idx: int,
    rate: float,
) -> None:
    """设置解调器输出数据速率.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    rate : float
        Sa/s
    """
    instr.set_double(f"{instr.demod_path(demod_idx)}/rate", rate)
    instr.sync()
    logger.info("解调器 %d rate=%.1f Sa/s", demod_idx, rate)


def set_demod_time_constant(
    instr: HF2Instrument,
    demod_idx: int,
    tc: float,
) -> None:
    """设置解调器低通时间常数（决定带宽）.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    tc : float
        时间常数 (s)
    """
    instr.set_double(f"{instr.demod_path(demod_idx)}/timeconstant", tc)
    instr.sync()
    logger.info("解调器 %d 时间常数=%.2e s", demod_idx, tc)


def set_demod_order(
    instr: HF2Instrument,
    demod_idx: int,
    order: int,
) -> None:
    """设置解调器低通滤波器阶数.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    order : int
    """
    instr.set_int(f"{instr.demod_path(demod_idx)}/order", order)
    instr.sync()
    logger.info("解调器 %d 滤波器阶数=%d", demod_idx, order)


def set_demod_harmonic(
    instr: HF2Instrument,
    demod_idx: int,
    harmonic: int,
) -> None:
    """设置解调谐波.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    harmonic : int
        1=基波
    """
    instr.set_int(f"{instr.demod_path(demod_idx)}/harmonic", harmonic)
    instr.sync()
    logger.info("解调器 %d 谐波=%d", demod_idx, harmonic)


def set_demod_phase(
    instr: HF2Instrument,
    demod_idx: int,
    phase_deg: float,
) -> None:
    """设置解调器相位偏移.

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    phase_deg : float
        相位 (度)
    """
    instr.set_double(f"{instr.demod_path(demod_idx)}/phaseshift", phase_deg)
    instr.sync()
    logger.info("解调器 %d 相位偏移=%.2f deg", demod_idx, phase_deg)


def read_demod_sample(
    instr: HF2Instrument,
    demod_idx: int = 0,
) -> dict:
    """读取当前解调样本.

    返回包含 x, y, r, phase, frequency, theta 等字段的字典。

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
        解调器索引 (0-7)

    返回
    -------
    dict
    """
    path = f"{instr.demod_path(demod_idx)}/sample"
    sample = instr.get_sample(path)

    result = {}
    for key in ("x", "y", "frequency", "phase", "timestamp"):
        values = sample.get(key, [])
        result[key] = values[-1] if values else 0.0

    result["r"] = math.hypot(result["x"], result["y"])
    result["theta"] = math.atan2(result["y"], result["x"])

    if "timestamp" in result and isinstance(result["timestamp"], (int, float)):
        result["time"] = result["timestamp"] / instr.clockbase

    return result


def auto_calibrate_phase(
    instr: HF2Instrument,
    demod_idx: int = 0,
    tolerance_deg: float = 0.05,
    max_attempts: int = 10,
    settle_time: float = 0.1,
) -> float:
    """自动调整相位偏移，使解调器输出相位为 0.

    直接读取解调器 sample.phase，将补偿值累加到 phaseshift 节点。
    settle_time 等待低通滤波器稳定。

    参数
    ----------
    instr : HF2Instrument
    demod_idx : int
    tolerance_deg : float
    max_attempts : int
    settle_time : float
        每次调整后等待滤波器稳定的时间 (s)

    返回
    -------
    float
        实际设置的补偿相位 (度)

    抛出
    ------
    RuntimeError
    """
    base = instr.demod_path(demod_idx)

    for attempt in range(1, max_attempts + 1):
        time.sleep(settle_time)

        sample = read_demod_sample(instr, demod_idx)
        theta_deg = math.degrees(sample["theta"])  # atan2(Y, X)，解调输出相位
        r = sample["r"]

        if r < 1e-12:
            logger.warning(
                "解调器 %d 输出幅度接近零 (%e V)，无法校准相位",
                demod_idx, r,
            )
            return 0.0

        if abs(theta_deg) < tolerance_deg:
            logger.info(
                "相位校准完成 (attempt %d): theta=%.4f deg", attempt, theta_deg,
            )
            return instr.get_double(f"{base}/phaseshift")

        current_shift = instr.get_double(f"{base}/phaseshift")
        new_shift = current_shift + theta_deg
        instr.set_double(f"{base}/phaseshift", new_shift)
        instr.sync()

        logger.debug(
            "相位校准 attempt %d: theta=%.4f deg, 补偿 %.4f -> %.4f deg",
            attempt, theta_deg, current_shift, new_shift,
        )

    raise RuntimeError(
        f"相位校准失败 (max_attempts={max_attempts})，"
        f"最终 theta={theta_deg:.4f} deg"
    )


# ======================================================================
# 信号输出模块 (sigouts) — 与辅助输出 AUXOUTS 不同
# ======================================================================


def configure_signal_output(
    instr: HF2Instrument,
    config: SignalOutputConfig,
) -> None:
    """配置信号输出模块.

    参数
    ----------
    instr : HF2Instrument
    config : SignalOutputConfig
    """
    path = instr.sig_out_path(config.output_index)
    instr.set_double(f"{path}/range", config.range)
    instr.set_double(f"{path}/offset", config.offset)
    instr.set_int(f"{path}/add", 1 if config.add else 0)
    instr.set_int(f"{path}/on", 1 if config.enable else 0)
    instr.sync()
    logger.info(
        "信号输出 %d: range=%.3f V, offset=%.3f V, add=%s, enable=%s",
        config.output_index, config.range, config.offset,
        config.add, config.enable,
    )


def set_output_enable(
    instr: HF2Instrument,
    output_idx: int,
    enable: bool,
) -> None:
    """使能/禁用信号输出.

    参数
    ----------
    instr : HF2Instrument
    output_idx : int
        输出通道索引 (0 或 1)
    enable : bool
    """
    instr.set_int(f"{instr.sig_out_path(output_idx)}/on", 1 if enable else 0)
    instr.sync()
    logger.info("信号输出 %d %s", output_idx, "已使能" if enable else "已禁用")


def set_output_range(
    instr: HF2Instrument,
    output_idx: int,
    range_v: float,
) -> None:
    """设置信号输出量程.

    参数
    ----------
    instr : HF2Instrument
    output_idx : int
    range_v : float
        量程 (V)
    """
    instr.set_double(f"{instr.sig_out_path(output_idx)}/range", range_v)
    instr.sync()
    logger.info("信号输出 %d 量程=%.3f V", output_idx, range_v)
