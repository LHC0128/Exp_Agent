"""有符号 RF 响应扫描、噪声 DAQ 与响应模式恢复。"""
from dataclasses import replace
import math
import numpy as np
from lockin_amplifier import DAQConfig, daq, demod
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from ._helpers import hf2_snapshot, wait
from .excitation import arm_z


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
    # 幅度扫描与相位扫描节奏一致：按温控分组关闭温控、组间恢复，每点定长 DAQ 采集后逐点落盘。
    set_temperature_switch(signals, False, channel=channels["temp"])
    wait(params.temp_off_settle_s)
    for index, amplitude in enumerate(amplitudes):
        check_cancelled()
        if index and index % params.temp_group_size == 0:
            set_temperature_switch(signals, False, channel=channels["temp"])
            wait(params.temp_off_settle_s)
        phase = arm_z(signals, channels["z"], params, float(amplitude), phase_base=z_positive_phase)
        wait(params.rf_step_settle_s)
        results = daq.acquire_data(hf2, response_daq, demod_idx=3, actual_rate=response_rate, timeout=params.scan_point_duration_s + 10, cancellation_check=check_cancelled, strict_cleanup=True)
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
        np.savez(
            directory.raw / "response_data.npz",
            amplitudes_V=amplitudes[:index + 1], amplitudes_nT=amplitudes[:index + 1] * params.z_v_to_nt,
            demod3_x_V=means["x"], demod3_y_V=means["y"], demod3_r_V=means["r"],
            demod3_x_std_V=stds["x"], demod3_y_std_V=stds["y"], demod3_r_std_V=stds["r"],
            demod3_x_series_V=np.asarray(series["x"]), demod3_y_series_V=np.asarray(series["y"]), demod3_r_series_V=np.asarray(series["r"]),
            demod3_theta_deg=thetas, z_burst_phase_deg=phases, z_rf_freq_Hz=params.z_rf_freq, z_v_to_nT=params.z_v_to_nt,
            xy_ctrl_phase_deg=xy_phase.final_phase_deg, xy_ctrl_quad_deg=params.xy_ctrl_quad,
            duration_s=params.scan_point_duration_s, actual_rate_Sa_s=response_rate,
        )
        state["response_points_completed"] = index + 1
        if "raw/response_data.npz" not in state["data_files"]:
            state["data_files"].append("raw/response_data.npz")
        directory.update_config(**state)
        if (index + 1) % params.temp_group_size == 0 or index + 1 == len(amplitudes):
            set_temperature_switch(signals, True, channel=channels["temp"])
            wait(params.temp_switch_on_lag_s)
        ctx.progress("response", index + 1)


def acquire_noise(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    response_config = ctx.response_config
    arm_z(signals, channels["z"], params, 0.0)
    ctx.demod_mode = "noise"
    actual_rate = float(demod.configure_demodulator(hf2, replace(response_config, rate=params.noise_rate, time_constant=params.demod3_noise_tc, order=params.demod3_noise_order)))
    if not math.isfinite(actual_rate) or actual_rate <= 0 or actual_rate * params.noise_duration < 2:
        raise RuntimeError("实际噪声采样率或记录长度无效")
    state["actual_rates"]["demod3_noise_Sa_s"] = actual_rate
    state["hf2_noise_snapshot"] = hf2_snapshot(hf2)
    record_stage_settings(ctx, "noise")
    directory.update_config(**state)
    daq_config = DAQConfig(device=hf2.device_id, duration=params.noise_duration, grid_cols=int(actual_rate * params.noise_duration), grid_rows=1, grid_mode=2, signal_paths=["sample.x"])
    for index in range(params.noise_n_avg):
        check_cancelled()
        set_temperature_switch(signals, False, channel=channels["temp"])
        wait(params.temp_off_settle_s)
        results = daq.acquire_data(hf2, daq_config, demod_idx=3, actual_rate=actual_rate, timeout=params.noise_duration + 10, cancellation_check=check_cancelled, strict_cleanup=True)
        if not results or results[0].values.size < 2 or not np.all(np.isfinite(results[0].values)):
            raise RuntimeError("噪声 DAQ 未返回有效 X 数据")
        name = f"noise_D3_{index:04d}.npz"
        np.savez(directory.raw / name, noise_x_V=results[0].values, time_s=results[0].time, actual_rate_Sa_s=actual_rate, demod_idx=3, signal_path="sample.x", noise_duration_s=params.noise_duration,
                 z_rf_freq_Hz=state["stage_frequencies"]["noise"]["rf_frequency_Hz"],
                 demod3_freq_Hz=state["stage_frequencies"]["noise"]["demod3_frequency_Hz"])
        state["data_files"].append(f"raw/{name}")
        state["noise_records_completed"] = index + 1
        directory.update_config(**state)
        set_temperature_switch(signals, True, channel=channels["temp"])
        wait(params.temp_switch_on_lag_s)
        ctx.progress("noise", params.rf_amp_points + index + 1)


def restore_response(ctx):
    if ctx.response_config is not None:
        demod.configure_demodulator(ctx.devices["hf2"], ctx.response_config)
        ctx.demod_mode = "response"


def record_response_settings(ctx):
    """记录实际设置；仪器支持的离散值不作为一致性门槛。"""
    if ctx.demod_mode != "response" or ctx.response_config is None:
        raise ValueError("解调器模式已改变，请重新执行 A10 回环与响应准备及后续校相")
    hf2 = ctx.devices["hf2"]
    snapshot = hf2_snapshot(hf2)
    ctx.state["hf2_response_snapshot"] = snapshot
    ctx.state["actual_rates"]["demod3_response_Sa_s"] = snapshot[f"{hf2.demod_path(3)}/rate"]
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
        "rf_frequency_Hz": ctx.state[f"{stage}_output_snapshot"]["z"]["frequency_Hz"],
        "demod3_frequency_Hz": hf2.get_double(f"{hf2.osc_path(ctx.params.demod3_osc_idx)}/freq"),
    }
