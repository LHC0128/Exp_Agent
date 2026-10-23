"""Probe/PZT 无硬件预检、DLC pro 初态保护与安全恢复。"""
from __future__ import annotations
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import time
from typing import Any

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_dlc_pro
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import DeviceSession
from ..mx_y_rf_sensitivity import preparation as rf_preparation
from ..mx_y_rf_sensitivity.excitation import restore_mx_y_rf_laser_powers
from . import run_format


@dataclass(frozen=True, slots=True)
class ProbeLaserState:
    """进入实验前需要原样恢复的 DLC pro PZT/扫描状态。"""
    pzt_voltage_v: float
    pzt_actual_v: float
    scan_amplitude_vpp: float
    scan_enabled: bool


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



def preflight(params, root: Path):
    """只解析配置；不会连接 DLC pro、TEC 或任何仪器。"""
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def connect(mapping, session):
    try:
        devices, channels = rf_preparation._connect_devices(mapping, session, connect_tec=False)
        controller, config = _connect_probe_laser_controller(mapping, session)
        devices["probe_laser_controller"] = controller
        state, snapshot = _snapshot_and_validate_probe_laser(controller, config)
        initial = rf_preparation._initial_state_snapshot(devices, channels)
        initial["probe_laser_controller"] = snapshot
        return devices, channels, state, initial
    except BaseException:
        session.cleanup_connection_failure()
        raise


def shutdown(devices, channels, params, laser_state):
    """各恢复独立尝试，PZT 失败也不跳过危险输出关闭。"""
    restore = _restore_probe_laser_state(devices.get("probe_laser_controller"), laser_state)
    errors = [] if restore["success"] else [f"DLC pro 状态恢复: {restore['error']}"]
    try:
        restore_mx_y_rf_laser_powers(devices, channels,
            pump_power_v=params.pump_laser_power_v, probe_power_v=params.probe_laser_power_v)
    except Exception as exc:
        errors.append(f"光功率基准恢复: {exc}")
    report = rf_preparation.safe_shutdown(devices, channels, params, disconnect_tec=False)
    report = replace(report, action_errors=(*report.action_errors, *errors))
    return report, restore
