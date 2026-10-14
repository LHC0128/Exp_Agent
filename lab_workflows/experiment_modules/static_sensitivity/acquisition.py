"""静磁场直流色散采集；只接收已连接设备，导入无硬件副作用。"""

from __future__ import annotations
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites

import time
import math
from contextlib import nullcontext
from pathlib import Path

import numpy as np

from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DGChannelShutdown, TemperatureSwitchRestore, run_safety_shutdown, set_temperature_switch
from ...steps.dispersion import acquire_dispersion_point, save_scan
from lockin_amplifier import DAQConfig, select_acquisition
from lockin_amplifier.acquisition import suspended_demod_streams
from .models import StaticSensitivityParams
class RuntimeCancellation:
    """沿用现有实验的取消文件到公共步骤接入方式。"""

    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def wait_for_settle(seconds: float) -> None:
    """每段最多等待 0.1 秒，响应稳定等待期间的取消。"""
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def set_temp_switch(device, channel: int, enabled: bool) -> None:
    """静磁场已确认的例外：测量时物理 OFF，恢复为 5 V DC + ON。"""
    if enabled:
        set_temperature_switch(device, True, channel=channel)
    else:
        validate_safety_limit("Temp_Switch", 0.0)
        device.set_output(False, channel=channel)


def acquire_dispersion(params: StaticSensitivityParams, z, z_channel: int, temp,
                       temp_channel: int, hf2, path: Path) -> None:
    """启动 Z 输出前检查模块，并在 Scope 整个扫描阶段暂停解调传输。"""
    plan = select_acquisition(hf2.model, hf2.capabilities,
        DAQConfig(duration=params.dispersion_duration_s, signal_paths=["sample.x", "sample.y"]),
        params.hf2_demod_rate, params.hf2_demod_idx, hf2.acquisition_mode)
    with suspended_demod_streams(hf2) if plan.module == "scope" else nullcontext():
        _acquire_dispersion(params, z, z_channel, temp, temp_channel, hf2, path)


def _acquire_dispersion(params: StaticSensitivityParams, z, z_channel: int, temp,
                        temp_channel: int, hf2, path: Path) -> None:
    """纯 DC 正向扫描，逐点中位数；即使失败也保留已经读取的样本。"""
    offsets = np.linspace(params.dispersion_start_v, params.dispersion_stop_v, params.dispersion_steps)
    data = {
        "schema_version": np.array(3),
        "offsets_v": offsets,
        "completed_mask": np.zeros(offsets.size, dtype=bool),
        "sample_counts": np.zeros(offsets.size, dtype=int),
        "point_started_unix_s": np.full(offsets.size, np.nan),
        "point_finished_unix_s": np.full(offsets.size, np.nan),
        "requested_rate_sa_s": np.array(params.hf2_demod_rate),
        "requested_duration_s": np.array(params.dispersion_duration_s),
        "actual_rate_sa_s": np.full(offsets.size, np.nan),
        "actual_duration_s": np.full(offsets.size, np.nan),
        **{f"{key}_median": np.full(offsets.size, np.nan) for key in ("x", "y", "r")},
    }
    interval = params.dispersion_temp_switch_interval_points
    started = time.monotonic()
    with DeferredAcquisitionWrites() as pending:
        failed = False
        try:
            for index, offset in enumerate(offsets):
                check_cancelled()
                data["point_started_unix_s"][index] = time.time()
                validate_safety_limit("Z_magnetic_field", float(offset))
                z.setup_dc(float(offset), channel=z_channel)
                z.set_output(True, channel=z_channel)
                if index == 0 or (interval > 0 and index % interval == 0):
                    set_temp_switch(temp, temp_channel, False)
                wait_for_settle(params.system_settle_s)
                point = acquire_dispersion_point(
                    hf2, demod_idx=params.hf2_demod_idx, duration_s=params.dispersion_duration_s,
                    requested_rate=params.hf2_demod_rate, offset_v=float(offset),
                    path=path.parent / "dispersion_steps" / f"point_{index:04d}.npz",
                    cancellation_check=check_cancelled, pending=pending,
                )
                for key in ("x", "y", "r"):
                    data[f"{key}_median"][index] = point[f"{key}_median"]
                data["sample_counts"][index] = point["sample_count"]
                data["actual_rate_sa_s"][index] = point["actual_rate_sa_s"]
                data["actual_duration_s"][index] = point["actual_duration_s"]
                data["acquisition_module"] = point["acquisition_module"]
                data["time_source"] = point["time_source"]
                data["completed_mask"][index] = True
                data["point_finished_unix_s"][index] = time.time()
                z.set_output(False, channel=z_channel)
                pending.add(save_scan, path, data)
                if interval > 0 and (index + 1) % interval == 0 and index + 1 < offsets.size:
                    set_temp_switch(temp, temp_channel, True)
                    wait_for_settle(params.dispersion_temp_switch_on_settle_s)
                    pending.flush()
                report_runtime_progress(
                    "dispersion", f"色散点 {index + 1}/{offsets.size}，Z={offset:+.6g} V",
                    40 * (index + 1) / offsets.size,
                    estimated_remaining_seconds=(time.monotonic() - started) / (index + 1) * (offsets.size - index - 1),
                )
        except BaseException:
            failed = True
            try:
                pending.add(save_scan, path, data)
            except Exception as exc:
                print(f"色散部分数据保存失败：{exc}", flush=True)
            raise
        finally:
            report = run_safety_shutdown(
                dg_channels=(DGChannelShutdown(z, z_channel, "Z_magnetic_field", "Z 直流偏置", disable_sync=True),),
                temperature_switch=TemperatureSwitchRestore(temp, temp_channel),
            )
            if report.errors:
                print("色散安全恢复失败：" + "；".join(report.errors), flush=True)
                if not failed:
                    raise RuntimeError("色散安全恢复失败：" + "；".join(report.errors))


