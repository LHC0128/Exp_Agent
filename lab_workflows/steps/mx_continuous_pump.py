"""Mx 连续 Pump 与 SDS 采集设备族的共享连接与安全收尾步骤。"""

from __future__ import annotations

from typing import Any

from gs200 import GS200Instrument
from sds_acquisition import SDSAcquisition, SDSInstrument
from tec_controller import TECInstrument

from ..common import validate_safety_limit
from ..devices import create_signal_generator
from .routed_signal_generator import connect_signal_generator_routes
from .safety_shutdown import (
    DGChannelShutdown,
    DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS,
    SafetyShutdownReport,
    ShutdownAction,
    TemperatureSwitchRestore,
    run_safety_shutdown,
)
from .session import DeviceSession
from .state import StateGuard


def _set_pump_gate_on(device: Any, channel: int, voltage_v: float) -> None:
    validate_safety_limit("Time_sequence", voltage_v)
    device.set_burst_state(False, channel=channel)
    device.set_mod_state(False, channel=channel)
    device.setup_dc(voltage_v, channel=channel)
    device.set_output(True, channel=channel)


def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接除 HF2 外的 Mx 连续 Pump 与 SDS 采集设备。"""
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}

    gs_cfg = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect(
        "gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"])
    )

    z_cfg = mapping["Z_magnetic_field"]
    devices["z_field"] = session.connect(
        "z_field", z_cfg["resource"], lambda: create_signal_generator(z_cfg)
    )
    channels["z_field"] = int(z_cfg["channel"])

    x_cfg = mapping["X_magnetic_field"]
    y_cfg = mapping["Y_magnetic_field"]
    devices["xy_field"], routed_channels = connect_signal_generator_routes(
        session,
        "xy_field",
        {
            "x_field": ("X_magnetic_field", x_cfg),
            "y_rf": ("Y_magnetic_field", y_cfg),
        },
    )
    channels.update(routed_channels)

    pump_cfg = mapping["Pump_laser_power"]
    probe_cfg = mapping["Probe_laser_power"]
    devices["laser"], routed_channels = connect_signal_generator_routes(
        session,
        "laser",
        {
            "pump_laser": ("Pump_laser_power", pump_cfg),
            "probe_laser": ("Probe_laser_power", probe_cfg),
        },
    )
    channels.update(routed_channels)

    carrier_cfg = mapping["Pump_modulation"]
    gate_cfg = mapping["Time_sequence"]
    devices["pump_rf"], routed_channels = connect_signal_generator_routes(
        session,
        "pump_rf",
        {
            "pump_carrier": ("Pump_modulation", carrier_cfg),
            "pump_gate": ("Time_sequence", gate_cfg),
        },
    )
    channels.update(routed_channels)

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
    devices["tec"] = session.connect_optional(
        "tec",
        tec_cfg["resource"],
        lambda: TECInstrument(port=tec_cfg["resource"]),
        device_label="TEC103",
    )
    return devices, channels


def safe_shutdown(
    devices: dict[str, Any],
    channels: dict[str, int],
    params: Any,
    main_field_guard: StateGuard | None = None,
    *, disconnect_tec: bool = True, configure_pump: bool = True, scope_channel: int | None = None,
) -> SafetyShutdownReport:
    """停止 SDS、恢复 DC 耦合、关闭辅助场并恢复温控与 GS200 状态。"""
    dg_channels: list[DGChannelShutdown] = []
    if devices.get("z_field") is not None and "z_field" in channels:
        dg_channels.append(
            DGChannelShutdown(
                devices["z_field"],
                channels["z_field"],
                "Z_magnetic_field",
                "Z 辅助场",
            )
        )
    if devices.get("xy_field") is not None:
        if "x_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["x_field"],
                    "X_magnetic_field",
                    "X 磁场",
                )
            )
        if "y_rf" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    devices["xy_field"],
                    channels["y_rf"],
                    "Y_magnetic_field",
                    "Y 磁场",
                )
            )

    extra_actions: list[ShutdownAction] = []
    scope = devices.get("scope")
    if scope is not None:
        extra_actions.append(ShutdownAction("停止示波器触发失败", scope.trigger_stop))
        extra_actions.append(
            ShutdownAction(
                "恢复示波器直流耦合失败",
                lambda: scope.set_channel_coupling(scope_channel if scope_channel is not None else params.scope_pd_channel, "DC"),
            )
        )

    pump_rf = devices.get("pump_rf")
    if configure_pump and pump_rf is not None and "pump_gate" in channels:
        extra_actions.append(
            ShutdownAction(
                "保持 Pump RF 开关 5V 常开失败",
                lambda: _set_pump_gate_on(
                    pump_rf,
                    channels["pump_gate"],
                    params.pump_gate_voltage_v,
                ),
            )
        )
    if configure_pump and pump_rf is not None and "pump_carrier" in channels:

        def keep_pump_carrier_on() -> None:
            validate_safety_limit(
                "Pump_modulation", params.pump_carrier_amplitude_vpp
            )
            pump_rf.setup_sine(
                params.pump_carrier_frequency_hz,
                params.pump_carrier_amplitude_vpp,
                offset=0.0,
                phase=0.0,
                channel=channels["pump_carrier"],
            )
            pump_rf.set_output(True, channel=channels["pump_carrier"])

        extra_actions.append(
            ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on)
        )

    temperature_restore = None
    if devices.get("temp_switch") is not None and "temp_switch" in channels:
        temperature_restore = TemperatureSwitchRestore(
            devices["temp_switch"], channels["temp_switch"]
        )
    report = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=temperature_restore,
        extra_actions=extra_actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )
    guard_errors = tuple(main_field_guard.restore()) if main_field_guard else ()
    return SafetyShutdownReport(
        action_errors=(*report.action_errors, *guard_errors),
        disconnect_errors=report.disconnect_errors,
        preserved_outputs=report.preserved_outputs,
    )