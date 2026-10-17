"""逐频点内存采集、质量重试与恢复温控后的真实 NPZ 写盘。"""
import time
import numpy as np
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import set_temperature_switch
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from ...steps.dispersion import save_scan
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ._helpers import sleep, recovery_wait
from .excitation import set_gate_frequency, set_demod_frequency


class RPointQualityError(RuntimeError):
    """重采仍超标，保留所有尝试并阻断扫描后继。"""


def acquire_point(params, devices, channels, directory, metadata, actual_rate, device_id, *, switch=None, wait=None,
                  recovery=None, reader=None):
    switch, wait, recovery, reader = switch or set_temperature_switch, wait or sleep, recovery or recovery_wait, reader or acquire_r
    for attempt in range(params.r_point_max_attempts):
        check_cancelled()
        with DeferredAcquisitionWrites() as pending:
            def enabled(value):
                switch(devices["Temp_Switch"], value, channel=channels["Temp_Switch"])
                if value:
                    recovery(params.temp_switch_on_lag_s)
            with acquisition_window(enabled, wait, params.system_settle_s):
                payload = reader(devices["hf2"], device_id=device_id, demod_idx=0,
                                    actual_rate_sa_s=actual_rate, requested_rate_sa_s=params.response_rate_sa_s, duration_s=params.frequency_duration_s)
                summary = summarize_r(payload)
                accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
                filename = f"frequency_{metadata['frequency_index']:04d}_attempt_{attempt:02d}.npz"
                pending.add(np.savez, directory / filename, time_s=payload["time_s"], r_v=payload["r"],
                         actual_rate_sa_s=payload.get("actual_rate_Sa_s", actual_rate), attempt_index=attempt,
                         quality_accepted=accepted, **metadata, **summary, **{key: value for key, value in payload.items() if key not in {"time_s", "r", "x", "y"}})
        check_cancelled()
        if accepted:
            return {**summary, "actual_rate_sa_s": float(payload.get("actual_rate_Sa_s", actual_rate))}, filename
        print(f"R 标准差 {summary['r_std_v']:.6g} V 超限，第 {attempt + 1} 次采集被拒绝")
    raise RPointQualityError(f"频点 {metadata.get('frequency_hz', 'unknown')} Hz 连续 {params.r_point_max_attempts} 次 R 标准差超限")


def acquire_scan(params, devices, channels, directory, actual_rate, device_id, *, point_reader=None):
    point_reader = point_reader or acquire_point
    z_values = params.z_axis()
    total = len(z_values) * len(params.frequency_axis(float(z_values[0])))
    completed = 0
    started = time.monotonic()
    files, centers, finished_z = [], [], []
    for z_index, z_value in enumerate(z_values):
        check_cancelled()
        z = float(z_value)
        validate_safety_limit("Z_magnetic_field", z)
        device, channel = devices["Z_magnetic_field"], channels["Z_magnetic_field"]
        device.setup_dc(z, channel=channel)
        device.set_output(True, channel=channel)
        if device.get_shape(channel=channel).strip('"').upper() != "DC" or not device.get_output(channel=channel):
            raise RuntimeError("Z DC 模式或输出状态验证失败")
        point_dir = directory.raw / f"z_{z_index:03d}"
        point_dir.mkdir()
        frequencies = params.frequency_axis(z)
        means, stds, accepted_files = [], [], []
        for index, frequency_value in enumerate(frequencies):
            check_cancelled()
            frequency = float(frequency_value)
            set_gate_frequency(params, devices["Time_sequence"], channels["Time_sequence"], frequency)
            set_demod_frequency(devices["hf2"], frequency)
            metadata = dict(z_index=z_index, z_bias_v=z, z_bias_commanded_v=z,
                            predicted_center_hz=params.predicted_center(z), frequency_index=index,
                            frequency_hz=frequency, pump_duty_percent=params.pump_mod_duty,
                            pulse_width_s=params.pump_mod_duty / 100 / frequency)
            summary, filename = point_reader(params, devices, channels, point_dir, metadata, actual_rate, device_id)
            means.append(summary["r_mean_v"])
            stds.append(summary["r_std_v"])
            accepted_files.append(filename)
            completed += 1
            report_runtime_progress("acquisition", f"Z={z:+.3f} V，f={frequency:.1f} Hz，{completed}/{total}",
                                    85 * completed / total,
                                    estimated_remaining_seconds=(time.monotonic() - started) / completed * (total - completed))
        summary_path = point_dir / "frequency_scan.npz"
        np.savez(summary_path, z_bias_v=z, z_bias_commanded_v=z, predicted_center_hz=params.predicted_center(z),
                 frequency_hz=frequencies, r_mean_v=means, r_std_v=stds,
                 actual_rate_sa_s=summary.get("actual_rate_sa_s", actual_rate), accepted_file=np.asarray(accepted_files))
        files.append(summary_path.relative_to(directory.root).as_posix())
        centers.append(params.predicted_center(z))
        finished_z.append(z)
        # 每完成一条曲线更新索引，取消后只分析完整频扫。
        save_scan(directory.raw / "z_scan_index.npz", dict(z_bias_v=finished_z,
                  predicted_center_hz=centers, summary_file=np.asarray(files)))
    return [path.relative_to(directory.root).as_posix() for path in directory.raw.rglob("*.npz")]