def prepare_noise(ctx):
    from lockin_amplifier import demod
    from ...steps import configure_fixed_dc_field
    from .phase import demod_config, hf2_snapshot
    import math

    hf2, params = ctx.devices["hf2"], ctx.params
    rate = float(demod.configure_demodulator(hf2, demod_config(params, ctx.calibrated_phase, noise=True), for_acquisition=True))
    if not math.isfinite(rate) or rate <= 0 or int(rate * params.noise_duration) < 2:
        raise ValueError("HF2 实际噪声采样率无效或采集样本不足")
    ctx.noise_rate = rate
    ctx.demod_mode = "noise"
    ctx.state["actual_rates"]["hf2_noise_rate_Sa_s"] = rate
    ctx.state["hf2_noise_configuration"] = hf2_snapshot(hf2, params.hf2_demod_idx)
    ctx.state["gs200_range_measurements"] = []
    wait_for_settle(0.3)
    configure_fixed_dc_field(ctx.devices["dg_sweep"], ctx.channels["Z_magnetic_field"], "Z_magnetic_field", 0.0)
    wait_for_settle(0.5)


def acquire_noise(ctx):
    """多量程逐段采集原始 Y；不在采集期间计算 PSD。"""
    from lockin_amplifier import DAQConfig, daq, result_rate, acquisition_metadata, save_acquisition_metadata

    params, devices, channels, mapping, directory = ctx.params, ctx.devices, ctx.channels, ctx.mapping, ctx.directory
    hf2 = devices["hf2"]
    rate = ctx.noise_rate
    measurements = []
    ctx.state["gs200_range_measurements"] = measurements
    total = len(params.gs200_current_ranges) * params.noise_n_avg
    started = time.monotonic()
    completed = 0
    for current_range in params.gs200_current_ranges:
        check_cancelled()
        gs = devices["gs200"]
        gs.set_current_range(current_range)
        wait_for_settle(params.gs200_range_settle_time)
        actual_range, actual_current = float(gs.get_current_range()), float(gs.get_current())
        if not math.isfinite(actual_range) or not math.isfinite(actual_current) or not math.isclose(actual_range, current_range, rel_tol=1e-6):
            raise RuntimeError("GS200 实际量程与请求值不一致或回读无效")
        name = f"gs200_range_{current_range * 1000:g}mA"
        noise_dir = directory.raw / "noise_raw" / name
        noise_dir.mkdir(parents=True, exist_ok=True)
        measurement = dict(directory=name, current_range_set_A=current_range,
                           current_range_actual_A=actual_range, current_setpoint_actual_A=actual_current,
                           samples_saved=0)
        measurements.append(measurement)
        ctx.save()
        for index in range(params.noise_n_avg):
            check_cancelled()
            config = DAQConfig(device=mapping["lockin_r"]["device_id"], trigger_type=0,
                              duration=params.noise_duration, grid_cols=int(rate * params.noise_duration),
                              grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(
                    lambda enabled: set_temp_switch(devices["dg_temp"], channels["Temp_Switch"], enabled),
                    wait_for_settle, params.system_settle_s,
                ):
                    acquired = daq.acquire_data(hf2, config=config, demod_idx=params.hf2_demod_idx,
                                               actual_rate=rate, requested_rate=params.hf2_noise_rate, timeout=params.noise_duration + 5,
                                               cancellation_check=check_cancelled, strict_cleanup=True)
                    waveform = next((np.asarray(item.values) for item in acquired if item.signal_name == "sample.y"), None)
                    if waveform is None:
                        raise ValueError("HF2 DAQ 未返回 sample.y")
                    pending.add(np.save, noise_dir / f"noise_{index:04d}.npy", waveform)
                    pending.add(save_acquisition_metadata, noise_dir / f"noise_{index:04d}.npy", acquired)
                    ctx.state["actual_rates"]["hf2_noise_rate_Sa_s"] = result_rate(acquired, rate)
                    measurement["acquisition"] = acquisition_metadata(acquired)
                    measurement["samples_saved"] += 1
                    pending.add(ctx.save)
                    if waveform.ndim != 1 or waveform.size < 2 or not np.isfinite(waveform).all():
                        raise ValueError("噪声波形维度、样本数或有限性无效；原始波形已保留")
            wait_for_settle(2.0)
            completed += 1
            report_runtime_progress(
                "noise", f"噪声 {name}，{index + 1}/{params.noise_n_avg}", 40 + 60 * completed / total,
                estimated_remaining_seconds=(time.monotonic() - started) / completed * (total - completed),
            )
