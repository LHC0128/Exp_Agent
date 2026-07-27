"""原子自旋投影噪声 SDS 原始 PD 波形采集工作流。"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from gs200 import GS200Instrument
from sds_acquisition import SDSAcquisition, SDSInstrument, save_to_npz
from tec_controller import TECInstrument

from ...common import (
    find_project_root,
    load_mapping,
    load_safety_limits,
    validate_safety_limit,
)
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ScopeAutoRangeState,
    ScopeCaptureSettings,
    ShutdownAction,
    TemperatureSwitchRestore,
    acquire_autoranged_waveform,
    configure_fixed_rate_scope,
    create_run_directory,
    read_complete_scope_record,
    restore_main_field_state,
    run_safety_shutdown,
    set_temperature_switch,
    snapshot_main_field_state,
    synchronize_connected_clocks,
    temperature_gated_acquire,
    wait_for_temperature_stable,
)
from ...steps.state import StateGuard
from .calibration import MainFieldCalibration, load_main_field_calibration
from .models import ProjectionNoiseParams


EXPERIMENT_ID = "projection-noise"
DATA_TYPE = "Projection_noise"
EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()


def _sleep(seconds: float) -> None:
    """支持 GUI 取消的短间隔等待。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _scope_settings(params: ProjectionNoiseParams) -> ScopeCaptureSettings:
    return ScopeCaptureSettings(
        sample_rate_sa_s=params.scope_sample_rate_sa_s,
        duration_s=params.scope_duration_s,
        pd_channel=params.scope_pd_channel,
        trigger_mode=params.scope_trigger_mode,
        initial_scale_v_div=params.scope_initial_scale_v_div,
        offset_v=params.scope_offset_v,
        vertical_divisions=params.scope_vertical_divisions,
        scale_min_v_div=params.scope_scale_min_v_div,
        scale_max_v_div=params.scope_scale_max_v_div,
        auto_range_low_fraction=params.scope_auto_range_low_fraction,
        auto_range_high_fraction=params.scope_auto_range_high_fraction,
        auto_offset_tolerance_fraction=(
            params.scope_auto_offset_tolerance_fraction
        ),
        auto_range_max_attempts=params.scope_auto_range_max_attempts,
        welch_nperseg=params.welch_nperseg,
        maximum_frequency_hz=(
            params.target_larmor_frequency_hz + params.fit_half_width_hz
        ),
        record_max_attempts=params.scope_record_max_attempts,
    )


