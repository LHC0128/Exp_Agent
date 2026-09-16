"""响应滤波闭环的纯数值核心：核构建、循环卷积、带限平移与覆盖预检。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...common import validate_safety_limit
from ...control_sources import (
    AppliedControlWaveform, TheoryControlSource, applied_from_voltage, load_theory_control,
    resolve_control_results_root,
)
from ...current_feedback import (
    AWCurrentResponse, CurrentCouplingCalibration, load_aw_frequency_response,
    load_current_coupling_calibration, validate_current_power,
)
from .static_feedback import StaticFit, fit_static_current, rms, scope_range


class ResponseCoverageError(ValueError):
    """学习频带缺少可靠响应；消息中列出需要补测的区间。"""


@dataclass(frozen=True)
class LearningFilter:
    """周期网格上的响应模型、逆核与收缩后的有效学习频带。"""

    response_model_a_per_v: np.ndarray
    inverse_kernel_frequency: np.ndarray
    forward_kernel: np.ndarray
    inverse_kernel: np.ndarray
    learning_band: np.ndarray
    requested_cutoff_hz: float
    effective_cutoff_hz: float
    effective_low_hz: float
    reliable_min_hz: float
    reliable_max_hz: float
    excluded_frequency_hz: np.ndarray
    interpolated_frequency_hz: np.ndarray


@dataclass(frozen=True)
class PreparedLoop:
    """本次闭环固定的目标、来源、学习频带和滤波核。"""

    theory: TheoryControlSource
    calibration: CurrentCouplingCalibration
    response: AWCurrentResponse | None
    fit: StaticFit
    target_current_a: np.ndarray
    target_rms_a: float
    initial: AppliedControlWaveform
    frequency_hz: np.ndarray
    response_model_a_per_v: np.ndarray
    inverse_kernel_frequency: np.ndarray
    forward_kernel: np.ndarray
    inverse_kernel: np.ndarray
    learning_band: np.ndarray
    target_outside_band_rms_a: float
    requested_cutoff_hz: float = 0.0
    effective_cutoff_hz: float = 0.0
    effective_low_hz: float = 0.0
    excluded_frequency_hz: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=float))
    interpolated_frequency_hz: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=float))


def circular_convolution(kernel: np.ndarray, signal: np.ndarray) -> np.ndarray:
    """周期卷积 sum_m kernel[m] * signal[(n-m) mod N]，FFT 加速实现。"""
    count = np.asarray(signal).size
    return np.fft.irfft(np.fft.rfft(kernel, n=count) * np.fft.rfft(signal, n=count), n=count)


def apply_band_mask(values: np.ndarray, band: np.ndarray) -> np.ndarray:
    """只保留频带 mask 内的周期分量，其余频率置零。"""
    return np.fft.irfft(np.fft.rfft(values) * band, n=values.size)


def shift_bandlimited(values: np.ndarray, shift: float, band: np.ndarray) -> np.ndarray:
    """带限周期平移 shift(x, s)[n] = x[n+s]；只改变频带内的相位。"""
    frequencies = np.fft.rfftfreq(values.size)
    phases = np.exp(2j * np.pi * frequencies * float(shift))
    return np.fft.irfft(np.fft.rfft(values) * phases * band, n=values.size)


def reliable_runs(
    frequency_hz: np.ndarray, transfer: np.ndarray, reliable: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """按原始频率顺序切出可靠连续段。

    任何不可靠频点都会切断插值区间：插值只发生在同一段内，既不跨过
    不可靠点，也不向段外外推。
    """
    runs: list[tuple[np.ndarray, np.ndarray]] = []
    start: int | None = None
    for index in range(frequency_hz.size):
        if bool(reliable[index]) and start is None:
            start = index
        elif not bool(reliable[index]) and start is not None:
            runs.append((frequency_hz[start:index], transfer[start:index]))
            start = None
    if start is not None:
        runs.append((frequency_hz[start:], transfer[start:]))
    return runs


def interpolate_in_run(
    run_frequency_hz: np.ndarray, run_transfer: np.ndarray, target_hz: float,
) -> complex | None:
    """在单个可靠段内插值 H；目标超出该段范围时返回 None。

    对数幅度与展开相位分别线性插值：幅度按 log 尺度避免量级压缩，
    相位先展开再插值以免 2π 跳变。参考信号与电流取自同一帧同一时间轴，
    因此相位随频率平缓变化、展开可靠。
    """
    if target_hz < run_frequency_hz[0] or target_hz > run_frequency_hz[-1]:
        return None
    if run_frequency_hz.size < 2:
        return (
            complex(run_transfer[0])
            if np.isclose(target_hz, run_frequency_hz[0], rtol=0.0, atol=1e-9)
            else None
        )
    log_frequency = np.log(run_frequency_hz)
    log_target = float(np.log(target_hz))
    magnitude = np.exp(np.interp(log_target, log_frequency, np.log(np.abs(run_transfer))))
    phase = np.interp(log_target, log_frequency, np.unwrap(np.angle(run_transfer)))
    return complex(magnitude * np.exp(1j * phase))


def build_learning_filter(
    theory: TheoryControlSource, static_gain_a_per_v: float,
    response: AWCurrentResponse, cutoff_hz: float, regularization: float,
) -> LearningFilter:
    """按当前周期网格重建 H、L 及其时域核。

    频响不再与任何目标网格绑定：在每个周期谐波上，从可靠扫频点之间插值
    取 H；可靠覆盖之外的学习频带自动收缩并完整报告，不插值、不外推。
    """
    count = theory.time_s.size
    dt = float(np.median(np.diff(theory.time_s)))
    frequency = np.fft.rfftfreq(count, dt)
    if cutoff_hz >= 0.5 / dt:
        raise ValueError("最高学习频率必须严格低于命令网格奈奎斯特频率")
    if static_gain_a_per_v == 0.0:
        raise ValueError("静态增益不能为零")
    order = np.argsort(np.asarray(response.frequency_hz, dtype=float))
    source_frequency = np.asarray(response.frequency_hz, dtype=float)[order]
    source_transfer = np.asarray(response.transfer_a_per_v, dtype=complex)[order]
    source_reliable = np.asarray(response.reliable, dtype=bool)[order]
    reliable_frequency = source_frequency[source_reliable]
    if reliable_frequency.size < 2:
        raise ResponseCoverageError("电流频响可靠频点不足 2 个，无法插值")
    runs = reliable_runs(source_frequency, source_transfer, source_reliable)
    requested_active = (frequency > 0) & (
        (frequency <= cutoff_hz) | np.isclose(frequency, cutoff_hz, rtol=1e-12, atol=0))
    harmonic_frequency = frequency[requested_active]
    resolved = np.zeros(harmonic_frequency.shape, dtype=complex)
    covered = np.zeros(harmonic_frequency.shape, dtype=bool)
    excluded: list[float] = []
    interpolated: list[float] = []
    for position, target in enumerate(harmonic_frequency):
        value = None
        for run_frequency, run_transfer in runs:
            value = interpolate_in_run(run_frequency, run_transfer, float(target))
            if value is not None:
                break
        if value is None:
            excluded.append(float(target))
            continue
        resolved[position] = value
        covered[position] = True
        if not np.any(np.isclose(
            target, reliable_frequency, rtol=0.0, atol=1e-9,
        )):
            interpolated.append(float(target))
    if not np.any(covered):
        raise ResponseCoverageError(
            "学习频带内没有任何被可靠扫频覆盖的周期谐波，请扩大扫频范围或提高可靠性")
    response_model = np.zeros(frequency.shape, dtype=complex)
    response_model[0] = static_gain_a_per_v
    response_model[requested_active] = np.where(covered, resolved, 0.0)
    learning_band = np.zeros(frequency.shape, dtype=bool)
    learning_band[0] = True
    learning_band[requested_active] = covered
    regularization_scale = regularization * float(np.max(np.abs(
        response_model[learning_band])))
    inverse = np.zeros(frequency.shape, dtype=complex)
    inverse[0] = 1.0 / static_gain_a_per_v
    # covered 只覆盖学习频带内的谐波，必须放回完整网格再索引。
    active_covered = np.zeros(frequency.shape, dtype=bool)
    active_covered[requested_active] = covered
    inverse[active_covered] = np.conj(response_model[active_covered]) / (
        np.abs(response_model[active_covered]) ** 2 + regularization_scale ** 2)
    effective = frequency[active_covered]
    return LearningFilter(
        response_model_a_per_v=response_model,
        inverse_kernel_frequency=inverse,
        forward_kernel=np.fft.irfft(response_model, n=count),
        inverse_kernel=np.fft.irfft(inverse, n=count),
        learning_band=learning_band,
        requested_cutoff_hz=float(cutoff_hz),
        effective_cutoff_hz=float(effective[-1]),
        effective_low_hz=float(effective[0]),
        reliable_min_hz=float(reliable_frequency[0]),
        reliable_max_hz=float(reliable_frequency[-1]),
        excluded_frequency_hz=np.asarray(excluded, dtype=float),
        interpolated_frequency_hz=np.asarray(interpolated, dtype=float),
    )


def require_matching_resistor_and_hardware_reference(
    calibration: CurrentCouplingCalibration, response: AWCurrentResponse,
) -> None:
    """两个来源必须来自同一采样电阻阻值，避免单位不一致。"""
    if not np.isclose(
        calibration.sense_resistor_ohm, response.sense_resistor_ohm,
        rtol=1e-6, atol=1e-9,
    ):
        raise ValueError(
            f"静态标定采样电阻 {calibration.sense_resistor_ohm:.9g} Ω 与"
            f"频响标定 {response.sense_resistor_ohm:.9g} Ω 不一致，不能混用"
        )


def prepare_closed_loop(root: Path, params) -> PreparedLoop:
    """无硬件预检和运行共用的数据准备入口，每次调用只加载一次来源。"""
    theory = load_theory_control(
        resolve_control_results_root(params.control_source_set), params.control_version)
    calibration = load_current_coupling_calibration(
        root, params.current_coupling_calibration_source_run)
    if params.correction_method not in {"time_domain", "response_filtered_feedback", "harmonic_jacobian"}:
        raise ValueError("未知闭环校正方法")
    response = None
    if getattr(params, "correction_method", "response_filtered_feedback") == "response_filtered_feedback":
        if not str(params.current_frequency_response_source_run).strip():
            raise ValueError("未指定电流频响标定来源（CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN），请先补选")
        response = load_aw_frequency_response(root, params.current_frequency_response_source_run)
        require_matching_resistor_and_hardware_reference(calibration, response)
    fit = fit_static_current(calibration)
    target = params.control_scale * theory.omega_ctrl_hz / calibration.slope_hz_per_a
    validate_current_power(target, calibration.sense_resistor_ohm,
                           params.sense_resistor_power_rating_w,
                           derating_fraction=params.sense_resistor_power_derating,
                           maximum_current_a=params.maximum_current_a)
    target_rms = rms(target)
    if target_rms == 0:
        raise ValueError("全零目标无法定义相对 RMS 误差")
    command_nyquist = 0.5 / float(np.median(np.diff(theory.time_s)))
    if params.error_cutoff_hz >= command_nyquist:
        raise ValueError("最高学习频率必须严格低于命令网格奈奎斯特频率")
    scope_range(target * calibration.sense_resistor_ohm, params.scope_vertical_divisions,
                params.scope_headroom_factor)
    for value in (-params.z_aw_output_vpp / 2, params.z_aw_output_vpp / 2):
        validate_safety_limit("Z_magnetic_field", value)
    if response is None:
        frequency = np.fft.rfftfreq(theory.time_s.size, float(np.median(np.diff(theory.time_s))))
        band = frequency <= float(params.error_cutoff_hz)
        if params.correction_method == "harmonic_jacobian":
            from .harmonic_feedback import select_harmonics
            selected = select_harmonics(target, frequency, params)
            band[:] = False
            band[selected] = True
        response_model = np.where(band, fit.gain_a_per_v, 0.0).astype(complex)
        inverse_frequency = np.where(band, 1.0 / fit.gain_a_per_v, 0.0).astype(complex)
        learning = LearningFilter(
            response_model, inverse_frequency,
            np.fft.irfft(response_model, n=theory.time_s.size),
            np.fft.irfft(inverse_frequency, n=theory.time_s.size), band,
            float(params.error_cutoff_hz), float(params.error_cutoff_hz), 0.0,
            float(frequency[1]), float(params.error_cutoff_hz), np.zeros(0), np.zeros(0),
        )
    else:
        learning = build_learning_filter(
            theory, fit.gain_a_per_v, response,
            params.error_cutoff_hz, params.inverse_regularization,
        )
    learning_band = learning.learning_band
    target_spectrum = np.fft.rfft(target)
    outside = np.fft.irfft(target_spectrum * (~learning_band), n=target.size)
    initial = applied_from_voltage(
        ((target - np.mean(target)) if params.correction_method == "harmonic_jacobian"
         else (target - fit.intercept_a)) / fit.gain_a_per_v,
        amplitude_vpp=params.z_aw_output_vpp, offset_v=0.0,
    )
    if learning.effective_cutoff_hz < float(params.error_cutoff_hz) - 1e-9:
        print(
            f"[Z 闭环] 学习频带按可靠覆盖收缩到 "
            f"{learning.effective_low_hz:.6g}–{learning.effective_cutoff_hz:.6g} Hz"
            f"（请求上限 {params.error_cutoff_hz:.6g} Hz，可靠覆盖 "
            f"{learning.reliable_min_hz:.6g}–{learning.reliable_max_hz:.6g} Hz，"
            f"未覆盖谐波 {learning.excluded_frequency_hz.size} 个）",
            flush=True,
        )
    return PreparedLoop(
        theory=theory,
        calibration=calibration,
        response=response,
        fit=fit,
        target_current_a=target,
        target_rms_a=target_rms,
        initial=initial,
        frequency_hz=np.fft.rfftfreq(
            theory.time_s.size, float(np.median(np.diff(theory.time_s)))),
        response_model_a_per_v=learning.response_model_a_per_v,
        inverse_kernel_frequency=learning.inverse_kernel_frequency,
        forward_kernel=learning.forward_kernel,
        inverse_kernel=learning.inverse_kernel,
        learning_band=learning_band,
        target_outside_band_rms_a=rms(outside),
        requested_cutoff_hz=learning.requested_cutoff_hz,
        effective_cutoff_hz=learning.effective_cutoff_hz,
        effective_low_hz=learning.effective_low_hz,
        excluded_frequency_hz=learning.excluded_frequency_hz,
        interpolated_frequency_hz=learning.interpolated_frequency_hz,
    )
