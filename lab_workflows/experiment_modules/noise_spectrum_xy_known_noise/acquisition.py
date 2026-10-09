"""本实验成对采集步骤；接收已连接设备，导入不访问硬件。"""
import json
import time

import numpy as np

from ...experiment_runtime import check_cancelled
from ...common import validate_safety_limit
from ...steps import DGChannelShutdown, TemperatureSwitchRestore, run_safety_shutdown, set_temperature_switch
from ...steps.dispersion import acquire_dispersion_point, save_scan
from .dispersion import fit_dispersion_curve


def wait_for_settle(seconds):
    """稳定等待期间响应取消，异常由工作流统一安全收尾。"""
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def acquire_pair(index, raw_dir, generator, channel, acquire, settle_s):
    """相邻点交替两态顺序；每段立即落盘，第二段失败时保留第一段。"""
    states = (False, True) if index % 2 == 0 else (True, False)
    for order, enabled in enumerate(states):
        check_cancelled()
        state = "on" if enabled else "off"
        generator.set_output(enabled, channel=channel)
        wait_for_settle(settle_s)
        started = time.time()
        waveform = acquire()
        finished = time.time()
        name = f"waveform_C{index:04d}_{state}.npy"
        np.save(raw_dir / name, waveform)
        waveform = np.asarray(waveform)
        nonfinite = int((~np.isfinite(waveform)).sum())
        valid = bool(waveform.ndim == 1 and waveform.size >= 2 and nonfinite == 0
                     and np.std(waveform) > 0)
        # 主机时间标记 DAQ 调用边界，不冒充仪器触发时间。
        with (raw_dir / "pair_timing.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(point=index, order=order, state=state, file=name,
                                         started_unix_s=started, finished_unix_s=finished,
                                         settle_s=settle_s, samples=int(waveform.size),
                                         nonfinite_samples=nonfinite, waveform_valid=valid)) + "\n")
        if not valid:
            print(f"警告: {name} 波形无效（非有限样本 {nonfinite}）；保留原文件，离线排除该数据对。", flush=True)


def acquire_dispersion_scan(params, *, z, z_channel, temp, temp_channel, hf2,
                            raw_dir, k_z_hz_per_v):
    """Z 纯 DC 色散扫描；单点连续采集，成功或故障均归零 Z 并恢复温控。"""
    offsets = np.linspace(-abs(params.dispersion_span_v), abs(params.dispersion_span_v),
                          params.dispersion_steps)
    data = {
        "schema_version": np.array(3), "offsets_v": offsets,
        "completed_mask": np.zeros(offsets.size, dtype=bool),
        "sample_counts": np.zeros(offsets.size, dtype=int),
        "point_started_unix_s": np.full(offsets.size, np.nan),
        "point_finished_unix_s": np.full(offsets.size, np.nan),
        "requested_rate_sa_s": np.array(params.hf2_demod_rate),
        "requested_duration_s": np.array(params.dispersion_duration_s),
        "actual_rate_sa_s": np.full(offsets.size, np.nan),
        "actual_duration_s": np.full(offsets.size, np.nan),
        "k_z_hz_per_v": np.array(k_z_hz_per_v),
        **{f"{key}_median": np.full(offsets.size, np.nan) for key in ("x", "y", "r")},
    }
    path = raw_dir / "dispersion_scan.npz"
    interval = params.dispersion_temp_switch_interval_points
    failed = False
    try:
        z.set_output(False, channel=z_channel)
        validate_safety_limit("Z_magnetic_field", 0.0)
        z.setup_dc(0.0, channel=z_channel)
        for index, offset in enumerate(offsets):
            check_cancelled()
            if index == 0 or (interval > 0 and index % interval == 0):
                validate_safety_limit("Temp_Switch", 0.0)
                temp.set_output(False, channel=temp_channel)
                wait_for_settle(params.xy_settle_time)
            data["point_started_unix_s"][index] = time.time()
            validate_safety_limit("Z_magnetic_field", float(offset))
            z.setup_dc(float(offset), channel=z_channel)
            z.set_output(True, channel=z_channel)
            wait_for_settle(params.dispersion_step_settle_s)
            point = acquire_dispersion_point(
                hf2, demod_idx=params.hf2_demod_idx, duration_s=params.dispersion_duration_s,
                requested_rate=params.hf2_demod_rate, offset_v=float(offset),
                path=raw_dir / "dispersion_steps" / f"point_{index:04d}.npz",
                cancellation_check=check_cancelled,
            )
            for key in ("x", "y", "r"):
                data[f"{key}_median"][index] = point[f"{key}_median"]
            data["sample_counts"][index] = point["sample_count"]
            data["actual_rate_sa_s"][index] = point["actual_rate_sa_s"]
            data["actual_duration_s"][index] = point["actual_duration_s"]
            data["point_finished_unix_s"][index] = time.time()
            data["completed_mask"][index] = True
            z.set_output(False, channel=z_channel)
            save_scan(path, data)
            if interval > 0 and (index + 1) % interval == 0 and index + 1 < offsets.size:
                set_temperature_switch(temp, True, channel=temp_channel, on_voltage=params.temp_switch)
                wait_for_settle(params.temp_switch_on_settle_s)
        fit = fit_dispersion_curve(
            offsets, data["y_median"], skip_start_points=params.dispersion_fit_skip_start,
            skip_end_points=params.dispersion_fit_skip_end, k_z_hz_per_v=k_z_hz_per_v,
        )
        data.update(fit, fit_valid=fit["is_valid"])
        save_scan(path, data)
    except BaseException:
        failed = True
        try:
            save_scan(path, data)
        except Exception as exc:
            print(f"色散部分数据保存失败：{exc}", flush=True)
        raise
    finally:
        report = run_safety_shutdown(
            dg_channels=(DGChannelShutdown(z, z_channel, "Z_magnetic_field", "Z 色散偏置"),),
            temperature_switch=TemperatureSwitchRestore(temp, temp_channel),
        )
        if report.errors:
            print("色散安全恢复失败：" + "；".join(report.errors), flush=True)
            if not failed:
                raise RuntimeError("色散安全恢复失败：" + "；".join(report.errors))
    wait_for_settle(params.temp_switch_on_settle_s)
    return data
