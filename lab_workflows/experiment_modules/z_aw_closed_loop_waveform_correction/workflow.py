"""Z 任意波实际电流闭环波形校正采集工作流。"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...current_feedback import (
    load_current_coupling_calibration,
    load_current_frequency_response,
    relative_regularization_scale,
    regularized_inverse_update,
    sense_voltage_to_current,
    spectral_nrmse,
    sha256_array,
    target_current_from_omega,
    validate_current_power,
)
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    ScopeAutoRangeState,
    configure_optimal_control_trigger,
    configure_z_optimal_control_output,
    create_run_directory,
    run_safety_shutdown,
    synchronize_connected_clocks,
    validate_z_trigger_mapping,
)
from ...steps.safety_shutdown import ShutdownAction
from ..mx_z_optimal_control_rf_sensitivity.sources import (
    AppliedControlWaveform,
    build_applied_control,
    load_theory_control,
    load_z_calibration,
    resolve_control_results_root,
)
from ..z_aw_waveform_scope_check.analysis import (
    _falling_edge_time,
    _periodic_alignment,
    _periodic_interp,
    _saturation_diagnostic,
)
from ..z_aw_waveform_scope_check.workflow import (
    _capture_with_auto_range,
    _connect_devices,
    _scope_config,
    safe_shutdown,
)
from .models import ZAWClosedLoopWaveformCorrectionParams


EXPERIMENT_ID = "z-aw-closed-loop-waveform-correction"
DATA_TYPE = "Z_AW_Closed_Loop_Waveform_Correction"
EXECUTION_MODE = "typed_workflow"
LOG_PREFIX = "[Z 闭环]"


def _log_progress(message: str) -> None:
    """向 GUI 子进程输出流和命令行实时发送闭环进度。"""
    print(f"{LOG_PREFIX} {message}", flush=True)


def _interpolate_transfer(
    frequencies_hz: np.ndarray,
    response_frequency_hz: np.ndarray,
    response: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """将扫频传递函数插值到目标任意波 FFT 频点。"""
    positive = frequencies_hz >= 0.0
    query = np.asarray(frequencies_hz, dtype=float)
    inside = (query >= response_frequency_hz[0]) & (query <= response_frequency_hz[-1])
    real = np.interp(query, response_frequency_hz, np.real(response), left=0.0, right=0.0)
    imag = np.interp(query, response_frequency_hz, np.imag(response), left=0.0, right=0.0)
    transfer = real + 1j * imag
    transfer[~positive] = 0.0
    if transfer.size and response_frequency_hz[0] > 0.0:
        # 不对 DC 做逆滤波，但安全预测使用最低可靠频点近似 DC 增益，
        # 避免把命令波形的直流功率误算为零。
        transfer[0] = response[0]
    return transfer, inside


def _applied_from_voltage(
    voltage_v: np.ndarray,
    *,
    amplitude_vpp: float,
    offset_v: float,
) -> AppliedControlWaveform:
    """把迭代后的命令电压包装为现有 DG 任意波输出契约。"""
    values = np.asarray(voltage_v, dtype=float).reshape(-1)
    if values.size < 2 or not np.all(np.isfinite(values)):
        raise ValueError("闭环命令波形无有效值")
    half_range = 0.5 * float(amplitude_vpp)
    if half_range <= 0.0:
        raise ValueError("Z_AW_OUTPUT_VPP 必须为正")
    normalized = (values - float(offset_v)) / half_range
    if np.max(np.abs(normalized)) > 1.0 + 1e-9:
        raise ValueError("闭环更新后的命令电压超出 DG 固定 Vpp/offset 范围")
    output_minimum = float(offset_v - half_range)
    output_maximum = float(offset_v + half_range)
    for value in (float(np.min(values)), float(np.max(values)), output_minimum, output_maximum):
        validate_safety_limit("Z_magnetic_field", value)
    return AppliedControlWaveform(
        voltage_v=values,
        normalized=np.clip(normalized, -1.0, 1.0),
        amplitude_vpp=float(amplitude_vpp),
        offset_v=float(offset_v),
        minimum_v=float(np.min(values)),
        maximum_v=float(np.max(values)),
        output_minimum_v=output_minimum,
        output_maximum_v=output_maximum,
        max_abs_normalized=float(np.max(np.abs(normalized))),
    )


def _predict_current_from_command(
    command_voltage: np.ndarray,
    transfer: np.ndarray,
) -> np.ndarray:
    """用已标定的电流频响估计命令波形对应的电流。"""
    command = np.asarray(command_voltage, dtype=float).reshape(-1)
    response = np.asarray(transfer, dtype=complex).reshape(-1)
    if command.size < 2 or response.size != command.size // 2 + 1:
        raise ValueError("预测电流的命令和频响长度不一致")
    return np.fft.irfft(np.fft.rfft(command) * response, n=command.size)


def _initial_preemphasis(
    baseline_command: np.ndarray,
    target_current: np.ndarray,
    transfer: np.ndarray,
    reliable_bins: np.ndarray,
    regularization: float,
) -> np.ndarray:
    """用电流频响生成可靠带宽内的初始预加重，带外保留原命令。"""
    baseline_spectrum = np.fft.rfft(np.asarray(baseline_command, dtype=float))
    target_spectrum = np.fft.rfft(np.asarray(target_current, dtype=float))
    response = np.asarray(transfer, dtype=complex)
    absolute_regularization = relative_regularization_scale(
        response,
        reliable_bins,
        regularization,
    )
    inverse = np.conj(response) / (
        np.abs(response) ** 2 + absolute_regularization**2
    )
    desired_spectrum = inverse * target_spectrum
    combined = np.where(reliable_bins, desired_spectrum, baseline_spectrum)
    return np.fft.irfft(combined, n=np.asarray(baseline_command).size)


def _time_domain_update(
    command_voltage: np.ndarray,
    target_current: np.ndarray,
    measured_current: np.ndarray,
    transfer: np.ndarray,
    reliable_bins: np.ndarray,
    *,
    damping: float,
) -> np.ndarray:
    """用相位对齐后的时域误差做保守迭代更新。

    该方法不使用复数频响的相位，只取最低可靠频点的幅值作为静态
    A/V 增益。误差先做短窗口平滑，再换算为电压修正；适合验证
    频响相位不可信时，纯时域差分能否改善波形。
    """
    command = np.asarray(command_voltage, dtype=float).reshape(-1)
    target = np.asarray(target_current, dtype=float).reshape(-1)
    measured = np.asarray(measured_current, dtype=float).reshape(-1)
    response = np.asarray(transfer, dtype=complex).reshape(-1)
    reliable = np.asarray(reliable_bins, dtype=bool).reshape(-1)
    if not (command.size == target.size == measured.size and command.size >= 2):
        raise ValueError("时域更新输入数组长度不足或不一致")
    if response.size != reliable.size or response.size != command.size // 2 + 1:
        raise ValueError("时域更新频响长度不一致")
    if not np.isfinite(damping) or not 0.0 < damping <= 1.0:
        raise ValueError("ITERATION_DAMPING 必须在 (0, 1] 内")
    valid = reliable & np.isfinite(response) & (np.abs(response) > 0.0)
    if not np.any(valid):
        raise ValueError("时域更新没有可靠电流增益")
    first = int(np.flatnonzero(valid)[0])
    gain_a_per_v = float(np.abs(response[first]))
    if not np.isfinite(gain_a_per_v) or gain_a_per_v <= 0.0:
        raise ValueError("最低可靠频点的电流增益无效")
    error = target - measured
    if not np.all(np.isfinite(error)):
        raise ValueError("时域误差包含非有限值")
    # 仅抑制采样噪声，不引入额外相位；窗口足够短，不改变 250 Hz 周期形状。
    window = min(9, error.size if error.size % 2 else error.size - 1)
    if window < 3:
        smoothed = error
    else:
        kernel = np.full(window, 1.0 / window, dtype=float)
        smoothed = np.convolve(error, kernel, mode="same")
    correction_voltage = smoothed / gain_a_per_v
    return command + float(damping) * correction_voltage


def _waveform_metrics(target: np.ndarray, measured: np.ndarray) -> dict[str, float]:
    error = np.asarray(target, dtype=float) - np.asarray(measured, dtype=float)
    target_std = max(float(np.std(target)), 1e-15)
    correlation = float(np.corrcoef(target, measured)[0, 1])
    return {
        "shape_nrmse": float(np.sqrt(np.mean(error * error)) / target_std),
        "shape_correlation": correlation,
        "spectral_nrmse": spectral_nrmse(target, measured),
    }


def _should_rollback(
    selection_error: float,
    best_error: float,
    iteration_count: int,
    maximum_increase_fraction: float,
) -> bool:
    """判断当前 hold-out 误差是否已超过允许恶化幅度。"""
    return bool(
        iteration_count > 1
        and selection_error > best_error * (1.0 + maximum_increase_fraction)
    )


def _save_iteration_frame(path: Path, frame: dict[str, Any]) -> None:
    np.savez(
        path,
        time_s=np.asarray(frame["time_s"], dtype=float),
        measured_voltage_v=np.asarray(frame["measured_voltage_v"], dtype=float),
        trigger_time_s=np.asarray(frame["trigger_time_s"], dtype=float),
        trigger_voltage_v=np.asarray(frame["trigger_voltage_v"], dtype=float),
        actual_rate_sa_s=np.float64(frame["actual_rate_sa_s"]),
        scale_used_v_div=np.float64(frame["scale_used_v_div"]),
        offset_used_v=np.float64(frame["offset_used_v"]),
    )


def _trigger_relative_time(
    frame: dict[str, Any],
    trigger_level_v: float,
) -> np.ndarray:
    """以 CH4 下降沿为零点返回 CH3 的相对时间轴。"""
    trigger_edge_time_s = _falling_edge_time(
        np.asarray(frame["trigger_time_s"], dtype=float),
        np.asarray(frame["trigger_voltage_v"], dtype=float),
        trigger_level_v,
    )
    return np.asarray(frame["time_s"], dtype=float) - trigger_edge_time_s


def _write_corrected_waveform(
    results_dir: Path,
    *,
    theory: Any,
    target_omega_ctrl_hz: np.ndarray,
    target_current: np.ndarray,
    command_voltage: np.ndarray,
    applied: AppliedControlWaveform,
    current_calibration: Any,
    frequency_response_run: str,
    frequency_response_sha256: str,
    summary: list[dict[str, Any]],
) -> list[str]:
    results_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        results_dir / "corrected_control_waveform.npz",
        time_s=np.asarray(theory.time_s, dtype=float),
        omega_ctrl_hz=np.asarray(target_omega_ctrl_hz, dtype=float),
        target_current_a=np.asarray(target_current, dtype=float),
        normalized=np.asarray(applied.normalized, dtype=float),
        voltage_v=np.asarray(command_voltage, dtype=float),
        repeat_frequency_hz=np.float64(theory.repeat_frequency_hz),
        amplitude_vpp=np.float64(applied.amplitude_vpp),
        offset_v=np.float64(applied.offset_v),
        coupling_calibration_run=np.asarray(current_calibration.run_name),
        coupling_calibration_sha256=np.asarray(current_calibration.analysis_sha256),
        frequency_response_run=np.asarray(frequency_response_run),
        frequency_response_sha256=np.asarray(frequency_response_sha256),
        iteration_summary_json=np.asarray(json.dumps(summary, ensure_ascii=False, sort_keys=True)),
    )
    with (results_dir / "corrected_control_waveform.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "omega_ctrl_hz", "target_current_a", "command_voltage_v", "normalized"])
        for row in zip(
            theory.time_s,
            target_omega_ctrl_hz,
            target_current,
            command_voltage,
            applied.normalized,
            strict=True,
        ):
            writer.writerow([float(value) for value in row])
    (results_dir / "iteration_summary.yaml").write_text(
        yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return [
        "results/corrected_control_waveform.npz",
        "results/corrected_control_waveform.csv",
        "results/iteration_summary.yaml",
    ]


def run(params: ZAWClosedLoopWaveformCorrectionParams) -> Path:
    """执行同一设备会话内的实际电流闭环迭代。"""
    root = find_project_root()
    mapping = load_mapping(root)
    theory = load_theory_control(
        resolve_control_results_root(params.control_source_set),
        params.control_version,
    )
    command_calibration = load_z_calibration(root, params.z_calibration_source_run)
    current_calibration = load_current_coupling_calibration(
        root, params.current_coupling_calibration_source_run
    )
    if abs(current_calibration.sense_resistor_ohm - params.sense_resistor_ohm) > max(1e-12, params.sense_resistor_ohm * 1e-6):
        raise ValueError("闭环参数中的采样电阻与电流耦合标定不一致")
    frequency_response = load_current_frequency_response(
        root,
        params.current_frequency_response_source_run,
    )
    if not np.isclose(
        frequency_response.sense_resistor_ohm,
        params.sense_resistor_ohm,
        rtol=1e-6,
        atol=1e-12,
    ):
        raise ValueError("闭环参数中的采样电阻与实际电流频响不一致")
    response_frequency = frequency_response.frequency_hz
    response = frequency_response.transfer_a_per_v
    response_sha256 = frequency_response.response_sha256
    target_omega_ctrl_hz = params.control_scale * theory.omega_ctrl_hz
    target_current = target_current_from_omega(target_omega_ctrl_hz, current_calibration)
    safety = validate_current_power(
        target_current,
        params.sense_resistor_ohm,
        params.sense_resistor_power_rating_w,
        derating_fraction=params.sense_resistor_power_derating,
        maximum_current_a=params.maximum_current_a,
    )
    baseline_applied = build_applied_control(
        theory,
        command_calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    command_voltage = np.asarray(baseline_applied.voltage_v, dtype=float).copy()
    transfer, reliable_bins = _interpolate_transfer(
        np.fft.rfftfreq(command_voltage.size, d=float(np.median(np.diff(theory.time_s)))),
        response_frequency,
        response,
    )
    reliable_bins &= np.abs(transfer) > 0.0
    if np.count_nonzero(reliable_bins) < 2:
        raise ValueError("目标波形在实际电流频响可靠带宽内没有足够频点")
    absolute_regularization = relative_regularization_scale(
        transfer,
        reliable_bins,
        params.inverse_regularization,
    )
    if params.correction_method == "frequency_domain":
        command_voltage = _initial_preemphasis(
            command_voltage,
            target_current,
            transfer,
            reliable_bins,
            params.inverse_regularization,
        )
    applied = _applied_from_voltage(
        command_voltage,
        amplitude_vpp=params.z_aw_output_vpp,
        offset_v=params.z_aw_output_offset_v,
    )
    predicted_initial_current = _predict_current_from_command(command_voltage, transfer)
    predicted_initial_safety = validate_current_power(
        predicted_initial_current,
        params.sense_resistor_ohm,
        params.sense_resistor_power_rating_w,
        derating_fraction=params.sense_resistor_power_derating,
        maximum_current_a=params.maximum_current_a,
    )
    _log_progress(
        "初始波形计算完成: "
        f"静态标定基准 {baseline_applied.minimum_v:.6g} 至 "
        f"{baseline_applied.maximum_v:.6g} V；"
        f"校正方法 {params.correction_method}，"
        f"相对正则化 {params.inverse_regularization:.6g}，"
        f"绝对尺度 {absolute_regularization:.6g} A/V"
    )
    _log_progress(
        "动态预加重结果: "
        f"命令范围 {applied.minimum_v:.6g} 至 {applied.maximum_v:.6g} V，"
        f"DG 归一化峰值 {applied.max_abs_normalized:.6g}，"
        f"预测电流峰值 {predicted_initial_safety['peak_current_a'] * 1e3:.6g} mA，"
        f"RMS {predicted_initial_safety['rms_current_a'] * 1e3:.6g} mA"
    )
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    iterations: list[dict[str, Any]] = []
    completion_status = "failed"
    failure_reason: str | None = None
    best_error = float("inf")
    best_command = command_voltage.copy()
    best_applied = applied
    best_iteration_index: int | None = None
    stop_reason = "达到最大迭代轮数"
    _log_progress(
        f"运行目录已创建: {run_dir.root}；计划执行 {params.max_iterations} 轮，"
        f"每轮 {params.iteration_repeats} 次反馈采集和 {params.holdout_repeats} 次 hold-out"
    )
    try:
        _log_progress("正在连接 DG4000 和 SDS")
        devices, channels = _connect_devices(mapping, session)
        channels["trigger"] = validate_z_trigger_mapping(mapping)
        clocks = synchronize_connected_clocks(
            {"z_control": devices["z_control"]},
            mapping,
            {"z_control": "Z_magnetic_field"},
            settle_s=0.2,
        )
        _log_progress("时钟同步完成，正在保存运行快照并配置 SDS")
        run_dir.update_config(
            experiment_id=EXPERIMENT_ID,
            data_type=DATA_TYPE,
            execution_mode=EXECUTION_MODE,
            measurement_mode="closed_loop_current_feedback",
            current_coupling_calibration={
                "source_run": current_calibration.run_name,
                "analysis_sha256": current_calibration.analysis_sha256,
                "slope_hz_per_a": current_calibration.slope_hz_per_a,
                "sense_resistor_ohm": current_calibration.sense_resistor_ohm,
            },
            current_frequency_response={
                "source_run": params.current_frequency_response_source_run,
                "frequency_response_sha256": response_sha256,
                "regularization_relative": params.inverse_regularization,
                "regularization_absolute_a_per_v": absolute_regularization,
            },
            source_hashes={
                "theory_waveform_sha256": theory.waveform_sha256,
                "theory_parameter_sha256": theory.parameter_sha256,
                "command_calibration_sha256": command_calibration.analysis_sha256,
            },
            target_current_safety=safety,
            initial_command={
                "baseline_min_v": baseline_applied.minimum_v,
                "baseline_max_v": baseline_applied.maximum_v,
                "preemphasized_min_v": applied.minimum_v,
                "preemphasized_max_v": applied.maximum_v,
                "max_abs_normalized": applied.max_abs_normalized,
                "predicted_current_safety": predicted_initial_safety,
            },
            clock_sources=clocks,
        )
        config, scope_snapshot = _scope_config(
            params,
            theory,
            measured_scale_v_div=params.scope_initial_scale_v_div,
            measured_offset_v=0.0,
        )
        devices["acquirer"].apply_config(config)
        scope = devices["scope"]
        scope_snapshot.update(
            {
                "actual_sample_rate_sa_s": float(scope.get_sampling_rate()),
                "actual_points": int(scope.get_actual_points()),
            }
        )
        auto_range = ScopeAutoRangeState(
            scale_v_div=float(scope.get_channel_scale(params.scope_measured_channel)),
            offset_v=float(scope.get_channel_offset(params.scope_measured_channel)),
        )
        _log_progress(
            "SDS 配置完成: "
            f"{scope_snapshot['actual_points']} 点，"
            f"{scope_snapshot['actual_sample_rate_sa_s']:.6g} Sa/s，开始闭环迭代"
        )
        for iteration_index in range(params.max_iterations):
            check_cancelled()
            iteration_label = f"轮次 {iteration_index + 1}/{params.max_iterations}"
            applied = _applied_from_voltage(
                command_voltage,
                amplitude_vpp=params.z_aw_output_vpp,
                offset_v=params.z_aw_output_offset_v,
            )
            _log_progress(
                f"{iteration_label}: 正在配置任意波，"
                f"命令范围 {applied.minimum_v:.6g} 至 {applied.maximum_v:.6g} V"
            )
            configure_optimal_control_trigger(
                params, devices["z_control"], channels["trigger"], output=False
            )
            configure_z_optimal_control_output(
                params, devices["z_control"], channels["z_field"], theory, applied
            )
            devices["z_control"].set_output(True, channel=channels["trigger"])
            iteration_dir = run_dir.raw / f"iteration_{iteration_index:03d}"
            iteration_dir.mkdir(parents=True, exist_ok=True)
            measured_cycles: list[np.ndarray] = []
            first_alignment: dict[str, Any] | None = None
            for repeat_index in range(params.iteration_repeats):
                capture_label = (
                    f"{iteration_label}，反馈采集 "
                    f"{repeat_index + 1}/{params.iteration_repeats}"
                )
                _log_progress(f"{capture_label}: 等待 SDS CH4 下降沿")
                frame = _capture_with_auto_range(params, devices, config, auto_range)
                frame_path = iteration_dir / f"scope_capture_{repeat_index:03d}.npz"
                _save_iteration_frame(frame_path, frame)
                time_relative = _trigger_relative_time(
                    frame, params.scope_trigger_level_v
                )
                measured_current = sense_voltage_to_current(
                    frame["measured_voltage_v"], params.sense_resistor_ohm
                )
                saturation = _saturation_diagnostic(
                    frame["measured_voltage_v"],
                    scale_v_div=frame.get("scale_used_v_div"),
                    offset_v=frame.get("offset_used_v", 0.0),
                )
                if saturation["saturation_detected"]:
                    raise RuntimeError("SDS CH3 采样电阻电压检测到饱和平顶")
                alignment = _periodic_alignment(
                    time_relative,
                    measured_current,
                    {
                        "time_s": theory.time_s,
                        "voltage_v": target_current,
                        "repeat_frequency_hz": theory.repeat_frequency_hz,
                    },
                    scale_v_div=frame.get("scale_used_v_div"),
                    offset_v=frame.get("offset_used_v", 0.0),
                )
                sample = _periodic_interp(
                    np.asarray(theory.time_s, dtype=float) + alignment["best_delay_s"],
                    time_relative,
                    measured_current,
                    1.0 / theory.repeat_frequency_hz,
                )
                measured_cycles.append(sample)
                first_alignment = alignment
                _log_progress(
                    f"{capture_label}: 完成，{len(frame['time_s'])} 点，"
                    f"对齐延迟 {alignment['best_delay_s'] * 1e6:.3f} us，"
                    f"CH3 量程 {frame['scale_used_v_div']:.6g} V/div"
                )
            measured_current = np.mean(np.vstack(measured_cycles), axis=0)
            validate_current_power(
                measured_current,
                params.sense_resistor_ohm,
                params.sense_resistor_power_rating_w,
                derating_fraction=params.sense_resistor_power_derating,
                maximum_current_a=params.maximum_current_a,
            )
            metrics = _waveform_metrics(target_current, measured_current)
            iteration_record = {
                "iteration": iteration_index,
                **metrics,
                "best_delay_s": float(first_alignment["best_delay_s"] if first_alignment else np.nan),
                "command_min_v": float(np.min(command_voltage)),
                "command_max_v": float(np.max(command_voltage)),
                "measured_peak_current_a": float(np.max(np.abs(measured_current))),
                "measured_rms_current_a": float(np.sqrt(np.mean(measured_current * measured_current))),
            }
            holdout_cycles: list[np.ndarray] = []
            for holdout_index in range(params.holdout_repeats):
                holdout_label = (
                    f"{iteration_label}，hold-out "
                    f"{holdout_index + 1}/{params.holdout_repeats}"
                )
                _log_progress(f"{holdout_label}: 等待 SDS CH4 下降沿")
                holdout_frame = _capture_with_auto_range(params, devices, config, auto_range)
                _save_iteration_frame(
                    iteration_dir / f"holdout_capture_{holdout_index:03d}.npz",
                    holdout_frame,
                )
                holdout_time = _trigger_relative_time(
                    holdout_frame, params.scope_trigger_level_v
                )
                holdout_current = sense_voltage_to_current(
                    holdout_frame["measured_voltage_v"], params.sense_resistor_ohm
                )
                holdout_saturation = _saturation_diagnostic(
                    holdout_frame["measured_voltage_v"],
                    scale_v_div=holdout_frame.get("scale_used_v_div"),
                    offset_v=holdout_frame.get("offset_used_v", 0.0),
                )
                if holdout_saturation["saturation_detected"]:
                    raise RuntimeError("SDS CH3 hold-out 采样检测到饱和平顶")
                holdout_alignment = _periodic_alignment(
                    holdout_time,
                    holdout_current,
                    {
                        "time_s": theory.time_s,
                        "voltage_v": target_current,
                        "repeat_frequency_hz": theory.repeat_frequency_hz,
                    },
                    scale_v_div=holdout_frame.get("scale_used_v_div"),
                    offset_v=holdout_frame.get("offset_used_v", 0.0),
                )
                holdout_cycles.append(
                    _periodic_interp(
                        np.asarray(theory.time_s) + holdout_alignment["best_delay_s"],
                        holdout_time,
                        holdout_current,
                        1.0 / theory.repeat_frequency_hz,
                    )
                )
                _log_progress(
                    f"{holdout_label}: 完成，{len(holdout_frame['time_s'])} 点，"
                    f"对齐延迟 {holdout_alignment['best_delay_s'] * 1e6:.3f} us"
                )
            holdout_current_mean = np.mean(np.vstack(holdout_cycles), axis=0)
            validate_current_power(
                holdout_current_mean,
                params.sense_resistor_ohm,
                params.sense_resistor_power_rating_w,
                derating_fraction=params.sense_resistor_power_derating,
                maximum_current_a=params.maximum_current_a,
            )
            iteration_record["holdout"] = _waveform_metrics(target_current, holdout_current_mean)
            iteration_record["command_sha256"] = sha256_array(command_voltage)
            normalized_error = metrics["shape_nrmse"]
            selection_error = iteration_record["holdout"]["shape_nrmse"]
            error = target_current - measured_current
            iterations.append(iteration_record)
            np.savez(
                iteration_dir / "feedback.npz",
                time_s=np.asarray(theory.time_s, dtype=float),
                target_current_a=target_current,
                measured_current_a=measured_current,
                holdout_current_a=holdout_current_mean,
                error_current_a=error,
                command_voltage_v=command_voltage,
            )
            _log_progress(
                f"{iteration_label}: 反馈 NRMSE={normalized_error:.6g}，"
                f"相关系数={metrics['shape_correlation']:.6g}，"
                f"hold-out NRMSE={selection_error:.6g}"
            )
            if selection_error < best_error:
                best_error = selection_error
                best_command = command_voltage.copy()
                best_applied = applied
                best_iteration_index = iteration_index
                _log_progress(
                    f"{iteration_label}: 刷新最佳 hold-out NRMSE={best_error:.6g}"
                )
            if (
                normalized_error <= params.target_shape_nrmse
                and selection_error <= params.target_shape_nrmse
            ):
                stop_reason = (
                    f"反馈和 hold-out 均达到目标 NRMSE "
                    f"{params.target_shape_nrmse:.6g}"
                )
                _log_progress(f"{iteration_label}: {stop_reason}，停止迭代")
                break
            if _should_rollback(
                selection_error,
                best_error,
                len(iterations),
                params.maximum_error_increase_fraction,
            ):
                command_voltage = best_command.copy()
                stop_reason = (
                    f"hold-out 误差恶化至 {selection_error:.6g}，"
                    f"超过最佳值 {best_error:.6g} 的允许增幅"
                )
                _log_progress(f"{iteration_label}: {stop_reason}，回退最佳波形")
                break
            if params.correction_method == "time_domain":
                next_command = _time_domain_update(
                    command_voltage,
                    target_current,
                    measured_current,
                    transfer,
                    reliable_bins,
                    damping=params.iteration_damping,
                )
            else:
                next_command = regularized_inverse_update(
                    command_voltage,
                    target_current,
                    measured_current,
                    transfer,
                    damping=params.iteration_damping,
                    regularization=params.inverse_regularization,
                    reliable_bins=reliable_bins,
                )
            try:
                _applied_from_voltage(
                    next_command,
                    amplitude_vpp=params.z_aw_output_vpp,
                    offset_v=params.z_aw_output_offset_v,
                )
                predicted_current = _predict_current_from_command(next_command, transfer)
                predicted_safety = validate_current_power(
                    predicted_current,
                    params.sense_resistor_ohm,
                    params.sense_resistor_power_rating_w,
                    derating_fraction=params.sense_resistor_power_derating,
                    maximum_current_a=params.maximum_current_a,
                )
            except (ValueError, RuntimeError) as exc:
                iteration_record["update_rejected"] = str(exc)
                command_voltage = best_command.copy()
                stop_reason = f"下一轮更新被安全检查拒绝: {exc}"
                _log_progress(f"{iteration_label}: {stop_reason}，回退最佳波形")
                break
            iteration_record["predicted_next_current_peak_a"] = predicted_safety["peak_current_a"]
            iteration_record["predicted_next_current_rms_a"] = predicted_safety["rms_current_a"]
            _log_progress(
                f"{iteration_label}: 下一轮更新已通过安全检查，"
                f"预测峰值电流 {predicted_safety['peak_current_a'] * 1e3:.6g} mA，"
                f"RMS {predicted_safety['rms_current_a'] * 1e3:.6g} mA"
            )
            command_voltage = next_command
        best_iteration_text = (
            str(best_iteration_index + 1)
            if best_iteration_index is not None
            else "未产生有效轮次"
        )
        _log_progress(
            f"闭环停止: {stop_reason}；正在冻结最佳轮次 {best_iteration_text}，"
            f"hold-out NRMSE={best_error:.6g}"
        )
        files = _write_corrected_waveform(
            run_dir.results,
            theory=theory,
            target_omega_ctrl_hz=target_omega_ctrl_hz,
            target_current=target_current,
            command_voltage=best_command,
            applied=best_applied,
            current_calibration=current_calibration,
            frequency_response_run=params.current_frequency_response_source_run,
            frequency_response_sha256=response_sha256,
            summary=iterations,
        )
        final_safety = validate_current_power(
            _predict_current_from_command(best_command, transfer),
            params.sense_resistor_ohm,
            params.sense_resistor_power_rating_w,
            derating_fraction=params.sense_resistor_power_derating,
            maximum_current_a=params.maximum_current_a,
        )
        run_dir.update_config(
            completion_status="completed",
            failure_reason=None,
            data_files=files,
            iterations=iterations,
            final_holdout_shape_nrmse=best_error,
            final_predicted_current_safety=final_safety,
            command_waveform_sha256=sha256_array(best_command),
        )
        completion_status = "completed"
        _log_progress(f"实验完成，校正波形已保存: {run_dir.results}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        _log_progress(f"实验失败: {exc}")
        raise
    finally:
        report = safe_shutdown(devices, channels)
        if report.errors:
            _log_progress(f"安全关闭存在错误: {'；'.join(report.errors)}")
        else:
            _log_progress("安全关闭完成，DG 输出已关闭且设备已断开")
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=report.to_dict(),
            )


def main() -> int:
    run(load_runtime_params(ZAWClosedLoopWaveformCorrectionParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
