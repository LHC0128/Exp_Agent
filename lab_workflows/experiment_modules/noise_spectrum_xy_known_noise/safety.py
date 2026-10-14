"""已知噪声关闭共用触发；保留主场、光功率及 Pump 门控。"""
from ...steps import DGChannelShutdown, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, TemperatureSwitchRestore, run_safety_shutdown


def safe_scan_outputs_off(devices):
    return run_safety_shutdown(
        dg_channels=(DGChannelShutdown(devices.get("dg_comp"), 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(devices.get("dg_comp"), 2, "Y_magnetic_field", "Y 正弦控制"),
            DGChannelShutdown(devices.get("dg_sweep"), 1, "Z_magnetic_field", "Z 噪声"),
            DGChannelShutdown(devices.get("dg_sweep"), 2, "Time_sequence_2", "XY 共用触发")),
        temperature_switch=TemperatureSwitchRestore(devices.get("dg_temp"), 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        shutdown_timing_outputs=("Time_sequence_2",),
        preserved_outputs=tuple(key for key in (*STANDARD_PRESERVED_OUTPUTS, "Time_sequence") if key != "Time_sequence_2"))


def safe_connection_outputs_off(session, mapping):
    """连接中途失败时，从已连接物理端点收尾，再由连接步骤释放通信。"""
    specs = []
    for semantic, key in (("dg_comp", "X_magnetic_field"), ("dg_comp", "Y_magnetic_field"),
                          ("dg_sweep", "Z_magnetic_field"), ("dg_sweep", "Time_sequence_2")):
        name = f"{semantic}:{key}"
        if name in session:
            specs.append(DGChannelShutdown(session.get(name), int(mapping[key]["channel"]), key, key))
    temp = "dg_temp:Temp_Switch"
    restore = TemperatureSwitchRestore(session.get(temp), int(mapping["Temp_Switch"]["channel"])) if temp in session else None
    return run_safety_shutdown(dg_channels=specs, temperature_switch=restore,
                               shutdown_timing_outputs=("Time_sequence_2",))
