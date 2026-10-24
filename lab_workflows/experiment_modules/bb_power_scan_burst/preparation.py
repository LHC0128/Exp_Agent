"""无副作用预检、按资源复用连接、初始条件及明确的输出保留策略。"""
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from sds_acquisition import SDSInstrument, SDSAcquisition
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (connect_signal_generator_routes, synchronize_connected_clocks, initialize_lockin_inputs,
    configure_temperature_control, set_temperature_switch, DGChannelShutdown, TemperatureSwitchRestore,
    DisconnectTarget, run_safety_shutdown, STANDARD_PRESERVED_OUTPUTS)
from . import run_format

SAFE_SIGNALS = ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "X_magnetic_field_AM", "Y_magnetic_field_AM")
SIGNALS = ("Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence", "Temp_Switch")


def safe_signals(mapping):
    keys = [key for key in SAFE_SIGNALS if key in mapping]
    if "Y_magnetic_field" not in mapping and "rf_coil" in mapping:
        keys.append("rf_coil")
    return tuple(keys)


def checked(ctx, key, *values):
    limits = load_safety_limits(ctx.root)
    for value in values:
        validate_safety_limit(key, value, limits)


def preflight(params, root):
    # 同时校验直接构造模型的类型，避免 NaN/布尔值绕过 GUI 转换。
    params = type(params).from_external(params.to_external(), schema_version=params.schema_version)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    required = (*run_format.REQUIRED_MAPPING_KEYS, *safe_signals(mapping), *(("lockin_r",) if params.use_hf2_monitor else ()))
    validate_mapping_usage(required, root)
    if params.use_hf2_monitor:
        require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)
    limits = load_safety_limits(root)
    spans = {"main_magnetic_field": (params.z_field_setpoint,), "temperature": (params.tec_temperature,),
        "Pump_laser_power": tuple(params.pump_power), "Probe_laser_power": (params.probe_power,),
        "Pump_modulation": (params.aom_carrier_amplitude_v, params.aom_carrier_offset_v-params.aom_carrier_amplitude_v/2,
                            params.aom_carrier_offset_v+params.aom_carrier_amplitude_v/2),
        "PUMP_MOD_DUTY": (params.fixed_duty_cycle,), "Time_sequence": (params.pump_rf_gate_low_v, params.pump_rf_gate_high_v),
        "Temp_Switch": (0, 5), **{key: (0,) for key in safe_signals(mapping)}}
    for key, values in spans.items():
        if any(limits.get(key, {}).get(bound) is None for bound in ("min", "max")):
            raise ValueError(f"{key} 缺少明确安全上下限")
        for value in values:
            validate_safety_limit(key, value, limits)
    if params.pump_rf_gate_high_v <= params.pump_rf_gate_low_v:
        raise ValueError("门控高电平必须高于低电平")
    if mapping["main_magnetic_field"].get("source_function", "").upper() not in ("CURR", "CURRENT"):
        raise ValueError("主磁场必须使用电流源模式")
    return params, mapping


def connect(ctx):
    cfg, session = ctx.mapping, ctx.device_session
    ctx.devices["gs"] = session.connect("gs", cfg["main_magnetic_field"]["resource"],
        lambda: GS200Instrument(cfg["main_magnetic_field"]["resource"]))
    ctx.devices["sg"], ctx.channels = connect_signal_generator_routes(session, "bb-power",
        {key: (key, cfg[key]) for key in (*SIGNALS, *safe_signals(cfg))})
    tec = cfg["temperature"]
    ctx.devices["tec"] = session.connect_optional("tec", tec["resource"], lambda: TECInstrument(port=tec["resource"]), device_label="TEC103")
    ctx.devices["scope"] = session.connect("scope", cfg["scope_waveform"]["resource"], lambda: SDSInstrument(cfg["scope_waveform"]["resource"]))
    ctx.devices["acquirer"] = SDSAcquisition(ctx.devices["scope"])
    if ctx.params.use_hf2_monitor:
        lockin = cfg["lockin_r"]
        ctx.devices["lockin"] = session.connect("lockin", f'{lockin["model"]}://{lockin["device_id"]}', lambda: create_lockin(lockin))
        initialize_lockin_inputs(ctx.devices["lockin"])


def clocks(ctx):
    physical = {route.mapping_key: route.device for route in ctx.devices["sg"].iter_physical_routes()}
    if "lockin" in ctx.devices:
        physical["lockin_r"] = ctx.devices["lockin"]
    return synchronize_connected_clocks(physical, ctx.mapping, {key: key for key in physical})


def switch(ctx, enabled):
    checked(ctx, "Temp_Switch", 5 if enabled else 0)
    set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"])
    ctx.outputs["温控"] = "5 V DC + ON" if enabled else "0 V DC + ON"


def initial_outputs(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in safe_signals(ctx.mapping):
        checked(ctx, key, 0)
        sg.set_output(False, channel=ch[key])
    for key, value in (("Pump_laser_power", p.pump_power[0]), ("Probe_laser_power", p.probe_power)):
        checked(ctx, key, value)
        sg.setup_dc(value, channel=ch[key]); sg.set_output(True, channel=ch[key])
    ctx.outputs["Pump"] = f"{p.pump_power[0]:g} V DC + ON"
    ctx.outputs["Probe"] = f"{p.probe_power:g} V DC + ON"
    checked(ctx, "main_magnetic_field", p.z_field_setpoint)
    gs = ctx.devices["gs"]
    gs.set_source_function(ctx.mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(load_safety_limits(ctx.root)["main_magnetic_field"]["max"]/1000)
    gs.set_current(p.z_field_setpoint/1000); gs.set_output(True)
    ctx.outputs["主场"] = f"{p.z_field_setpoint:g} mA + ON"
    switch(ctx, True)
    checked(ctx, "temperature", p.tec_temperature)
    status = configure_temperature_control(ctx.devices.get("tec"), p.tec_temperature, channel=ctx.mapping["temperature"].get("channel", 1))
    ctx.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)


def shutdown(ctx, *, disconnect_tec=True):
    sg = ctx.devices.get("sg")
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(sg, ctx.channels[key], key, key) for key in safe_signals(ctx.mapping) if key in ctx.channels],
        temperature_switch=TemperatureSwitchRestore(sg, ctx.channels["Temp_Switch"]) if sg else None,
        disconnect_targets=(DisconnectTarget("TEC103", ctx.devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"))
