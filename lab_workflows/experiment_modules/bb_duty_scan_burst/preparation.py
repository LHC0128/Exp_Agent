"""无硬件预检、资源复用、时钟与初始输出；导入不连接设备。"""
import math
import time
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from sds_acquisition import SDSInstrument, SDSAcquisition, AcquisitionConfig, ChannelConfig, TriggerConfig
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...experiment_runtime import check_cancelled
from ...steps import (connect_signal_generator_routes, initialize_lockin_inputs, synchronize_connected_clocks,
    configure_temperature_control, set_temperature_switch, TemperatureSwitchRestore, DGChannelShutdown,
    DisconnectTarget, ShutdownAction, run_safety_shutdown)
from . import run_format


def sleep(seconds, *, cancellable=True):
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        if cancellable:
            check_cancelled()
        time.sleep(min(0.1, remaining))


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    keys = (*run_format.REQUIRED_MAPPING_KEYS, *(k for k in run_format.UNUSED_FIELDS if k in mapping))
    if params.use_hf2_monitor:
        keys += ("lockin_r",)
    validate_mapping_usage(keys, root)
    for key in keys:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    if mapping["main_magnetic_field"].get("instrument") != "gs200" or mapping["main_magnetic_field"].get("source_function", "").upper() not in ("CURR", "CURRENT"):
        raise ValueError("本实验主场必须使用 GS200 电流模式")
    limits = load_safety_limits(root)
    for key, values in (
        ("main_magnetic_field", (params.z_field_setpoint,)),
        ("Pump_laser_power", (params.fixed_pump_power_voltage,)), ("Probe_laser_power", (params.probe_power,)),
        ("temperature", (params.tec_temperature,)), ("Temp_Switch", (0, 5)),
        ("Time_sequence", (params.pump_rf_gate_low_v, params.pump_rf_gate_high_v)),
        ("Pump_modulation", (params.aom_carrier_amplitude_v, params.aom_carrier_offset_v - params.aom_carrier_amplitude_v / 2,
                              params.aom_carrier_offset_v + params.aom_carrier_amplitude_v / 2)),
        ("PUMP_MOD_DUTY", tuple(100 * v for v in params.duty_cycle_list)),
        *((key, (0,)) for key in run_format.UNUSED_FIELDS if key in mapping),
    ):
        if limits.get(key, {}).get("min") is None or limits.get(key, {}).get("max") is None:
            raise ValueError(f"{key} 缺少明确安全上下限")
        for value in values:
            validate_safety_limit(key, value, limits)
    return params, mapping


def connect(ctx):
    m, d, session = ctx.mapping, ctx.devices, ctx.device_session
    cfg = m["main_magnetic_field"]
    d["gs"] = session.connect("gs", str(cfg["resource"]), lambda: GS200Instrument(cfg["resource"]))
    signals = [key for key in (*run_format.REQUIRED_MAPPING_KEYS, *run_format.UNUSED_FIELDS)
               if key in m and m[key]["instrument"] == "signal_generator"]
    d["sg"], ctx.channels = connect_signal_generator_routes(session, "bb_duty", {key: (key, m[key]) for key in signals})
    cfg = m["scope_waveform"]
    d["scope"] = session.connect("scope", str(cfg["resource"]), lambda: SDSInstrument(cfg["resource"]))
    d["acquirer"] = session.bind("acquirer", SDSAcquisition(d["scope"], check_cancelled=check_cancelled))
    cfg = m["temperature"]
    d["tec"] = session.connect_optional("tec", str(cfg["resource"]), lambda: TECInstrument(port=cfg["resource"]), device_label="TEC103")
    if ctx.params.use_hf2_monitor:
        cfg = m["lockin_r"]
        d["lockin"] = session.connect("lockin", f'{cfg["model"]}://{cfg["device_id"]}@{cfg.get("host")}:{cfg.get("port")}', lambda: create_lockin(cfg))
        initialize_lockin_inputs(d["lockin"])


def clocks(ctx):
    physical = {route.mapping_key: route.device for route in ctx.devices["sg"].iter_physical_routes()}
    if ctx.devices.get("lockin"):
        physical["lockin_r"] = ctx.devices["lockin"]
    return synchronize_connected_clocks(physical, ctx.mapping, {key: key for key in physical}, settle_wait=sleep)


