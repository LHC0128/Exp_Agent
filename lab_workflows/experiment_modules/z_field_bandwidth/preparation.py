"""无硬件预检、设备路由、时钟和统一安全收尾。"""
import math
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...lockin_control import apply_settings
from ...steps import (connect_signal_generator_routes, initialize_lockin_inputs,
    synchronize_connected_clocks, configure_temperature_control, set_temperature_switch,
    DGChannelShutdown, TemperatureSwitchRestore, DisconnectTarget, run_safety_shutdown)
from . import run_format

SIGNALS = ("Z_magnetic_field", "Time_sequence_2", "X_magnetic_field", "Y_magnetic_field",
           "Pump_laser_power", "Probe_laser_power",
           "Pump_modulation", "Time_sequence", "Temp_Switch")


def checked(ctx, key, *values):
    limits = load_safety_limits(ctx.root)
    for value in values:
        validate_safety_limit(key, value, limits)


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)
    limits = load_safety_limits(root)
    spans = {
        "main_magnetic_field": (params.main_magnetic_field,), "temperature": (params.temperature,),
        "Pump_laser_power": (params.pump_laser_power,), "Probe_laser_power": (params.probe_laser_power,),
        "Temp_Switch": (0, 5), "Z_magnetic_field": (-params.z_drive_amplitude/2, params.z_drive_amplitude/2),
        "X_magnetic_field": (params.x_magnetic_field, 0),
        "Y_magnetic_field": (params.y_magnetic_field, 0),
        "Pump_modulation": (-params.pump_mod_amplitude, params.pump_mod_amplitude),
        "PUMP_MOD_DUTY": (params.pump_mod_duty,),
        "Time_sequence": (params.rf_gate_offset-params.rf_gate_amplitude/2, params.rf_gate_offset+params.rf_gate_amplitude/2),
        "Time_sequence_2": (-params.time_sequence/2, params.time_sequence/2),
    }
    # HF2 无硬件输出限幅，依据主输入量程保守检查 Aux 的整个包络。
    aux_key = f'{mapping["lockin_r"]["device_library_id"]}_aux_output_{params.auxout_index}'
    swing = abs(params.auxout_scale) * math.sqrt(2) * params.hf2_demod0_signal_range
    spans[aux_key] = (params.auxout_offset-swing, params.auxout_offset+swing)
    for key, values in spans.items():
        if any(limits.get(key, {}).get(bound) is None for bound in ("min", "max")):
            raise ValueError(f"{key} 缺少明确安全上下限")
        for value in values:
            validate_safety_limit(key, value, limits)
    if mapping["main_magnetic_field"].get("source_function", "").upper() not in ("CURR", "CURRENT"):
        raise ValueError("本实验主场必须为电流源模式")
    return params, mapping


def connect(ctx):
    cfg, session = ctx.mapping, ctx.device_session
    gs = cfg["main_magnetic_field"]
    ctx.devices["gs"] = session.connect("gs", gs["resource"], lambda: GS200Instrument(gs["resource"]))
    ctx.devices["sg"], ctx.channels = connect_signal_generator_routes(session, "z-bandwidth",
        {key: (key, cfg[key]) for key in SIGNALS})
    tec = cfg["temperature"]
    ctx.devices["tec"] = session.connect_optional("tec", tec["resource"], lambda: TECInstrument(port=tec["resource"]), device_label="TEC103")
    lockin = cfg["lockin_r"]
    ctx.devices["lockin"] = session.connect("lockin", f'{lockin["model"]}://{lockin["device_id"]}', lambda: create_lockin(lockin))
    initialize_lockin_inputs(ctx.devices["lockin"])


def clocks(ctx):
    physical = {route.mapping_key: route.device for route in ctx.devices["sg"].iter_physical_routes()}
    physical["lockin_r"] = ctx.devices["lockin"]
    return synchronize_connected_clocks(physical, ctx.mapping, {key: key for key in physical})


def switch(ctx, enabled):
    set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"])
    ctx.outputs["温控"] = "5 V DC + ON" if enabled else "0 V DC + ON"


