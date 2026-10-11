"""沿用本实验明确的安全收尾声明。"""
from ...steps import DGChannelShutdown, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, TemperatureSwitchRestore, run_safety_shutdown

def safe_scan_outputs_off(devices):
    """声明本实验需要关闭和保留的输出，交给共享安全步骤执行。"""
    return run_safety_shutdown(
        dg_channels=(
            DGChannelShutdown(devices.get("dg_comp"), 1, "X_magnetic_field", "X 正弦控制"),
            DGChannelShutdown(devices.get("dg_comp"), 2, "Y_magnetic_field", "Y 正弦控制"),
            DGChannelShutdown(devices.get("dg_sweep"), 1, "Z_magnetic_field", "Z 场"),
            DGChannelShutdown(devices.get("dg_sweep"), 2, "Time_sequence_2", "X/Y 共用触发"),
        ),
        temperature_switch=TemperatureSwitchRestore(devices.get("dg_temp"), 2),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )


