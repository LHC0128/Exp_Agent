"""无硬件预检、映射连接和专用安全收尾。"""
from __future__ import annotations
from typing import Any
from sds_acquisition import SDSAcquisition, SDSInstrument
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...steps import DGChannelShutdown, DeviceSession, DisconnectTarget, run_safety_shutdown, validate_z_trigger_mapping
from ...steps.routed_signal_generator import connect_signal_generator_routes


def preflight(params, root):
    """校验纯电学实验的参数、路由和限值，绝不连接仪器。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    if any(character in params.run_tag for character in "/\\:\x00"):
        raise ValueError("运行标签不能包含路径分隔符")
    mapping, limits = load_mapping(root), load_safety_limits(root)
    validate_z_trigger_mapping(mapping)
    if mapping["Z_magnetic_field"].get("instrument") != "signal_generator":
        raise ValueError("Z_magnetic_field 必须映射到信号发生器")
    if mapping["scope_waveform"].get("instrument") != "sds_acquisition":
        raise ValueError("scope_waveform 必须映射到 SDS 示波器")
    for key, amplitude, offset in (("Z_magnetic_field", params.drive_amplitude_vpp, params.drive_offset_v),
                                   ("Time_sequence_2", params.trigger_amplitude_vpp, params.trigger_offset_v)):
        for value in (offset - amplitude / 2, offset + amplitude / 2):
            validate_safety_limit(key, value, limits)
    return params, mapping, limits

def _connect_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """按物理量映射连接同一台 DG4000 和 SDS。"""
    z_cfg = mapping["Z_magnetic_field"]
    trigger_cfg = mapping["Time_sequence_2"]
    if z_cfg.get("instrument") != "signal_generator":
        raise ValueError("Z_magnetic_field 必须映射到信号发生器")
    if trigger_cfg.get("instrument") != "signal_generator":
        raise ValueError("Time_sequence_2 必须映射到信号发生器")
    trigger_channel = validate_z_trigger_mapping(mapping)
    z_control, channels = connect_signal_generator_routes(
        session,
        "z_control",
        {
            "z_field": ("Z_magnetic_field", z_cfg),
            "trigger": ("Time_sequence_2", trigger_cfg),
        },
    )
    scope_cfg = mapping["scope_waveform"]
    scope = session.connect(
        "scope",
        str(scope_cfg["resource"]),
        lambda: SDSInstrument(str(scope_cfg["resource"])),
    )
    devices = {
        "z_control": z_control,
        "scope": scope,
        "acquirer": session.bind("acquirer", SDSAcquisition(scope)),
    }
    if channels["trigger"] != trigger_channel:
        raise ValueError("Time_sequence_2 路由通道与映射端点不一致")
    return devices, channels

def safe_shutdown(devices: dict[str, Any], channels: dict[str, int]) -> Any:
    """停止 SDS，关闭 Z 并保持共同触发当前状态。"""
    scope = devices.get("scope")
    z_control = devices.get("z_control")
    dg_channels = []
    if z_control is not None:
        if "z_field" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    z_control,
                    channels["z_field"],
                    "Z_magnetic_field",
                    "Z 正弦驱动",
                )
            )
        if "trigger" in channels:
            dg_channels.append(
                DGChannelShutdown(
                    z_control,
                    channels["trigger"],
                    "Time_sequence_2",
                    "共同触发",
                )
            )

    def stop_scope() -> None:
        if scope is not None:
            scope.trigger_stop()

    from ...steps.safety_shutdown import ShutdownAction

    return run_safety_shutdown(
        dg_channels=dg_channels,
        extra_actions=(ShutdownAction("停止 SDS 采集", stop_scope),),
        disconnect_targets=(
            DisconnectTarget("SDS", scope),
            DisconnectTarget("Z 信号源", z_control),
        ),
    )
