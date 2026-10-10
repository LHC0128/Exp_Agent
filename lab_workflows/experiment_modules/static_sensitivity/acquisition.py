"""静磁场直流色散采集；只接收已连接设备，导入无硬件副作用。"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DGChannelShutdown, TemperatureSwitchRestore, run_safety_shutdown, set_temperature_switch
from ...steps.dispersion import acquire_dispersion_point, save_scan
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
    failed = False
    try:
        for index, offset in enumerate(offsets):
            check_cancelled()
            if index == 0 or (interval > 0 and index % interval == 0):
                set_temp_switch(temp, temp_channel, False)
                wait_for_settle(params.dispersion_temp_switch_off_settle_s)
            data["point_started_unix_s"][index] = time.time()
            validate_safety_limit("Z_magnetic_field", float(offset))
            z.setup_dc(float(offset), channel=z_channel)
            z.set_output(True, channel=z_channel)
            wait_for_settle(params.dispersion_step_settle_s)
            point = acquire_dispersion_point(
                hf2, demod_idx=params.hf2_demod_idx, duration_s=params.dispersion_duration_s,
                requested_rate=params.hf2_demod_rate, offset_v=float(offset),
                path=path.parent / "dispersion_steps" / f"point_{index:04d}.npz",
                cancellation_check=check_cancelled,
            )
            for key in ("x", "y", "r"):
                data[f"{key}_median"][index] = point[f"{key}_median"]
            data["sample_counts"][index] = point["sample_count"]
            data["actual_rate_sa_s"][index] = point["actual_rate_sa_s"]
            data["actual_duration_s"][index] = point["actual_duration_s"]
            data["completed_mask"][index] = True
            data["point_finished_unix_s"][index] = time.time()
            z.set_output(False, channel=z_channel)
            save_scan(path, data)
            if interval > 0 and (index + 1) % interval == 0:
                set_temp_switch(temp, temp_channel, True)
                wait_for_settle(params.dispersion_temp_switch_on_settle_s)
            report_runtime_progress(
                "dispersion", f"色散点 {index + 1}/{offsets.size}，Z={offset:+.6g} V",
                40 * (index + 1) / offsets.size,
                estimated_remaining_seconds=(time.monotonic() - started) / (index + 1) * (offsets.size - index - 1),
            )
    except BaseException:
        failed = True
        try:
            save_scan(path, data)
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