def initial_outputs(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key, value in (("Z_magnetic_field", 0), ("X_magnetic_field", p.x_magnetic_field), ("Y_magnetic_field", p.y_magnetic_field)):
        checked(ctx, key, value)
        sg.set_output(False, channel=ch[key])
        sg.set_burst_state(False, channel=ch[key]); sg.set_mod_state(False, channel=ch[key])
        # setup_dc() 自动开启输出；关闭状态的辅助场使用保留开关的共同接口。
        sg.set_shape("DC", channel=ch[key]); sg.set_dc_voltage(value, channel=ch[key])
    for key, value in (("Pump_laser_power", p.pump_laser_power), ("Probe_laser_power", p.probe_laser_power)):
        checked(ctx, key, value)
        sg.setup_dc(value, channel=ch[key]); sg.set_output(True, channel=ch[key])
    checked(ctx, "main_magnetic_field", p.main_magnetic_field)
    gs = ctx.devices["gs"]
    gs.set_source_function(ctx.mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(load_safety_limits(ctx.root)["main_magnetic_field"]["max"] / 1000)
    gs.set_current(p.main_magnetic_field / 1000); gs.set_output(True)
    switch(ctx, True)
    status = configure_temperature_control(ctx.devices.get("tec"), p.temperature,
        channel=ctx.mapping["temperature"].get("channel", 1))
    ctx.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)


def prepare_lockin(ctx):
    p, h = ctx.params, ctx.devices["lockin"]
    demod.configure_signal_input(h, SignalInputConfig(input_index=0, range=p.hf2_demod0_signal_range, diff=False))
    demod.configure_signal_input(h, SignalInputConfig(input_index=p.sigin2_index, range=p.sigin2_range, diff=False))
    demod.configure_oscillator(h, OscillatorConfig(osc_index=p.hf2_demod0_osc_idx, frequency=p.hf2_demod0_osc_freq))
    demod.configure_oscillator(h, OscillatorConfig(osc_index=p.demod_r_osc_idx, frequency=1000))
    ctx.stream_rate = configure_main(ctx, calibration=True)
    if p.configure_auxout2_to_demod0_y:
        apply_settings(h, ctx.mapping["lockin_r"]["device_library_id"], p.hf2_demod0_idx,
            {"aux_output": dict(index=p.auxout_index, demod_index=p.auxout_source_demod_idx,
                output_select=p.auxout_source_select, scale=p.auxout_scale, offset_v=p.auxout_offset)}, project_root=ctx.root)
    demod.configure_demodulator(h, DemodulatorConfig(demod_index=p.demod_r_idx, enable=True,
        rate=p.demod_r_rate, input_channel=p.sigin2_index, osc_select=p.demod_r_osc_idx,
        harmonic=1, time_constant=max(p.demod_r_tc_min, p.demod_r_tc_per_period/1000), order=p.demod_r_order, phase=0))
    value = verify_routing(ctx)
    ctx.state.update(lockin_ready=value,
        hf2_demod0=dict(demod_idx=p.hf2_demod0_idx, osc_idx=p.hf2_demod0_osc_idx, osc_freq_Hz=p.hf2_demod0_osc_freq,
            signal_range_V=p.hf2_demod0_signal_range, rate_Sa_s_requested=p.hf2_demod0_rate,
            rate_Sa_s_actual=value["main"]["rate"], TC_calib_s=p.hf2_demod0_tc_calib, TC_meas_s=p.hf2_demod0_tc, order=p.hf2_demod0_order),
        hf2_demod_r=dict(demod_idx=p.demod_r_idx, osc_idx=p.demod_r_osc_idx, adcselect_requested=p.demod_r_adc_select,
            adcselect_actual=value["rf"]["adcselect"], rate_Sa_s_requested=p.demod_r_rate, rate_Sa_s_actual=value["rf"]["rate"],
            TC_min_s=p.demod_r_tc_min, TC_per_period=p.demod_r_tc_per_period, TC_initial_s=value["rf"]["timeconstant"],
            order_requested=p.demod_r_order, order_actual=value["rf"]["order"], n_avg=p.demod_r_n_avg, avg_interval_s=p.demod_r_avg_interval),
        hf2_signal_input_2=dict(input_index=p.sigin2_index, range_V_requested=p.sigin2_range, range_V_actual=value["inputs"][1]["range"],
            ac_coupling_actual=bool(value["inputs"][1]["ac"]), imp50_actual=bool(value["inputs"][1]["imp50"])),
        hf2_aux_out_2=dict(auxout_index=p.auxout_index, enabled=p.configure_auxout2_to_demod0_y,
            source_demod_requested=p.auxout_source_demod_idx, source_select_requested=p.auxout_source_select,
            scale_requested=p.auxout_scale, offset_requested=p.auxout_offset, actual=value["aux"]))


def configure_main(ctx, *, calibration=False):
    p, h = ctx.params, ctx.devices["lockin"]
    return demod.configure_demodulator(h, DemodulatorConfig(demod_index=p.hf2_demod0_idx,
        enable=True, rate=p.hf2_demod0_rate, input_channel=0, osc_select=p.hf2_demod0_osc_idx, harmonic=1,
        time_constant=p.hf2_demod0_tc_calib if calibration else p.hf2_demod0_tc,
        order=p.hf2_demod0_order, phase=ctx.state.get("demod_phase_deg", 0)), for_acquisition=not calibration)


def lockin_snapshot(ctx):
    h, p = ctx.devices["lockin"], ctx.params
    paths = {"main": h.demod_path(p.hf2_demod0_idx), "rf": h.demod_path(p.demod_r_idx)}
    value = {name: {key: h.get_int(path+"/"+key) for key in ("adcselect", "oscselect", "order", "enable")} |
        {key: h.get_double(path+"/"+key) for key in ("rate", "timeconstant", "phaseshift")} for name, path in paths.items()}
    value["inputs"] = [{key: h.get_int(h.sig_in_path(i)+"/"+key) for key in ("ac", "imp50", "diff")} |
        {"range": h.get_double(h.sig_in_path(i)+"/range")} for i in (0, p.sigin2_index)]
    path = h.aux_out_path(p.auxout_index)
    value["aux"] = {"demodselect": h.get_int(path+"/demodselect"), "outputselect": h.get_int(path+"/outputselect"),
                    "scale": h.get_double(path+"/scale"), "offset": h.get_double(path+"/offset")}
    value["main_frequency"] = h.get_double(h.osc_path(p.hf2_demod0_osc_idx)+"/freq")
    return value


def verify_routing(ctx):
    p, value = ctx.params, lockin_snapshot(ctx)
    if value["main"]["adcselect"] != 0 or value["rf"]["adcselect"] != p.sigin2_index:
        raise ValueError("锁相输入模式已改变，必须使用双主电压物理回环")
    if any(item["ac"] or item["imp50"] or item["diff"] for item in value["inputs"]):
        raise ValueError("锁相输入必须为高阻、DC、单端")
    expected = dict(demodselect=p.auxout_source_demod_idx, outputselect=1, scale=p.auxout_scale, offset=p.auxout_offset)
    if any(not math.isclose(value["aux"][key], val, rel_tol=1e-9, abs_tol=1e-12) for key, val in expected.items()):
        raise ValueError("Aux 物理回环配置回读不一致")
    if value["main"]["oscselect"] != p.hf2_demod0_osc_idx or value["rf"]["oscselect"] != p.demod_r_osc_idx:
        raise ValueError("锁相参考振荡器已改变")
    return value


def shutdown(ctx, *, disconnect_tec=True):
    sg = ctx.devices.get("sg")
    channels = [DGChannelShutdown(sg, ctx.channels[key], key, key) for key in (
        "Z_magnetic_field", "X_magnetic_field", "Y_magnetic_field", ) if key in ctx.channels]
    return run_safety_shutdown(dg_channels=channels,
        temperature_switch=TemperatureSwitchRestore(sg, ctx.channels["Temp_Switch"]) if sg else None,
        disconnect_targets=(DisconnectTarget("TEC103", ctx.devices.get("tec")),) if disconnect_tec else ())