def _connect_devices(
    mapping: dict[str, dict[str, Any]], session: DeviceSession
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接主场、光功率、温控、SDS 和 TEC。"""
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    if pump_cfg["resource"] != probe_cfg["resource"]:
        raise ValueError("Pump_laser_power 与 Probe_laser_power 必须位于同一台设备")
    devices["laser"] = session.connect(
        "laser", pump_cfg["resource"], lambda: create_signal_generator(pump_cfg)
    )
    channels["pump_laser"] = int(pump_cfg["channel"])
    channels["probe_laser"] = int(probe_cfg["channel"])

    temp_cfg = mapping["Temp_Switch"]
    devices["temp_switch"] = session.connect(
        "temp_switch",
        temp_cfg["resource"],
        lambda: create_signal_generator(temp_cfg),
    )
    channels["temp_switch"] = int(temp_cfg["channel"])

    scope_cfg = mapping["scope_waveform"]
    devices["scope"] = session.connect(
        "scope",
        scope_cfg["resource"],
        lambda: SDSInstrument(scope_cfg["resource"]),
    )
    devices["acquirer"] = session.bind(
        "acquirer", SDSAcquisition(devices["scope"])
    )

    tec_cfg = mapping["temperature"]
    devices["tec"] = session.connect(
        "tec", tec_cfg["resource"], lambda: TECInstrument(port=tec_cfg["resource"])
    )
    return devices, channels


def _device_snapshot(
    mapping: dict[str, dict[str, Any]],
    main_field_state: Any,
) -> dict[str, Any]:
    keys = (
        "main_magnetic_field",
        "Pump_laser_power",
        "Probe_laser_power",
        "Temp_Switch",
        "scope_waveform",
        "temperature",
    )
    return {
        "mapping": {key: mapping[key] for key in keys},
        "main_field_before_configuration": asdict(main_field_state),
    }


def _configure_outputs(
    params: ProjectionNoiseParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
) -> tuple[float, dict[str, dict[str, Any]]]:
    """配置热态光学条件、温控和 GS200 安全初始状态。"""
    clock_sources = synchronize_connected_clocks(
        devices,
        mapping,
        {
            "laser": "Pump_laser_power",
            "temp_switch": "Temp_Switch",
        },
    )
    laser = devices["laser"]
    validate_safety_limit("Pump_laser_power", params.pump_laser_power_v)
    laser.setup_dc(params.pump_laser_power_v, channel=channels["pump_laser"])
    laser.set_output(
        params.pump_laser_power_v > 0.0,
        channel=channels["pump_laser"],
    )
    validate_safety_limit("Probe_laser_power", params.probe_laser_power_v)
    laser.setup_dc(params.probe_laser_power_v, channel=channels["probe_laser"])
    laser.set_output(True, channel=channels["probe_laser"])

    set_temperature_switch(
        devices["temp_switch"], True, channel=channels["temp_switch"]
    )
    validate_safety_limit("temperature", params.temperature_c)
    tec = devices["tec"]
    tec.set_target_temperature(params.temperature_c, channel=1)
    tec.set_enable(True, channel=1)
    actual_temperature = wait_for_temperature_stable(
        tec,
        params.temperature_c,
        channel=1,
        tolerance_c=params.temperature_tolerance_c,
        stable_reads=params.temperature_stable_reads,
        poll_interval_s=params.temperature_poll_interval_s,
        timeout_s=params.temperature_timeout_s,
        cancellation=_RuntimeCancellation(),
    )

    gs200 = devices["gs200"]
    gs_cfg = mapping["main_magnetic_field"]
    gs200.set_output(False)
    if gs_cfg.get("source_function"):
        gs200.set_source_function(gs_cfg["source_function"])
    limits = load_safety_limits()
    gs200.set_current_limit(float(limits["main_magnetic_field"]["max"]) / 1000.0)
    return float(actual_temperature), clock_sources


def _temperature_gated_capture(
    params: ProjectionNoiseParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: Any,
) -> Any:
    settings = _scope_settings(params)
    return temperature_gated_acquire(
        temp_switch=devices["temp_switch"],
        temp_channel=channels["temp_switch"],
        off_settle_s=params.temp_switch_off_settle_s,
        on_settle_s=params.temp_switch_on_settle_s,
        acquire=lambda: read_complete_scope_record(
            settings,
            devices,
            scope_config,
            check_cancelled=check_cancelled,
            sleep=_sleep,
        ),
        check_cancelled=check_cancelled,
        sleep=_sleep,
        set_temperature_switch=set_temperature_switch,
        cancellation=_RuntimeCancellation(),
    )


def _save_frame(
    path: Path,
    payload: dict[str, Any],
    *,
    phase: str,
    frame_index: int,
    params: ProjectionNoiseParams,
    main_field_output_on: bool,
    main_field_current_ma: float,
) -> str:
    result = payload["result"]
    metadata = {
        "phase": phase,
        "frame_index": int(frame_index),
        "main_field_output_on": bool(main_field_output_on),
        "main_field_current_ma": float(main_field_current_ma),
        "requested_sample_rate_sa_s": float(params.scope_sample_rate_sa_s),
        "actual_sample_rate_sa_s": float(payload["actual_rate_sa_s"]),
        "requested_duration_s": float(params.scope_duration_s),
        "actual_duration_s": float(payload["actual_duration_s"]),
        "scale_used_v_div": float(payload["scale_used_v_div"]),
        "offset_used_v": float(payload["offset_used_v"]),
        "next_scale_v_div": float(payload["next_scale_v_div"]),
        "next_offset_v": float(payload["next_offset_v"]),
        "attempt_count": int(payload["attempt_count"]),
        "attempt_scales_v_div": np.asarray(
            payload["attempt_scales_v_div"], dtype=float
        ).tolist(),
        "attempt_offsets_v": np.asarray(
            payload["attempt_offsets_v"], dtype=float
        ).tolist(),
        "temp_switch_during_acquisition": "0 V DC + output ON",
        "temp_switch_between_frames": "5 V DC + output ON",
        "timebase_scale": float(result.preamble_dict.get("horiz_interval", 0.0))
        * max(1, len(result.raw_data))
        / 10.0,
        "horizontal_divisions": 10,
    }
    result.config_snapshot = metadata
    return save_to_npz(
        str(path), [result], config_dict=metadata, save_mode="raw"
    )


def _acquire_phase(
    params: ProjectionNoiseParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: Any,
    auto_range: ScopeAutoRangeState,
    *,
    phase: str,
    main_field_output_on: bool,
    main_field_current_ma: float,
) -> list[dict[str, Any]]:
    """采集一个 GS200 状态下的全部示波器帧。"""
    phase_dir = run_dir.raw / phase
    phase_dir.mkdir(exist_ok=False)
    settings = _scope_settings(params)
    records: list[dict[str, Any]] = []
    for index in range(params.acq_repeats):
        check_cancelled()
        print(f"{phase} 帧 {index + 1}/{params.acq_repeats}")
        payload = acquire_autoranged_waveform(
            settings,
            scope_config,
            devices["scope"],
            auto_range,
            capture=lambda: _temperature_gated_capture(
                params, devices, channels, scope_config
            ),
            check_cancelled=check_cancelled,
        )
        relative = f"raw/{phase}/waveform_{index:04d}.npz"
        _save_frame(
            run_dir.root / relative,
            payload,
            phase=phase,
            frame_index=index,
            params=params,
            main_field_output_on=main_field_output_on,
            main_field_current_ma=main_field_current_ma,
        )
        records.append(
            {
                "phase": phase,
                "frame_index": index,
                "file": relative,
                "main_field_output_on": main_field_output_on,
                "main_field_current_ma": main_field_current_ma,
                "actual_rate_sa_s": float(payload["actual_rate_sa_s"]),
                "actual_duration_s": float(payload["actual_duration_s"]),
                "scale_used_v_div": float(payload["scale_used_v_div"]),
                "offset_used_v": float(payload["offset_used_v"]),
                "attempt_count": int(payload["attempt_count"]),
            }
        )
    return records


def _save_acquisition_index(run_dir: Any, records: list[dict[str, Any]]) -> str:
    path = run_dir.raw / "scope_acquisition_index.npz"
    np.savez(
        path,
        phase=np.asarray([item["phase"] for item in records], dtype=str),
        frame_index=np.asarray([item["frame_index"] for item in records], dtype=int),
        relative_file=np.asarray([item["file"] for item in records], dtype=str),
        main_field_output_on=np.asarray(
            [item["main_field_output_on"] for item in records], dtype=bool
        ),
        main_field_current_ma=np.asarray(
            [item["main_field_current_ma"] for item in records], dtype=float
        ),
        actual_rate_sa_s=np.asarray(
            [item["actual_rate_sa_s"] for item in records], dtype=float
        ),
        actual_duration_s=np.asarray(
            [item["actual_duration_s"] for item in records], dtype=float
        ),
        scale_used_v_div=np.asarray(
            [item["scale_used_v_div"] for item in records], dtype=float
        ),
        offset_used_v=np.asarray(
            [item["offset_used_v"] for item in records], dtype=float
        ),
        attempt_count=np.asarray(
            [item["attempt_count"] for item in records], dtype=int
        ),
    )
    return "raw/scope_acquisition_index.npz"


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    main_field_guard: StateGuard,
) -> SafetyShutdownReport:
    """停止示波器、恢复温控和 GS200，并仅断开 TEC。"""
    extra_actions: list[ShutdownAction] = []
    if devices.get("scope") is not None:
        extra_actions.append(
            ShutdownAction("停止示波器触发失败", devices["scope"].trigger_stop)
        )
    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"], channels["temp_switch"]
        )
    report = run_safety_shutdown(
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=STANDARD_PRESERVED_OUTPUTS,
    )
    guard_errors = tuple(main_field_guard.restore())
    return SafetyShutdownReport(
        action_errors=(*report.action_errors, *guard_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )


def run(params: ProjectionNoiseParams) -> Path:
    """执行 GS200 打开阶段及可选关闭对照组的 SDS 自旋噪声采集。"""
    root = find_project_root()
    mapping = load_mapping(root)
    calibration = load_main_field_calibration(
        root, params.main_field_calibration_source_run
    )
    target_current_ma = calibration.current_for_frequency(
        params.target_larmor_frequency_hz
    )
    validate_safety_limit("main_magnetic_field", target_current_ma)
    phase_config: list[dict[str, Any]] = []
    if params.measure_field_off_control:
        phase_config.append(
            {
                "id": "field_off",
                "main_field_output": "OFF",
                "description": "GS200 output-off background",
            }
        )
    phase_config.append(
        {
            "id": "field_on",
            "main_field_output": "ON",
            "main_field_current_ma": target_current_ma,
            "predicted_larmor_frequency_hz": params.target_larmor_frequency_hz,
        }
    )
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        acquisition_backend="sds_pd_raw",
        geometry={
            "main_field": "Z controlled by GS200",
            "probe": "X",
            "pump": (
                f"{params.pump_laser_power_v:g} V DC, output "
                f"{'ON' if params.pump_laser_power_v > 0.0 else 'OFF'}"
            ),
            "pd_detection": "SDS CH1 raw waveform",
        },
        measure_field_off_control=params.measure_field_off_control,
        phases=phase_config,
        main_field_calibration={
            "source_run": calibration.source_run,
            "analysis_path": str(calibration.analysis_path),
            "slope_hz_per_ma": calibration.slope_hz_per_ma,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
            "target_current_ma": target_current_ma,
        },
        temperature_gating={
            "scope": "every_scope_attempt",
            "during_acquisition": "0 V DC + output ON",
            "between_frames": "5 V DC + output ON",
            "off_settle_s": params.temp_switch_off_settle_s,
            "on_settle_s": params.temp_switch_on_settle_s,
        },
        analysis_during_acquisition=False,
    )

    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    main_field_guard = StateGuard()
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = _connect_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        main_field_state = snapshot_main_field_state(devices["gs200"])
        main_field_guard.add(
            "恢复 GS200 运行前状态",
            lambda: restore_main_field_state(devices["gs200"], main_field_state),
        )
        run_dir.update_config(
            device_snapshot=_device_snapshot(mapping, main_field_state)
        )

        actual_temperature, clock_sources = _configure_outputs(
            params, devices, channels, mapping
        )
        scope_config, scope_snapshot = configure_fixed_rate_scope(
            _scope_settings(params), devices, sleep=_sleep
        )
        run_dir.update_config(
            initial_temperature_c=actual_temperature,
            clock_sources=clock_sources,
            scope_configuration=scope_snapshot,
            actual_rates={"scope_sa_s": scope_snapshot["actual_sample_rate_sa_s"]},
        )
        auto_range = ScopeAutoRangeState(
            scale_v_div=float(scope_snapshot["actual_initial_scale_v_div"]),
            offset_v=float(scope_snapshot["actual_initial_offset_v"]),
        )

        gs200 = devices["gs200"]
        gs200.set_output(False)
        records: list[dict[str, Any]] = []
        if params.measure_field_off_control:
            records.extend(
                _acquire_phase(
                    params,
                    run_dir,
                    devices,
                    channels,
                    scope_config,
                    auto_range,
                    phase="field_off",
                    main_field_output_on=False,
                    main_field_current_ma=0.0,
                )
            )

        validate_safety_limit("main_magnetic_field", target_current_ma)
        gs200.set_output(False)
        gs200.set_current(target_current_ma / 1000.0)
        gs200.set_output(True)
        _sleep(params.main_field_settle_s)
        records.extend(
            _acquire_phase(
                params,
                run_dir,
                devices,
                channels,
                scope_config,
                auto_range,
                phase="field_on",
                main_field_output_on=True,
                main_field_current_ma=target_current_ma,
            )
        )
        index_file = _save_acquisition_index(run_dir, records)
        data_files = [index_file, *[item["file"] for item in records]]
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            failure_reason=None,
            data_files=data_files,
            final_scope_scale_v_div=auto_range.scale_v_div,
            final_scope_offset_v=auto_range.offset_v,
        )
        print(f"原子自旋投影噪声示波器采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed", failure_reason=failure_reason
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, main_field_guard)
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                main_field_restored=not main_field_guard.restore_errors,
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(ProjectionNoiseParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
