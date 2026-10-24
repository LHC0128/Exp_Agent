"""无硬件预检、设备路由、参考时钟、Pump 激励与用户确认的 X 场归零收尾。"""
import numpy as np
from tec_controller import TECInstrument
from sds_acquisition import SDSInstrument, SDSAcquisition
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...instrument_config import validate_mapping_usage
from ...steps import (connect_signal_generator_routes, synchronize_connected_clocks, configure_temperature_control,
    set_temperature_switch, run_safety_shutdown, DGChannelShutdown, TemperatureSwitchRestore, DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS, ShutdownAction)
from .run_format import REQUIRED_MAPPING_KEYS

SIGNALS = ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Probe_laser_power",
           "Pump_modulation", "Time_sequence", "Temp_Switch")


def checked(ctx, key, *values):
    limits = load_safety_limits(ctx.root)
    for value in values:
        validate_safety_limit(key, value, limits)


def preflight(params, root):
    params = type(params).from_external(params.to_external(), schema_version=params.schema_version)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    limits = load_safety_limits(root)
    spans = {"X_magnetic_field": (0, params.x_field_amplitude), "Y_magnetic_field": (0,), "Z_magnetic_field": (0,),
        "Probe_laser_power": (params.probe_power, *params.probe_values), "Temp_Switch": (0, 5),
        "Pump_modulation": (params.pump_mod_amplitude, -params.pump_mod_amplitude/2, params.pump_mod_amplitude/2),
        "Time_sequence": (0, 5), "temperature": (params.tec_temperature,)}
    for key, values in spans.items():
        if any(limits.get(key, {}).get(bound) is None for bound in ("min", "max")):
            raise ValueError(f"{key} 缺少明确安全上下限")
        for value in values:
            if not np.isfinite(value):
                raise ValueError(f"{key} 必须为有限数值")
            validate_safety_limit(key, value, limits)
    return params, mapping


def connect(ctx):
    cfg, session = ctx.mapping, ctx.device_session
    ctx.devices["sg"], ctx.channels = connect_signal_generator_routes(session, "t1",
        {key: (key, cfg[key]) for key in SIGNALS})
    ctx.devices["scope"] = session.connect("scope", cfg["scope_waveform"]["resource"],
        lambda: SDSInstrument(cfg["scope_waveform"]["resource"]))
    ctx.devices["acquirer"] = SDSAcquisition(ctx.devices["scope"], check_cancelled=check_cancelled)
    tec = cfg["temperature"]
    ctx.devices["tec"] = session.connect_optional("tec", tec["resource"],
        lambda: TECInstrument(port=tec["resource"]), device_label="TEC103")


def clocks(ctx):
    physical = {route.mapping_key: route.device for route in ctx.devices["sg"].iter_physical_routes()}
    return synchronize_connected_clocks(physical, ctx.mapping, {key: key for key in physical})


def switch(ctx, enabled):
    checked(ctx, "Temp_Switch", 5 if enabled else 0)
    set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"])
    ctx.outputs["温控"] = "5 V DC + ON" if enabled else "0 V DC + ON"


def initial_outputs(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    switch(ctx, True)
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Probe_laser_power"):
        value = p.x_field_amplitude if key == "X_magnetic_field" else p.probe_power if key == "Probe_laser_power" else 0
        checked(ctx, key, value)
        sg.set_burst_state(False, channel=ch[key])
        sg.set_mod_state(False, channel=ch[key])
        sg.setup_dc(value, channel=ch[key])
        sg.set_output(key in ("X_magnetic_field", "Probe_laser_power"), channel=ch[key])
    ctx.outputs.update(X=f"{p.x_field_amplitude:g} V DC + ON", Y="0 V + OFF", Z="0 V + OFF",
                       Probe=f"{p.probe_power:g} V DC + ON")
    checked(ctx, "temperature", p.tec_temperature)
    status = configure_temperature_control(ctx.devices.get("tec"), p.tec_temperature,
        channel=ctx.mapping["temperature"].get("channel", 1))
    ctx.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)


def pump(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in ("Pump_modulation", "Time_sequence"):
        checked(ctx, key, p.pump_mod_amplitude if key == "Pump_modulation" else 5)
        sg.set_burst_state(False, channel=ch[key])
        sg.set_mod_state(False, channel=ch[key])
    checked(ctx, "Pump_modulation", p.pump_mod_amplitude, -p.pump_mod_amplitude/2, p.pump_mod_amplitude/2)
    sg.apply_wave("SINusoid", channel=ch["Pump_modulation"], freq=100e6, amp=p.pump_mod_amplitude, offset=0)
    sg.set_output(True, channel=ch["Pump_modulation"])
    checked(ctx, "Time_sequence", 0, 5)
    sg.apply_wave("SQUare", channel=ch["Time_sequence"], freq=p.pump_switch_freq, amp=5, offset=2.5, phase=180)
    sg.set_square_dcycle(50, channel=ch["Time_sequence"])
    sg.set_sync_state(True, channel=ch["Time_sequence"])
    sg.set_output(True, channel=ch["Time_sequence"])
    ctx.outputs.update(Pump=f"100 MHz / {p.pump_mod_amplitude:g} Vpp + ON", 门控=f"{p.pump_switch_freq:g} Hz / 50% / 180° + ON，SYNC ON")


def verify_pump(ctx):
    sg, ch = ctx.devices["sg"], ctx.channels
    for key, frequency in (("Pump_modulation", 100e6), ("Time_sequence", ctx.params.pump_switch_freq)):
        if (not sg.get_output(channel=ch[key]) or sg.get_burst_state(channel=ch[key])
                or not np.isclose(sg.get_frequency(channel=ch[key]), frequency, rtol=1e-6)):
            raise ValueError("Pump 模式已改变，请重跑 A05 与 A06")


def shutdown(ctx, *, disconnect_tec=True):
    sg = ctx.devices.get("sg")
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(sg, ctx.channels[key], key, key) for key in
            ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field") if key in ctx.channels],
        temperature_switch=TemperatureSwitchRestore(sg, ctx.channels["Temp_Switch"]) if sg else None,
        extra_actions=(ShutdownAction("停止示波器", ctx.devices["scope"].trigger_stop),) if ctx.devices.get("scope") else (),
        disconnect_targets=(DisconnectTarget("TEC103", ctx.devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"))
