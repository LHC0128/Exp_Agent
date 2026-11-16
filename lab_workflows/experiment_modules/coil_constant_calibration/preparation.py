"""无硬件预检、设备连接、主磁场双模式、初始输出与安全收尾；导入不连接仪器。"""
from __future__ import annotations

from typing import Any

from aigtek_1200b import Aigtek1200BInstrument
from gs200 import GS200Instrument
from oscilloscope import create_oscilloscope, create_scope_acquisition
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin, create_signal_generator
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
                      TemperatureSwitchRestore, connect_signal_generator_routes, run_safety_shutdown,
                      set_temperature_switch, synchronize_connected_clocks, configure_temperature_control,
                      disconnect_devices, initialize_lockin_inputs)
from ...steps.coil_drive_chain import (SafetyAbort, amplifier_safety_check, configure_amplifier,
                                       current_limit, set_amp_output, set_dc, sleep)
from .models import MAIN_FIELD_LIMIT_A, CoilConstantCalibrationParams
from .run_format import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, EXPERIMENT_ID, REQUIRED_MAPPING_KEYS,
                         SCOPE_KEY, SOURCE_KEY)

__all__ = [
    "SafetyAbort", "clocks", "configure_initial_outputs", "configure_main_field",
    "configure_temperature", "connect", "disconnect_all", "preflight", "safe_shutdown", "sleep",
]

SCOPE_MODELS = {"DHO900", "DHO914", "DHO914S", "DHO924", "DHO924S"}


def preflight(params, root):
    """参数、映射、器件类型、锁相能力与安全限值预检；不连接硬件。"""
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    expected = {
        "main_magnetic_field": ("gs200", None),
        SOURCE_KEY: ("signal_generator", None),
        AMPLIFIER_KEY: ("power_amplifier", {"1200B"}),
        SCOPE_KEY: ("oscilloscope", SCOPE_MODELS),
        "lockin_r": ("lockin_amplifier", None),
        "temperature": ("tec_controller", None),
    }
    for key, (kind, models) in expected.items():
        config = mapping[key]
        if config.get("instrument") != kind or (models and str(config.get("model", "")).upper() not in models):
            raise ValueError(f"{key} 必须映射到 {kind}"
                             f"{'（' + '/'.join(sorted(models)) + '）' if models else ''}")
    current_limit(load_safety_limits(root))
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    params.branch_counts()
    return params, mapping


def _identity(device: Any) -> str | None:
    try:
        value = device.idn() if callable(getattr(device, "idn", None)) else device.idn
        return str(value)
    except Exception:
        return None


def _snapshot_main_field(gs200: Any) -> dict[str, Any]:
    """记录 GS200 运行前状态；任一读取失败只记录错误，不阻断实验。"""
    snapshot: dict[str, Any] = {}
    for name, getter in (("source_function", gs200.get_source_function), ("current_a", gs200.get_current),
                         ("output_on", gs200.get_output), ("current_range_a", gs200.get_current_range),
                         ("current_limit_a", gs200.get_current_limit)):
        try:
            snapshot[name] = getter()
        except Exception as exc:  # 只记录，不因回读失败阻断
            snapshot[name] = f"read_error: {exc}"
    return snapshot


