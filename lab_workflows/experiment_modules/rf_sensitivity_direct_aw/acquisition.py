"""有符号 RF 响应扫描、带宽扫频、噪声 DAQ 与响应模式恢复。"""
from dataclasses import replace
import math
import numpy as np
from lockin_amplifier import DAQConfig, daq, demod, result_rate, acquisition_metadata
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import set_temperature_switch
from ._helpers import hf2_snapshot, wait, buffered_acquisition, save_npz
from .excitation import arm_z
from . import phase
from .excitation import set_z_rf


def acquire_response(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    z_positive_phase, xy_phase = ctx.z_positive_phase, ctx.xy_phase
    amplitudes = np.linspace(params.rf_amp_start, params.rf_amp_stop, params.rf_amp_points)
    means = {key: [] for key in ("x", "y", "r")}
    stds = {key: [] for key in ("x", "y", "r")}
    series = {key: [] for key in ("x", "y", "r")}
    thetas = []
    phases = []
    response_rate = state["actual_rates"]["demod3_response_Sa_s"]
    response_daq = DAQConfig(device=hf2.device_id, duration=params.scan_point_duration_s, grid_cols=max(8, int(response_rate * params.scan_point_duration_s)), grid_rows=1, grid_mode=2, signal_paths=["sample.r", "sample.x", "sample.y"])
    state["response_acquisition"] = {"mode": "daq_duration", "duration_s": params.scan_point_duration_s, "actual_rate_Sa_s": response_rate, "temperature_group_size": params.temp_group_size}
    directory.update_config(**state)
    # 幅度扫描与相位扫描节奏一致：按温控分组关闭温控、组间恢复，每点定长 DAQ 采集，整组恢复温控后落盘。
    with buffered_acquisition(ctx) as pending:
        for index, amplitude in enumerate(amplitudes):
            check_cancelled()
            phase = set_z_rf(ctx, float(amplitude), phase_base=z_positive_phase)
            if index % params.temp_group_size == 0:
                set_temperature_switch(signals, False, channel=channels["temp"])
            wait(params.system_settle_s)
            results = daq.acquire_data(hf2, response_daq, demod_idx=params.demod3_idx, actual_rate=response_rate, requested_rate=params.demod3_rate, timeout=params.scan_point_duration_s + 10, cancellation_check=check_cancelled, strict_cleanup=True)
            capture_rate = result_rate(results, response_rate)
            metadata = acquisition_metadata(results)
            state["response_acquisition"].update(metadata)
            by_name = {result.signal_name: np.asarray(result.values, dtype=float) for result in results}
            if set(by_name) != {"sample.r", "sample.x", "sample.y"} or not all(np.all(np.isfinite(values)) for values in by_name.values()):
                raise RuntimeError(f"幅度扫描第 {index + 1} 点 DAQ 未返回有效 R/X/Y 数据")
            for key in ("x", "y", "r"):
                values = by_name[f"sample.{key}"]
                means[key].append(float(values.mean()))
                stds[key].append(float(values.std(ddof=1)) if values.size > 1 else 0.0)
                series[key].append(values)
            thetas.append(float(np.degrees(np.angle(means["x"][-1] + 1j * means["y"][-1]))))
            phases.append(phase)
            pending.add(save_npz,
                directory.raw / "response_data.npz",
                amplitudes_V=amplitudes[:index + 1], amplitudes_nT=amplitudes[:index + 1] * params.z_v_to_nt,
                demod3_x_V=means["x"], demod3_y_V=means["y"], demod3_r_V=means["r"],
                demod3_x_std_V=stds["x"], demod3_y_std_V=stds["y"], demod3_r_std_V=stds["r"],
                demod3_x_series_V=np.asarray(series["x"]), demod3_y_series_V=np.asarray(series["y"]), demod3_r_series_V=np.asarray(series["r"]),
                demod3_theta_deg=thetas, z_burst_phase_deg=phases, z_rf_freq_Hz=params.z_rf_freq, z_v_to_nT=params.z_v_to_nt,
                xy_ctrl_phase_deg=xy_phase.final_phase_deg, xy_ctrl_quad_deg=params.xy_ctrl_quad,
                duration_s=params.scan_point_duration_s, demod_idx=params.demod3_idx,
                **{**metadata, "actual_rate_Sa_s": capture_rate}, time_s=results[0].time,
            )
            state["response_points_completed"] = index + 1
            if "raw/response_data.npz" not in state["data_files"]:
                state["data_files"].append("raw/response_data.npz")
            pending.add(directory.update_config, **state)
            if (index + 1) % params.temp_group_size == 0 or index + 1 == len(amplitudes):
                set_temperature_switch(signals, True, channel=channels["temp"])
                wait(params.temp_switch_on_lag_s)
                pending.flush()
            ctx.progress("response", index + 1)


def acquire_bandwidth(ctx):
    """噪声采集后以校准幅度扫频，取定长 R 时序均值测带宽。"""
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    z_positive_phase = ctx.z_positive_phase
    frequencies = np.linspace(params.bw_freq_start, params.bw_freq_stop, params.bw_freq_points)
    means = {key: [] for key in ("x", "y", "r")}
    stds = {key: [] for key in ("x", "y", "r")}
    series = {key: [] for key in ("x", "y", "r")}
    point_rates = []
    response_rate = float(state["hf2_bandwidth_snapshot"][f"{hf2.demod_path(params.demod3_idx)}/rate"])
    if not math.isfinite(response_rate) or response_rate <= 0:
        raise RuntimeError("实际带宽扫频采样率无效")
    state["actual_rates"]["demod3_bandwidth_Sa_s"] = response_rate
    bandwidth_daq = DAQConfig(device=hf2.device_id, duration=params.scan_point_duration_s, grid_cols=max(8, int(response_rate * params.scan_point_duration_s)), grid_rows=1, grid_mode=2, signal_paths=["sample.r", "sample.x", "sample.y"])
    state["bandwidth_acquisition"] = {"mode": "daq_duration", "frequency_start_Hz": params.bw_freq_start, "frequency_stop_Hz": params.bw_freq_stop,
        "points": params.bw_freq_points, "amplitude_vpp": params.z_rf_amplitude_init, "duration_s": params.scan_point_duration_s,
        "actual_rate_Sa_s": response_rate, "temperature_group_size": params.temp_group_size, "response_component": "R"}
    directory.update_config(**state)
    # 与 A11 同节奏：按温控分组关闭温控、组间恢复，每点同步改频重臂后采集。
    with buffered_acquisition(ctx) as pending:
        failure = None
        try:
            for index, frequency in enumerate(frequencies):
                check_cancelled()
                phase.set_rf_frequency(ctx, float(frequency))
                set_z_rf(ctx, params.z_rf_amplitude_init, phase_base=z_positive_phase)
                if index % params.temp_group_size == 0:
                    set_temperature_switch(signals, False, channel=channels["temp"])
                wait(params.system_settle_s)
                results = daq.acquire_data(hf2, bandwidth_daq, demod_idx=params.demod3_idx, actual_rate=response_rate, requested_rate=params.demod3_rate, timeout=params.scan_point_duration_s + 10, cancellation_check=check_cancelled, strict_cleanup=True)
                capture_rate = result_rate(results, response_rate)
                metadata = acquisition_metadata(results)
                state["bandwidth_acquisition"].update(metadata)
                state["actual_rates"]["demod3_bandwidth_Sa_s"] = capture_rate
                by_name = {result.signal_name: np.asarray(result.values, dtype=float) for result in results}
                if set(by_name) != {"sample.r", "sample.x", "sample.y"} or not all(values.ndim == 1 and values.size >= 2 and np.all(np.isfinite(values)) for values in by_name.values()):
                    raise RuntimeError(f"带宽扫频第 {index + 1} 点 DAQ 未返回有效 R/X/Y 数据")
                point_rates.append(capture_rate)
                for key in ("x", "y", "r"):
                    values = by_name[f"sample.{key}"]
                    means[key].append(float(values.mean()))
                    stds[key].append(float(values.std(ddof=1)) if values.size > 1 else 0.0)
                    series[key].append(values)
                pending.add(save_npz,
                    directory.raw / "bandwidth_data.npz",
                    frequency_Hz=frequencies[:index + 1],
                    demod3_x_V=means["x"], demod3_y_V=means["y"], demod3_r_V=means["r"],
                    demod3_x_std_V=stds["x"], demod3_y_std_V=stds["y"], demod3_r_std_V=stds["r"],
                    demod3_x_series_V=np.asarray(series["x"]), demod3_y_series_V=np.asarray(series["y"]), demod3_r_series_V=np.asarray(series["r"]),
                    z_rf_amplitude_vpp=params.z_rf_amplitude_init, z_burst_phase_deg=z_positive_phase,
                    z_rf_freq_Hz=params.z_rf_freq, duration_s=params.scan_point_duration_s, demod_idx=params.demod3_idx,
                    point_actual_rate_Sa_s=point_rates,
                    **{**metadata, "actual_rate_Sa_s": capture_rate}, time_s=results[0].time,
                )
                state["bandwidth_points_completed"] = index + 1
                if "raw/bandwidth_data.npz" not in state["data_files"]:
                    state["data_files"].append("raw/bandwidth_data.npz")
                pending.add(directory.update_config, **state)
                if (index + 1) % params.temp_group_size == 0 or index + 1 == len(frequencies):
                    set_temperature_switch(signals, True, channel=channels["temp"])
                    wait(params.temp_switch_on_lag_s)
                    pending.flush()
                report_runtime_progress("bandwidth", f"R 带宽扫频已完成 {index + 1}/{len(frequencies)} 点", 90 + 4 * (index + 1) / len(frequencies))
        except BaseException as exc:
            failure = exc
            raise
        finally:
            # 独立尝试每项恢复，清理错误不覆盖原始采集错误。
            errors = []
            for label, restore in (
                ("恢复 RF 频率", lambda: phase.set_rf_frequency(ctx, params.z_rf_freq)),
                ("关闭 Z RF", lambda: set_z_rf(ctx, 0.0)),
                ("恢复温控", lambda: set_temperature_switch(signals, True, channel=channels["temp"])),
            ):
                try:
                    restore()
                except Exception as exc:
                    errors.append(f"{label}：{exc}")
            if errors:
                state["bandwidth_restore_errors"] = errors
                if failure is None:
                    raise RuntimeError("带宽扫频恢复失败：" + "；".join(errors))
            else:
                ctx.outputs.update(Z="OFF", 温控="ON")


def acquire_noise(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    response_config = ctx.response_config
    set_z_rf(ctx, 0.0)
    ctx.demod_mode = "noise"
    actual_rate = float(demod.configure_demodulator(hf2, replace(response_config, rate=params.noise_rate, time_constant=params.demod3_noise_tc, order=params.demod3_noise_order), for_acquisition=True))
    if not math.isfinite(actual_rate) or actual_rate <= 0 or actual_rate * params.noise_duration < 2:
        raise RuntimeError("实际噪声采样率或记录长度无效")
    state["actual_rates"]["demod3_noise_Sa_s"] = actual_rate
    state["hf2_noise_snapshot"] = hf2_snapshot(hf2, params)
    record_stage_settings(ctx, "noise")
    directory.update_config(**state)
    daq_config = DAQConfig(device=hf2.device_id, duration=params.noise_duration, grid_cols=int(actual_rate * params.noise_duration), grid_rows=1, grid_mode=2, signal_paths=["sample.x"])
    for index in range(params.noise_n_avg):
        check_cancelled()
        with buffered_acquisition(ctx) as pending:
            set_temperature_switch(signals, False, channel=channels["temp"])
            wait(params.system_settle_s)
            results = daq.acquire_data(hf2, daq_config, demod_idx=params.demod3_idx, actual_rate=actual_rate, requested_rate=params.noise_rate, timeout=params.noise_duration + 10, cancellation_check=check_cancelled, strict_cleanup=True)
            capture_rate = result_rate(results, actual_rate)
            metadata = acquisition_metadata(results)
            state["noise_acquisition"] = metadata
            state["actual_rates"]["demod3_noise_Sa_s"] = capture_rate
            name = f"noise_D3_{index:04d}.npz"
            pending.add(np.savez, directory.raw / name, noise_x_V=results[0].values, time_s=results[0].time, **{**metadata, "actual_rate_Sa_s": capture_rate}, demod_idx=params.demod3_idx, signal_path="sample.x", noise_duration_s=params.noise_duration,
                     z_rf_freq_Hz=state["stage_frequencies"]["noise"]["rf_frequency_Hz"],
                     demod3_freq_Hz=state["stage_frequencies"]["noise"]["demod3_frequency_Hz"])
            state["data_files"].append(f"raw/{name}")
            state["noise_records_completed"] = index + 1
            pending.add(directory.update_config, **state)
            if not results or results[0].values.size < 2 or not np.all(np.isfinite(results[0].values)):
                raise RuntimeError("噪声 DAQ 未返回有效 X 数据")
        wait(params.temp_switch_on_lag_s)
        ctx.progress("noise", params.rf_amp_points + index + 1)


def restore_response(ctx):
    if ctx.response_config is not None:
        demod.configure_demodulator(ctx.devices["hf2"], ctx.response_config, for_acquisition=True)
        ctx.demod_mode = "response"


def record_response_settings(ctx):
    """记录实际设置；仪器支持的离散值不作为一致性门槛。"""
    if ctx.demod_mode != "response" or ctx.response_config is None:
        raise ValueError("解调器模式已改变，请重新执行 A10 回环与响应准备及后续校相")
    hf2 = ctx.devices["hf2"]
    snapshot = hf2_snapshot(hf2, ctx.params)
    ctx.state["hf2_response_snapshot"] = snapshot
    ctx.state["actual_rates"]["demod3_response_Sa_s"] = snapshot[f"{hf2.demod_path(ctx.params.demod3_idx)}/rate"]
    record_stage_settings(ctx, "response")


def record_stage_settings(ctx, stage):
    """阶段回读各自保存，校相和噪声不得覆盖响应标定。"""
    signals, channels, hf2 = (ctx.devices[key] for key in ("signals", "channels", "hf2"))
    ctx.state[f"{stage}_output_snapshot"] = {
        axis: {
            "shape": signals.get_shape(channel=channels[axis]),
            "frequency_Hz": signals.get_frequency(channel=channels[axis]),
            "output": signals.get_output(channel=channels[axis]),
            "mod_state": signals.get_mod_state(channel=channels[axis]),
            "burst_mode": signals.get_burst_mode(channel=channels[axis]),
            "burst_state": signals.get_burst_state(channel=channels[axis]),
            "burst_trigger_source": signals.get_burst_trigger_source(channel=channels[axis]),
        }
        for axis in ("x", "y", "z", "trigger")
    }
    ctx.state.setdefault("stage_frequencies", {})[stage] = {
        "rf_frequency_Hz": getattr(ctx, "z_rf_frequency_hz", ctx.state[f"{stage}_output_snapshot"]["z"]["frequency_Hz"]),
        "demod3_frequency_Hz": hf2.get_double(f"{hf2.osc_path(ctx.params.demod3_osc_idx)}/freq"),
    }
