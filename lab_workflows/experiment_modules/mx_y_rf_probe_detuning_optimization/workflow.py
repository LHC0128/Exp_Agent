"""Mx Y RF Probe 光功率与 PZT 失谐优化采集工作流。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import io
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
import yaml
from tqdm.auto import tqdm

from ...common import (
    WorkflowCancelled,
    find_project_root,
    load_mapping,
    load_safety_limits,
    validate_safety_limit,
)
from ...devices import create_dlc_pro
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeviceSession, create_run_directory
from ...steps.run_finish import finalize_run_safety
from ...steps.state import StateGuard
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    acquire_mx_y_rf_point,
    configure_mx_y_rf_outputs,
    connect_mx_y_rf_devices,
    restore_mx_y_rf_laser_powers,
    safe_shutdown,
    snapshot_mx_y_rf_state,
)
from .models import MxYRFProbeDetuningOptimizationParams
from .scan import (
    ProbeDetuningGridPoint,
    build_probe_detuning_axes,
    iter_probe_detuning_grid,
)


EXPERIMENT_ID = "mx-y-rf-probe-detuning-optimization"
DATA_TYPE = "Mx_Y_RF_Probe_Detuning_Optimization"
EXECUTION_MODE = "typed_workflow"


@dataclass(frozen=True, slots=True)
class ProbeLaserState:
    """进入实验前需要原样恢复的 DLC pro PZT/扫描状态。"""

    pzt_voltage_v: float
    pzt_actual_v: float
    scan_amplitude_vpp: float
    scan_enabled: bool


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """更新可恢复的网格采集清单，并兼容 Windows 覆盖限制。"""
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _sleep_cancellable(seconds: float) -> None:
    """以短轮询等待，使 PZT 稳定阶段仍可取消。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _connect_probe_laser_controller(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[Any, dict[str, Any]]:
    """连接 DLC pro Probe 激光控制器。"""
    config = mapping["probe_laser"]
    resource = str(config["resource"])
    instrument = session.connect(
        "probe_laser_controller",
        f"dlc-pro://{resource}:{int(config.get('laser_channel', 1))}",
        lambda: create_dlc_pro(config),
    )
    return instrument, config


def _snapshot_and_validate_probe_laser(
    instrument: Any,
    config: dict[str, Any],
) -> tuple[ProbeLaserState, dict[str, Any]]:
    """核对 DLC pro 身份、健康和出光状态并保存恢复快照。"""
    controller_serial = str(instrument.get_controller_serial())
    laser_head_serial = str(instrument.get_laser_head_serial())
    expected_controller = str(config.get("controller_serial", "")).strip()
    expected_head = str(config.get("laser_head_serial", "")).strip()
    if expected_controller and controller_serial != expected_controller:
        raise RuntimeError(
            "DLC pro 控制器身份不匹配："
            f"期望 {expected_controller!r}，实际 {controller_serial!r}"
        )
    if expected_head and laser_head_serial != expected_head:
        raise RuntimeError(
            "DLC pro 激光头身份不匹配："
            f"期望 {expected_head!r}，实际 {laser_head_serial!r}"
        )

    system_health_code = int(instrument.get_system_health_code())
    laser_health_code = int(instrument.get_laser_health_code())
    interlock_open = bool(instrument.get_interlock_open())
    laser_enabled = bool(instrument.get_laser_enabled())
    emission = bool(instrument.get_emission())
    laser_emission = bool(instrument.get_laser_emission())
    if system_health_code != 0:
        raise RuntimeError("DLC pro 系统健康状态异常，拒绝开始 PZT 扫描")
    if laser_health_code != 0:
        raise RuntimeError("DLC pro 激光头健康状态异常，拒绝开始 PZT 扫描")
    if interlock_open:
        raise RuntimeError("DLC pro 联锁回路断开，拒绝开始 PZT 扫描")
    if not laser_enabled:
        raise RuntimeError("DLC pro Laser Enabled 为 OFF，拒绝开始 PZT 扫描")
    if not emission or not laser_emission:
        raise RuntimeError("DLC pro Emission 未开启，拒绝开始 PZT 扫描")

    state = ProbeLaserState(
        pzt_voltage_v=float(instrument.get_pzt_voltage_v()),
        pzt_actual_v=float(instrument.get_pzt_voltage_actual_v()),
        scan_amplitude_vpp=float(instrument.get_scan_amplitude_vpp()),
        scan_enabled=bool(instrument.get_scan_enabled()),
    )
    validate_safety_limit(
        "probe_laser_pzt_voltage",
        state.pzt_voltage_v,
    )
    validate_safety_limit(
        "probe_laser_scan_amplitude",
        state.scan_amplitude_vpp,
    )
    safety_limits = load_safety_limits()
    pzt_rule = safety_limits["probe_laser_pzt_voltage"]
    pzt_low = float(pzt_rule["min"])
    pzt_high = float(pzt_rule["max"])
    envelope_low = (
        state.pzt_voltage_v - state.scan_amplitude_vpp / 2.0
    )
    envelope_high = (
        state.pzt_voltage_v + state.scan_amplitude_vpp / 2.0
    )
    if envelope_low < pzt_low or envelope_high > pzt_high:
        raise RuntimeError(
            "DLC pro 初始扫描包络越界，无法保证实验后安全恢复："
            f"{envelope_low:g}～{envelope_high:g} V，允许范围为 "
            f"{pzt_low:g}～{pzt_high:g} V"
        )
    snapshot = {
        "controller_serial": controller_serial,
        "laser_head_serial": laser_head_serial,
        "system_health_code": system_health_code,
        "system_health": str(instrument.get_system_health()),
        "laser_health_code": laser_health_code,
        "laser_health": str(instrument.get_laser_health()),
        "interlock_open": interlock_open,
        "laser_enabled": laser_enabled,
        "emission": emission,
        "laser_emission": laser_emission,
        **asdict(state),
    }
    return state, snapshot


def _prepare_probe_laser_scan(instrument: Any) -> None:
    """关闭 DLC pro 内置扫描并把扫描幅度归零。"""
    validate_safety_limit("probe_laser_scan_amplitude", 0.0)
    instrument.set_scan_enabled(False)
    instrument.set_scan_amplitude_vpp(0.0)


def _restore_probe_laser_state(
    instrument: Any | None,
    state: ProbeLaserState | None,
) -> dict[str, Any]:
    """按 PZT、扫描幅度、扫描启停顺序恢复进入实验前的状态。"""
    if instrument is None or state is None:
        return {
            "attempted": False,
            "success": True,
            "error": None,
        }
    try:
        validate_safety_limit(
            "probe_laser_pzt_voltage",
            state.pzt_voltage_v,
        )
        validate_safety_limit(
            "probe_laser_scan_amplitude",
            state.scan_amplitude_vpp,
        )
        instrument.set_pzt_voltage_v(state.pzt_voltage_v)
        instrument.set_scan_amplitude_vpp(state.scan_amplitude_vpp)
        instrument.set_scan_enabled(state.scan_enabled)
    except Exception as exc:
        error = (
            f"{exc}；设备状态可能未知，请检查 DLC pro/TOPAS"
        )
        print(f"Probe 激光 PZT/扫描状态恢复警告: {error}")
        return {
            "attempted": True,
            "success": False,
            "error": error,
            "target": asdict(state),
        }
    return {
        "attempted": True,
        "success": True,
        "error": None,
        "target": asdict(state),
    }


def _set_probe_power(
    point: ProbeDetuningGridPoint,
    devices: dict[str, Any],
    channels: dict[str, int],
) -> None:
    """设置一个 Probe 功率点并保持输出开启。"""
    validate_safety_limit("Probe_laser_power", point.probe_power_v)
    laser = devices["laser"]
    laser.setup_dc(
        point.probe_power_v,
        channel=channels["probe_laser"],
    )
    laser.set_output(True, channel=channels["probe_laser"])


def _set_pzt_row(
    params: MxYRFProbeDetuningOptimizationParams,
    instrument: Any,
    pzt_voltage_v: float,
) -> dict[str, float]:
    """设置一个静态 PZT 行，等待稳定并返回回读。"""
    validate_safety_limit("probe_laser_pzt_voltage", pzt_voltage_v)
    setpoint_readback = float(
        instrument.set_pzt_voltage_v(pzt_voltage_v)
    )
    _sleep_cancellable(params.pzt_settle_time_s)
    return {
        "pzt_voltage_v": setpoint_readback,
        "pzt_actual_v": float(instrument.get_pzt_voltage_actual_v()),
    }


def _read_pzt_point(
    instrument: Any,
    requested_pzt_voltage_v: float,
) -> dict[str, float]:
    """为每个完整灵敏度点保存 PZT setpoint 与实际电压。"""
    setpoint_readback = float(instrument.get_pzt_voltage_v())
    actual = float(instrument.get_pzt_voltage_actual_v())
    validate_safety_limit(
        "probe_laser_pzt_voltage",
        setpoint_readback,
    )
    return {
        "pzt_voltage_setpoint_v": float(requested_pzt_voltage_v),
        "pzt_voltage_readback_v": setpoint_readback,
        "pzt_actual_v": actual,
        "pzt_setpoint_error_v": (
            setpoint_readback - float(requested_pzt_voltage_v)
        ),
    }


def _initial_manifest(
    params: MxYRFProbeDetuningOptimizationParams,
    points: list[ProbeDetuningGridPoint],
) -> dict[str, Any]:
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    return {
        "schema_version": 1,
        "scan_order": "pzt_outer_probe_inner_serpentine",
        "pzt_voltage_v": pzt_axis.tolist(),
        "probe_power_v": probe_axis.tolist(),
        "pzt_settle_time_s": params.pzt_settle_time_s,
        "acquisition_order": [point.key for point in points],
        "pzt_rows": {},
        "points": {
            point.key: {
                "pzt_index": point.pzt_index,
                "probe_index": point.probe_index,
                "pzt_voltage_v": point.pzt_voltage_v,
                "probe_power_v": point.probe_power_v,
                "status": "pending",
                "reason": None,
            }
            for point in points
        },
    }


def _acquire_grid(
    params: MxYRFProbeDetuningOptimizationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_response_rate: float,
    device_id: str,
) -> dict[str, Any]:
    points = iter_probe_detuning_grid(params)
    pzt_axis, probe_axis = build_probe_detuning_axes(params)
    points_root = run_dir.raw / "points"
    points_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir.raw / "point_manifest.yaml"
    manifest = _initial_manifest(params, points)
    _write_manifest(manifest_path, manifest)
    np.savez(
        run_dir.raw / "probe_detuning_grid.npz",
        pzt_voltage_v=pzt_axis,
        probe_power_v=probe_axis,
        acquisition_order=np.asarray([point.key for point in points]),
    )

    completed = 0
    invalid = 0
    actual_response_rates: list[float] = []
    actual_noise_rates: list[float] = []
    total = len(points)
    current_pzt_index: int | None = None
    pzt_controller = devices["probe_laser_controller"]
    interactive_progress = bool(sys.stdout.isatty())
    progress_stream = sys.stdout if interactive_progress else io.StringIO()
    progress = tqdm(
        total=total,
        desc="PZT/Probe 二维网格",
        unit="point",
        file=progress_stream,
        dynamic_ncols=True,
    )
    try:
        for sequence_index, point in enumerate(points):
            check_cancelled()
            if point.pzt_index != current_pzt_index:
                row_readback = _set_pzt_row(
                    params,
                    pzt_controller,
                    point.pzt_voltage_v,
                )
                manifest["pzt_rows"][f"pzt_{point.pzt_index:03d}"] = {
                    "pzt_index": point.pzt_index,
                    "requested_pzt_voltage_v": point.pzt_voltage_v,
                    **row_readback,
                }
                current_pzt_index = point.pzt_index
                _write_manifest(manifest_path, manifest)

            record = manifest["points"][point.key]
            record["status"] = "running"
            record["sequence_index"] = sequence_index
            _write_manifest(manifest_path, manifest)
            progress.set_postfix_str(
                f"PZT={point.pzt_voltage_v:.3f} V, "
                f"Probe={point.probe_power_v:.3f} V"
            )
            print(
                f"Probe/PZT 点 {sequence_index + 1}/{total}: "
                f"PZT={point.pzt_voltage_v:.6g} V, "
                f"Probe={point.probe_power_v:.6g} V"
            )
            _set_probe_power(point, devices, channels)
            record.update(
                _read_pzt_point(
                    pzt_controller,
                    point.pzt_voltage_v,
                )
            )
            _write_manifest(manifest_path, manifest)
            point_raw = points_root / point.key
            try:
                point_result = acquire_mx_y_rf_point(
                    params,
                    point_raw,
                    devices,
                    channels,
                    actual_response_rate,
                    device_id,
                )
            except RPointQualityError as exc:
                invalid += 1
                record["status"] = "invalid_quality"
                record["reason"] = str(exc)
                _write_manifest(manifest_path, manifest)
                print(f"  该 Probe/PZT 点无效，继续扫描: {exc}")
            else:
                completed += 1
                response_rate = float(
                    point_result["actual_response_rate_sa_s"]
                )
                noise_rate = float(
                    point_result["actual_noise_rate_sa_s"]
                )
                actual_response_rates.append(response_rate)
                actual_noise_rates.append(noise_rate)
                record["status"] = "completed"
                record["reason"] = None
                record["actual_response_rate_sa_s"] = response_rate
                record["actual_noise_rate_sa_s"] = noise_rate
                _write_manifest(manifest_path, manifest)
            progress.update(1)
            if not interactive_progress:
                print(str(progress), flush=True)
    finally:
        progress.close()

    return {
        "total_point_count": total,
        "completed_point_count": completed,
        "invalid_quality_point_count": invalid,
        "actual_response_rates_sa_s": actual_response_rates,
        "actual_noise_rates_sa_s": actual_noise_rates,
        "manifest": "raw/point_manifest.yaml",
        "grid": "raw/probe_detuning_grid.npz",
    }


def run(params: MxYRFProbeDetuningOptimizationParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
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
        scan_mode="nested_scan",
        scan_order="pzt_outer_probe_inner_serpentine",
        objective="minimize flat_median_ft_per_sqrt_hz",
        geometry={
            "main_field": "Z",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "x_field_output": "OFF",
        },
        detuning_axis={
            "control": "DLC pro Laser 1 Scan Offset",
            "unit": "V",
            "frequency_calibrated": False,
        },
        linewidth_mode="amplitude_equivalent",
        compatibility={
            "reference_experiment_id": "mx-y-rf-power-optimization",
            "schema_version": params.schema_version,
        },
        acquisition_signals=["R"],
        power_restore={
            "pump_power_v": params.pump_laser_power_v,
            "probe_power_v": params.probe_laser_power_v,
            "outputs_on": True,
        },
        temperature_control={
            "tec_connected": False,
            "tec_target_temperature_configured": False,
            "temperature_stability_waited": False,
            "temperature_switch_controlled": True,
            "temperature_switch_restored_on_exit": True,
        },
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    grid_summary: dict[str, Any] | None = None
    probe_laser_state: ProbeLaserState | None = None
    probe_laser_guard = StateGuard()
    probe_laser_restore: dict[str, Any] = {
        "attempted": False,
        "success": True,
        "error": None,
    }
    try:
        check_cancelled()
        try:
            devices, channels = connect_mx_y_rf_devices(
                mapping,
                session,
                connect_tec=False,
            )
            controller, controller_config = (
                _connect_probe_laser_controller(mapping, session)
            )
            devices["probe_laser_controller"] = controller
        except Exception:
            session.cleanup_connection_failure()
            raise

        probe_laser_state, controller_snapshot = (
            _snapshot_and_validate_probe_laser(
                devices["probe_laser_controller"],
                controller_config,
            )
        )
        device_snapshot = snapshot_mx_y_rf_state(devices, channels)
        device_snapshot["probe_laser_controller"] = controller_snapshot
        run_dir.update_config(device_snapshot=device_snapshot)

        actual_response_rate, clock_sources = configure_mx_y_rf_outputs(
            params,
            devices,
            channels,
            mapping,
            control_tec=False,
        )
        run_dir.update_config(clock_sources=clock_sources)

        def restore_probe_laser() -> None:
            report = _restore_probe_laser_state(
                devices.get("probe_laser_controller"),
                probe_laser_state,
            )
            probe_laser_restore.update(report)
            if not report["success"]:
                raise RuntimeError(str(report["error"]))

        probe_laser_guard.add(
            "恢复 DLC pro PZT/扫描状态",
            restore_probe_laser,
        )
        _prepare_probe_laser_scan(devices["probe_laser_controller"])
        device_id = str(mapping["lockin_r"]["device_id"])
        grid_summary = _acquire_grid(
            params,
            run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            actual_rates={"response_sa_s": actual_response_rate},
            grid_summary=grid_summary,
            data_files=[
                "raw/probe_detuning_grid.npz",
                "raw/point_manifest.yaml",
            ],
        )
        print(
            "Mx Y RF Probe 光功率与 PZT 失谐优化采集完成: "
            f"{run_dir.root}"
        )
        return run_dir.root
    except WorkflowCancelled as exc:
        completion_status = "cancelled"
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                grid_summary=grid_summary,
            )
        raise
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
                grid_summary=grid_summary,
            )
        raise
    finally:
        probe_laser_restore_errors = probe_laser_guard.restore()
        power_restore_error = None
        try:
            restore_mx_y_rf_laser_powers(
                devices,
                channels,
                pump_power_v=params.pump_laser_power_v,
                probe_power_v=params.probe_laser_power_v,
            )
        except Exception as exc:
            power_restore_error = str(exc)
        finish = finalize_run_safety(
            shutdown=lambda: safe_shutdown(devices, channels, params),
            completion_status=completion_status,
            failure_reason=failure_reason,
            extra_errors=(
                [
                    f"恢复 DLC pro PZT/扫描状态: {item}"
                    for item in probe_laser_restore_errors
                ]
                + (
                    [f"光功率基准恢复: {power_restore_error}"]
                    if power_restore_error
                    else []
                )
            ),
        )
        if probe_laser_restore_errors:
            print(
                "Probe 激光状态保护失败: "
                + "；".join(probe_laser_restore_errors)
            )
        if power_restore_error:
            print(f"光功率基准恢复失败: {power_restore_error}")
        if finish.shutdown_report.errors:
            print("安全关闭失败: " + "；".join(finish.shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=finish.completion_status,
                failure_reason=finish.failure_reason,
                safety_shutdown=finish.shutdown_report.to_dict(),
                probe_laser_restore={
                    **probe_laser_restore,
                    "guard_errors": probe_laser_restore_errors,
                },
                laser_power_restore={
                    "pump_power_v": params.pump_laser_power_v,
                    "probe_power_v": params.probe_laser_power_v,
                    "outputs_on": True,
                    "success": power_restore_error is None,
                    "error": power_restore_error,
                },
                device_disconnect={
                    "tec_connected": False,
                    "tec_disconnect_attempted": False,
                    "other_devices_preserved": True,
                    "errors": list(finish.shutdown_report.disconnect_errors),
                },
            )
        if (
            finish.completion_status != "completed"
            and not finish.original_exception_pending
        ):
            raise RuntimeError(
                f"实验结束但安全恢复失败: {finish.failure_reason}"
            )


def main() -> int:
    params = load_runtime_params(
        MxYRFProbeDetuningOptimizationParams
    )
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