def connect(ctx):
    """连接全部设备、写入功放设置并记录 GS200 初态；功放输出保持关闭。"""
    m, d, session, channels = ctx.mapping, ctx.devices, ctx.device_session, ctx.channels
    gs_cfg = m["main_magnetic_field"]
    d["gs200"] = session.connect("gs200", gs_cfg["resource"], lambda: GS200Instrument(gs_cfg["resource"]))

    z_cfg = m[SOURCE_KEY]
    d["sg"] = session.connect("sg", f"{z_cfg['resource']}#{z_cfg['channel']}",
                              lambda: create_signal_generator(z_cfg))
    channels["z_field"] = int(z_cfg["channel"])

    amp = m[AMPLIFIER_KEY]
    d["amp"] = session.connect("amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
        amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
        safety_check=amplifier_safety_check(ctx, preflight)))

    scope_cfg = m[SCOPE_KEY]
    d["scope"] = session.connect("scope", str(scope_cfg["resource"]), lambda: create_oscilloscope(scope_cfg))
    acquirer = create_scope_acquisition(d["scope"], check_cancelled=check_cancelled)
    acquirer.use_auto_depth(ctx.params.max_memory_depth)
    d["acquirer"] = session.bind("acquirer", acquirer)

    x_cfg, rf_cfg = m["X_magnetic_field"], m["rf_coil"]
    d["xy_field"], routed = connect_signal_generator_routes(
        session, "xy_field",
        {"x_field": ("X_magnetic_field", x_cfg), "y_rf": ("rf_coil", rf_cfg)})
    channels.update(routed)

    pump_cfg, probe_cfg = m["Pump_laser_power"], m["Probe_laser_power"]
    d["laser"], routed = connect_signal_generator_routes(
        session, "laser",
        {"pump_laser": ("Pump_laser_power", pump_cfg), "probe_laser": ("Probe_laser_power", probe_cfg)})
    channels.update(routed)

    carrier_cfg, gate_cfg = m["Pump_modulation"], m["Time_sequence"]
    d["pump_rf"], routed = connect_signal_generator_routes(
        session, "pump_rf",
        {"pump_carrier": ("Pump_modulation", carrier_cfg), "pump_gate": ("Time_sequence", gate_cfg)})
    channels.update(routed)

    temp_cfg = m["Temp_Switch"]
    d["temp_switch"] = session.connect("temp_switch", temp_cfg["resource"],
                                       lambda: create_signal_generator(temp_cfg))
    channels["temp_switch"] = int(temp_cfg["channel"])

    hf_cfg = m["lockin_r"]
    d["hf2"] = session.connect("hf2", f"hf2://{hf_cfg.get('host', '127.0.0.1')}/{hf_cfg['device_id']}",
                               lambda: create_lockin(hf_cfg))
    initialize_lockin_inputs(d["hf2"])

    tec_cfg = m["temperature"]
    d["tec"] = session.connect_optional("tec", tec_cfg["resource"],
                                        lambda: TECInstrument(port=tec_cfg["resource"]),
                                        device_label="TEC103")
    configure_amplifier(ctx)
    ctx.state["main_field_before"] = _snapshot_main_field(d["gs200"])
    ctx.outputs["主场"] = "已记录运行前状态"
    ctx.outputs["DHO900"] = "已连接"


def clocks(ctx):
    return synchronize_connected_clocks(
        {"z_field": ctx.devices["sg"], "xy_field": ctx.devices["xy_field"],
         "laser": ctx.devices["laser"], "pump_rf": ctx.devices["pump_rf"],
         "temp_switch": ctx.devices["temp_switch"], "hf2": ctx.devices["hf2"]},
        ctx.mapping,
        {"z_field": SOURCE_KEY, "xy_field": "rf_coil", "laser": "Pump_laser_power",
         "pump_rf": "Pump_modulation", "temp_switch": "Temp_Switch", "hf2": "lockin_r"},
        settle_wait=sleep)


def configure_main_field(ctx) -> dict[str, Any]:
    """主磁场双模式：开启时校验并写电流，关闭时只关 GS200 输出。"""
    p, gs200, mapping = ctx.params, ctx.devices["gs200"], ctx.mapping
    cfg = mapping["main_magnetic_field"]
    if p.main_field_enabled:
        gs200.set_output(False)  # 先断输出再改量程/电流，避免带载阶跃
        if cfg.get("source_function"):
            gs200.set_source_function(str(cfg["source_function"]))
        validate_safety_limit("main_magnetic_field", float(p.main_field_ma))
        gs200.set_current_limit(MAIN_FIELD_LIMIT_A)
        gs200.set_current(float(p.main_field_ma) / 1000.0)
        gs200.set_output(True)
        ctx.outputs["主场"] = f"{p.main_field_ma:g} mA + ON（参考 {p.main_field_center_hz / 1e3:g} kHz）"
        record = dict(enabled=True, current_ma=float(p.main_field_ma),
                      center_hz=float(p.main_field_center_hz))
    else:
        gs200.set_output(False)
        ctx.outputs["主场"] = "OFF（未写电流）"
        record = dict(enabled=False, current_ma=None, center_hz=float(p.main_field_center_hz))
    ctx.state["main_field"] = record
    return record


def configure_initial_outputs(ctx):
    """A05：Z 通道 DC 0 V、X/Z 辅助场归零、光功率、温控开关 5 V，再设主磁场。"""
    p, d, ch = ctx.params, ctx.devices, ctx.channels
    sg = d["sg"]
    sg.set_burst_state(False)
    sg.set_mod_state(False)
    validate_safety_limit(SOURCE_KEY, 0.0)
    sg.setup_dc(0.0)
    ctx.outputs["Z 通道"] = "DC 0 V + ON（功放输入）"

    xy = d["xy_field"]
    for name, key in (("x_field", "X_magnetic_field"), ("y_rf", "rf_coil")):
        validate_safety_limit(key, 0.0)
        xy.set_burst_state(False, channel=ch[name])
        xy.set_mod_state(False, channel=ch[name])
        xy.setup_dc(0.0, channel=ch[name])
        xy.set_output(False, channel=ch[name])
    ctx.outputs["Y RF"] = "OFF"

    laser = d["laser"]
    validate_safety_limit("Pump_laser_power", p.pump_laser_power_v)
    laser.setup_dc(p.pump_laser_power_v, channel=ch["pump_laser"])
    laser.set_output(True, channel=ch["pump_laser"])
    validate_safety_limit("Probe_laser_power", p.probe_laser_power_v)
    laser.setup_dc(p.probe_laser_power_v, channel=ch["probe_laser"])
    laser.set_output(True, channel=ch["probe_laser"])

    set_temperature_switch(d["temp_switch"], True, channel=ch["temp_switch"])
    ctx.outputs["温控"] = "Temp_Switch 5 V + ON"
    configure_main_field(ctx)