def set_temp(ctx, enabled, *, recovery_wait=True):
    set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"])
    if enabled and recovery_wait:
        sleep(ctx.params.temp_switch_on_delay_s, cancellable=False)


def initial_outputs(ctx):
    p, d, c = ctx.params, ctx.devices, ctx.channels
    sg, limits = d["sg"], load_safety_limits(ctx.root)
    for key in run_format.UNUSED_FIELDS:
        if key in c:
            validate_safety_limit(key, 0, limits)
            sg.set_output(False, channel=c[key])
    validate_safety_limit("main_magnetic_field", p.z_field_setpoint, limits)
    d["gs"].set_source_function(ctx.mapping["main_magnetic_field"]["source_function"])
    d["gs"].set_current_limit(max(abs(limits["main_magnetic_field"]["min"]), abs(limits["main_magnetic_field"]["max"])) / 1000)
    d["gs"].set_current(p.z_field_setpoint / 1000)
    d["gs"].set_output(True)
    for key, value in (("Pump_laser_power", p.fixed_pump_power_voltage), ("Probe_laser_power", p.probe_power)):
        validate_safety_limit(key, value, limits)
        sg.set_burst_state(False, channel=c[key])
        sg.set_mod_state(False, channel=c[key])
        sg.setup_dc(value, channel=c[key])
        sg.set_output(True, channel=c[key])
    ctx.state["temperature_control"] = configure_temperature_control(d.get("tec"), p.tec_temperature,
        channel=int(ctx.mapping["temperature"].get("channel", 1))).to_dict()
    ctx.state["initial_temperature_c"] = None
    set_temp(ctx, True, recovery_wait=False)


def scope_config(p):
    channels = [ChannelConfig(number=ch, enabled=ch in (p.sds_pd_channel, p.sds_trig_channel),
        scale=p.sds_volt_scale_pd if ch == p.sds_pd_channel else p.sds_volt_scale_trig,
        offset=0, coupling="DC", impedance="ONEMeg", probe=1) for ch in range(1, 5)]
    return AcquisitionConfig(sampling_rate=p.sds_sample_rate, sampling_time=p.total_acq_s,
        acquire_type="NORMal", acquire_delay=0.1, channels=channels,
        trigger=TriggerConfig(mode="NORMal", source=f"C{p.sds_trig_channel}", type="EDGE", slope=p.sds_trig_slope, level=p.sds_trig_level),
        timebase_delay=p.sds_timebase_delay_s)


def prepare_scope(ctx):
    p, scope = ctx.params, ctx.devices["scope"]
    ctx.scope_config = scope_config(p)
    ctx.devices["acquirer"].apply_config(ctx.scope_config)
    sleep(0.1)
    points, rate, timebase = int(scope.get_actual_points()), float(scope.get_sampling_rate()), float(scope.get_timebase_scale())
    if points < 2 or not all(math.isfinite(v) and v > 0 for v in (rate, timebase)) or rate <= 2 * p.modulation_frequency_hz:
        raise ValueError("SDS 实际采样率/点数/时基无效，或 Nyquist 无法覆盖 Pump 频率")
    ctx.pd_scale = float(scope.get_channel_scale(p.sds_pd_channel))
    if not math.isfinite(ctx.pd_scale) or ctx.pd_scale <= 0:
        raise ValueError("SDS 实际 PD 垂直档位无效")
    ctx.state["scope_actual"] = dict(points_actual=points, sample_rate_actual_Hz=rate,
        timebase_scale_actual_s_per_div=timebase, capture_duration_actual_s=points / rate)


def shutdown(ctx, *, disconnect_tec=True):
    sg, c = ctx.devices.get("sg"), ctx.channels
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(sg, c[key], key, key) for key in run_format.UNUSED_FIELDS if key in c],
        temperature_switch=TemperatureSwitchRestore(sg, c["Temp_Switch"]) if sg else None,
        extra_actions=(ShutdownAction("停止 SDS 触发失败", ctx.devices["scope"].trigger_stop),) if ctx.devices.get("scope") else (),
        disconnect_targets=(DisconnectTarget("TEC", ctx.devices.get("tec")),) if disconnect_tec else (),
    )
