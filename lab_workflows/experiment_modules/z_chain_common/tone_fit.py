"""带工频与 5 kHz 载波泄漏扣除的正交解调音调拟合；不连接仪器、不写文件。

Demod 4 宽带解调（时间常数 1 µs 级）输出里，输入直流偏置经解调混频落到 5 kHz 载波及其
10/15/20 kHz 谐波，是读出链的已知寄生项、不是原子信号。本模块把目标基带音调、二次谐波、
直流漂移、不与目标音调共线的 50–250 Hz 工频谐波和这些载波泄漏项一起做最小二乘投影：
发布的 y_pk_v / x_pk_v 取联合扣除后的干净系数，另保留未扣除口径的标准误与信噪比作对照。
实现由 rf_phase_sensitivity.analysis_steps 迁出，行为与迁移前一致。
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import correlate

MIN_TONE_SNR = 5.0
MIN_TONE_CYCLES = 9.8
MAINS_HARMONICS_HZ = (50.0, 100.0, 150.0, 200.0, 250.0)
# 载波泄漏项只纳入低于 0.45 × 采样率的谐波，避免贴近 Nyquist 的项放大设计矩阵条件数。
CARRIER_HARMONICS_HZ = (5000.0, 10000.0, 15000.0, 20000.0)
CARRIER_RATE_FRACTION = 0.45
STEP_RTOL = 5e-4
RATE_RTOL = 1e-3
GRID_ATOL_SAMPLES = 0.02


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def sample_grid(time_s, rate_sa_s):
    """用端点间隔重算实际率，逐点验证均匀性、连续性以及回读采样率。"""
    reported = _number(rate_sa_s)
    t = np.asarray(time_s, dtype=float)
    if reported is None or reported <= 0 or t.ndim != 1 or t.size < 8 or not np.isfinite(t).all():
        raise ValueError("采样率或时间轴无效，单段至少需要 8 个样本")
    t = t - t[0]
    step = t[-1] / (t.size - 1)
    steps = np.diff(t)
    if (not math.isfinite(step) or step <= 0 or np.any(steps <= 0)
            or not np.allclose(steps, step, rtol=STEP_RTOL, atol=0)
            or np.any(np.abs(t / step - np.arange(t.size)) > GRID_ATOL_SAMPLES)):
        raise ValueError("时间轴不均匀、不连续或有缺样")
    rate = 1 / float(step)
    if not math.isclose(rate, reported, rel_tol=RATE_RTOL):
        raise ValueError("时间轴采样率与帧回读不一致")
    return t, rate


def _waveform(values, time_s):
    y = np.asarray(values, dtype=float)
    if y.shape != time_s.shape or not np.isfinite(y).all():
        raise ValueError("XY 波形非有限或长度与时间轴不一致")
    return y


def _mains_terms(time_s, frequency_hz, include_2f, nyquist):
    """返回（可用工频谐波频率、与目标音调/二次谐波碰撞而跳过的频率、容差）。

    容差取 2 Hz 与帧长频率分辨率 2/T 的较大值：目标音调与某个工频谐波相距不足一个
    容差时，两项近似共线，强行同时拟合会让设计矩阵病态并放大标准误，因此该谐波项
    跳过并记入碰撞列表，由调用方写进行内 mains_collision。
    """
    duration = float(time_s[-1])
    guard_hz = max(2.0 / duration, 0.5)
    avoid = (frequency_hz, *((2 * frequency_hz,) if include_2f else ()))
    used, collisions = [], []
    for mains_hz in MAINS_HARMONICS_HZ:
        if mains_hz + 1.0 / duration >= nyquist:
            break
        if any(abs(mains_hz - target) <= guard_hz for target in avoid):
            collisions.append(float(mains_hz))
        else:
            used.append(float(mains_hz))
    return used, collisions, guard_hz


def _carrier_terms(frequency_hz, include_2f, rate, guard_hz):
    """返回（实际纳入的 5 kHz 载波泄漏项、跳过项及原因）。

    5 kHz 载波及其 10/15/20 kHz 谐波来自 Demod 4 宽带解调输出中输入直流偏置的混频泄漏，
    是读出链的已知寄生项，不作为原子信号发布。仅纳入与目标音调/二次谐波不碰撞（容差同
    工频项）且低于 CARRIER_RATE_FRACTION × rate 的谐波；碰撞与超限项记入跳过列表，
    避免病态设计矩阵破坏拟合。
    """
    limit = CARRIER_RATE_FRACTION * rate
    avoid = (frequency_hz, *((2 * frequency_hz,) if include_2f else ()))
    used, skipped = [], []
    for carrier_hz in CARRIER_HARMONICS_HZ:
        if carrier_hz >= limit:
            skipped.append(f"{carrier_hz:g} Hz（超过 0.45 × 采样率）")
        elif any(abs(carrier_hz - target) <= guard_hz for target in avoid):
            skipped.append(f"{carrier_hz:g} Hz（与目标音调或二次谐波碰撞）")
        else:
            used.append(float(carrier_hz))
    return used, skipped


def _tone_columns(time_s, frequency_hz, include_2f, spur_hz):
    """构造目标音调、常数、线性漂移、可选二次谐波与工频/载波寄生项的正交解调设计列。"""
    theta = (2 * np.pi * frequency_hz) * time_s
    columns = [np.sin(theta), np.cos(theta), np.ones_like(time_s), time_s / time_s[-1] - 0.5]
    if include_2f:
        columns.extend((np.sin(2 * theta), np.cos(2 * theta)))
    for spur in spur_hz:
        angle = (2 * np.pi * spur) * time_s
        columns.extend((np.sin(angle), np.cos(angle)))
    return columns


def _solve_tone(columns, x, y, rate, correlation_s, include_2f):
    """对给定设计列联合最小二乘拟合 X/Y 并发布 Y 音调幅值、标准误与信噪比。"""
    design = np.column_stack(columns)
    coeff, _, rank, spectrum = np.linalg.lstsq(design, np.column_stack((x, y)), rcond=None)
    if rank != design.shape[1] or spectrum[-1] <= spectrum[0] * 1e-8:
        raise ValueError("基带音调拟合的设计矩阵病态")
    x_pk, y_pk = map(float, np.hypot(*coeff[:2]))
    residual = y - design @ coeff[:, 1]
    dof = len(y) - rank
    inverse_gram = np.linalg.inv(design.T @ design)
    direction = coeff[:2, 1] / y_pk if y_pk > 0 else np.array([1.0, 0.0])
    variance = (residual @ residual / dof) * (direction @ inverse_gram[:2, :2] @ direction)
    # Bartlett 加权的相关残差方差只提高、不会压低独立残差给出的标准误。
    lag = math.ceil(min(dof // 4, 5 * correlation_s * rate))
    if lag >= 1:
        score = residual * (design @ (inverse_gram[:, :2] @ direction))
        ac = correlate(score, score, mode="full", method="fft")[len(y) - 1:len(y) + lag]
        tapered = ac[0] + 2 * (ac[1:] @ (1 - np.arange(1, lag + 1) / (lag + 1)))
        variance = max(variance, tapered * len(y) / dof)
    y_se = math.sqrt(max(0.0, variance))
    # 抑制常数或纯漂移的舍入误差伪装成高 SNR；不设物理电压阈值。
    floor = max(np.finfo(float).tiny, 32 * np.finfo(float).eps * np.max(np.abs(y)))
    result = dict(x_pk_v=x_pk, y_pk_v=y_pk, y_pk_se_v=y_se, y_snr=float(y_pk / max(y_se, floor)))
    if include_2f:
        result.update(x_2f_pk_v=float(np.hypot(*coeff[4:6, 0])), y_2f_pk_v=float(np.hypot(*coeff[4:6, 1])))
    return result


def fit_tone(time_s, x_v, y_v, rate_sa_s, frequency_hz, *, correlation_s=0.0):
    """同时拟合基频、二次谐波、直流漂移及不碰撞的工频谐波与 5 kHz 载波泄漏项。

    5 kHz 载波泄漏来自 Demod 4 宽带解调（微秒级时间常数）：输入直流偏置经解调混频落到
    载波频率，10/15/20 kHz 为其谐波。它是读出链的已知寄生项，不是原子信号，只在 clean
    回归里作为干扰项扣除，不改变 y_pk_v 的物理含义。

    发布的 y_pk_v / x_pk_v 沿用联合扣除工频与载波泄漏后的干净系数（与改造前一致）；
    行内另保留原始口径（不含工频、不含载波项）的 y_pk_se_v / y_snr 作为对照，并发布
    扣除后的 y_pk_se_clean_v / y_snr_clean 供质检与告警判定。
    mains_collision_hz 记录因与目标音调或二次谐波共线而跳过的工频谐波，
    mains_harmonics_hz 记录实际参与拟合的工频谐波；carrier_terms_hz 记录实际纳入的
    载波泄漏项，carrier_skipped 记录因碰撞或超过 0.45×采样率而跳过的载波项。
    """
    t, rate = sample_grid(time_s, rate_sa_s)
    frequency = _number(frequency_hz)
    nyquist = min(rate, rate_sa_s) / 2
    if frequency is None or not 0 < frequency < nyquist:
        raise ValueError("基带音调不在帧的 Nyquist 范围内")
    cycles = frequency * float(t[-1])
    if cycles < MIN_TONE_CYCLES:
        raise ValueError(f"基带音调帧不足 {MIN_TONE_CYCLES:g} 个周期")
    x, y = _waveform(x_v, t), _waveform(y_v, t)
    include_2f = 2 * frequency + 1 / t[-1] < nyquist
    mains_used, collisions, guard_hz = _mains_terms(t, frequency, include_2f, nyquist)
    carrier_used, carrier_skipped = _carrier_terms(frequency, include_2f, rate, guard_hz)
    # 数据点不足时工频/载波项会挤占自由度，从最高频的寄生项开始裁剪。
    spurs = [*mains_used, *carrier_used]
    while spurs and len(t) <= len(_tone_columns(t, frequency, include_2f, spurs)) + 8:
        spurs.pop()
    carrier_skipped = [*carrier_skipped,
                       *(f"{value:g} Hz（数据点不足）" for value in carrier_used if value not in spurs)]
    carrier_used = [value for value in carrier_used if value in spurs]
    original = _solve_tone(_tone_columns(t, frequency, include_2f, ()), x, y, rate, correlation_s, include_2f)
    clean_columns = _tone_columns(t, frequency, include_2f, spurs)
    clean = _solve_tone(clean_columns, x, y, rate, correlation_s, include_2f) if spurs else original
    # 发布幅值沿用扣除工频/载波后的 clean 系数（与改造前一致）；原始口径只用于对照标准误与 SNR。
    result = dict(rate_sa_s=rate, samples=int(t.size), cycles=cycles,
                  x_pk_v=clean["x_pk_v"], y_pk_v=clean["y_pk_v"],
                  y_pk_se_v=original["y_pk_se_v"], y_snr=original["y_snr"],
                  y_pk_se_clean_v=clean["y_pk_se_v"], y_snr_clean=clean["y_snr"],
                  mains_harmonics_hz=[value for value in mains_used if value in spurs],
                  mains_collision_hz=collisions, mains_guard_hz=guard_hz,
                  carrier_terms_hz=carrier_used, carrier_skipped=carrier_skipped)
    if include_2f:
        result.update(x_2f_pk_v=clean["x_2f_pk_v"], y_2f_pk_v=clean["y_2f_pk_v"])
    if not all(math.isfinite(value) for value in result.values() if isinstance(value, (int, float))):
        raise ValueError("基带音调拟合产生了非有限值")
    return result