def configure_temperature(ctx):
    return configure_temperature_control(ctx.devices.get("tec"), ctx.params.temperature_c, channel=1)


def safe_shutdown(ctx) -> list[str]:
    """先关 1200B，再归零关闭辅助场、恢复温控与 Pump，最后停止示波器；保留 GS200 本次设定。"""
    d, ch, p = ctx.devices, ctx.channels, ctx.params
    first = run_safety_shutdown(
        extra_actions=(ShutdownAction("发送 1200B 关闭命令", lambda: d["amp"].set_output(False)),)
        if d.get("amp") else (),
        preserved_outputs=())
    dg_channels: list[DGChannelShutdown] = []
    if d.get("sg") and "z_field" in ch:
        dg_channels.append(DGChannelShutdown(d["sg"], ch["z_field"], SOURCE_KEY, "Z 线圈驱动",
                                             disable_sync=True))
    if d.get("xy_field"):
        if "x_field" in ch:
            dg_channels.append(DGChannelShutdown(d["xy_field"], ch["x_field"], "X_magnetic_field", "X 磁场"))
        if "y_rf" in ch:
            dg_channels.append(DGChannelShutdown(d["xy_field"], ch["y_rf"], "rf_coil", "Y RF 场"))
    ctx.state["amp_output_on"] = False

    extra: list[ShutdownAction] = []
    pump_rf = d.get("pump_rf")
    if pump_rf is not None and "pump_gate" in ch:
        def keep_pump_gate_on() -> None:
            validate_safety_limit("Time_sequence", p.pump_gate_voltage_v)
            pump_rf.set_burst_state(False, channel=ch["pump_gate"])
            pump_rf.set_mod_state(False, channel=ch["pump_gate"])
            pump_rf.setup_dc(p.pump_gate_voltage_v, channel=ch["pump_gate"])
            pump_rf.set_output(True, channel=ch["pump_gate"])
        extra.append(ShutdownAction("保持 Pump RF 开关 5V 常开失败", keep_pump_gate_on))
    if pump_rf is not None and "pump_carrier" in ch:
        def keep_pump_carrier_on() -> None:
            validate_safety_limit("Pump_modulation", p.pump_carrier_amplitude_vpp)
            pump_rf.setup_sine(p.pump_carrier_frequency_hz, p.pump_carrier_amplitude_vpp,
                               offset=0.0, phase=0.0, channel=ch["pump_carrier"])
            pump_rf.set_output(True, channel=ch["pump_carrier"])
        extra.append(ShutdownAction("保持 Pump 100MHz 载波开启失败", keep_pump_carrier_on))
    if d.get("scope") is not None:
        extra.append(ShutdownAction("停止 DHO900", d["scope"].trigger_stop))

    temperature_restore = None
    if d.get("temp_switch") is not None and "temp_switch" in ch:
        temperature_restore = TemperatureSwitchRestore(d["temp_switch"], ch["temp_switch"])
    second = run_safety_shutdown(
        dg_channels=dg_channels, temperature_switch=temperature_restore, extra_actions=extra,
        disconnect_targets=(DisconnectTarget("TEC", d.get("tec")),) if d.get("tec") else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"))
    return [*first.action_errors, *second.action_errors]


def disconnect_all(ctx) -> list[str]:
    targets = [DisconnectTarget(f"设备{index}", device)
               for index, device in enumerate(ctx.device_session.unique_devices())]
    return list(disconnect_devices(targets))


def shutdown_outputs(ctx) -> dict[str, Any]:
    """执行安全动作并把结果写入状态；返回可写入配置的摘要。"""
    errors = safe_shutdown(ctx)
    ctx.state["safety_shutdown"] = dict(completed=not errors, errors=errors)
    ctx.outputs.update({
        "功放": "未知" if errors else "OFF（已发送 AOF）",
        "Z 通道": "未知" if errors else "DC 0 V + OFF，同步 OFF",
        "Y RF": "未知" if errors else "OFF",
        "DHO900": "未知" if errors else "STOP",
        "温控": "未知" if errors else "Temp_Switch 5 V + ON",
    })
    return ctx.state["safety_shutdown"]
