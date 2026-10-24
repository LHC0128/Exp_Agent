"""无硬件预检、映射路由、锁相准备与声明式收尾。"""
from dataclasses import asdict
import math
import numpy as np
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from lockin_amplifier import DAQConfig, DemodulatorConfig, SignalInputConfig, demod, select_acquisition
from lockin_amplifier.acquisition import restore_demod_streams
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_acquisition import experiment_acquisition_requests
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (connect_signal_generator_routes, initialize_lockin_inputs,
    synchronize_connected_clocks, set_temperature_switch, TemperatureSwitchRestore,
    DisconnectTarget, ShutdownAction, run_safety_shutdown)
from . import run_format

SIGNALS = ("Pump_laser_power", "Probe_laser_power", "Temp_Switch")


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    validate_mapping_usage(run_format.REQUIRED_MAPPING_KEYS, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(run_format.EXPERIMENT_ID, params.to_external(), mapping=mapping)
    limits = load_safety_limits(root)
    for key, values in (
            ("main_magnetic_field", (params.shot_noise_field_ma,)),
            ("Pump_laser_power", (params.pump_laser_power,)),
            ("Probe_laser_power", (params.probe_power_start, params.probe_power_stop)),
            ("temperature", (params.temperature,)), ("Temp_Switch", (0, 5))):
        if limits.get(key, {}).get("min") is None or limits.get(key, {}).get("max") is None:
            raise ValueError(f"{key} 缺少明确安全上下限")
        for value in values:
            validate_safety_limit(key, value, limits)
    if mapping["main_magnetic_field"].get("source_function", "").upper() not in ("CURR", "CURRENT"):
        raise ValueError("本实验主场使用电流源模式，不会自动改用电压")
    if params.acq_duration * params.hf2_demod_rate < 8:
        raise ValueError("单次请求记录至少需要 8 个样本")
    return params, mapping


def connect(ctx):
    mapping, session, devices = ctx.mapping, ctx.device_session, ctx.devices
    cfg = mapping["main_magnetic_field"]
    devices["gs"] = session.connect("gs", str(cfg["resource"]), lambda: GS200Instrument(cfg["resource"]))
    devices["sg"], ctx.channels = connect_signal_generator_routes(session, "photon",
        {key: (key, mapping[key]) for key in SIGNALS})
    cfg = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", str(cfg["resource"]),
        lambda: TECInstrument(port=cfg["resource"]), device_label="TEC103")
    cfg = mapping["lockin_r"]
    devices["lockin"] = session.connect("lockin",
        f'{cfg["model"]}://{cfg["device_id"]}@{cfg.get("host")}:{cfg.get("port")}',
        lambda: create_lockin(cfg, acquisition_requests=experiment_acquisition_requests(
            run_format.EXPERIMENT_ID, ctx.params.to_external())))
    initialize_lockin_inputs(devices["lockin"])


def clocks(ctx):
    physical = {route.mapping_key: route.device for route in ctx.devices["sg"].iter_physical_routes()}
    physical["lockin_r"] = ctx.devices["lockin"]
    return synchronize_connected_clocks(physical, ctx.mapping, {key: key for key in physical})


def set_temp(ctx, enabled):
    set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"])


def initial_outputs(ctx):
    p, d, c = ctx.params, ctx.devices, ctx.channels
    limits = load_safety_limits(ctx.root)
    sg = d["sg"]
    validate_safety_limit("Pump_laser_power", p.pump_laser_power, limits)
    sg.set_output(False, channel=c["Pump_laser_power"])
    # setup_dc() 会自动打开输出；Pump 用保留开关状态的底层共同接口。
    sg.set_shape("DC", channel=c["Pump_laser_power"])
    sg.set_dc_voltage(p.pump_laser_power, channel=c["Pump_laser_power"])
    validate_safety_limit("Probe_laser_power", p.probe_power_start, limits)
    sg.set_burst_state(False, channel=c["Probe_laser_power"])
    sg.set_mod_state(False, channel=c["Probe_laser_power"])
    sg.setup_dc(p.probe_power_start, channel=c["Probe_laser_power"])
    sg.set_output(True, channel=c["Probe_laser_power"])
    validate_safety_limit("main_magnetic_field", p.shot_noise_field_ma, limits)
    gs = d["gs"]
    gs.set_source_function(ctx.mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(max(abs(limits["main_magnetic_field"]["min"]), abs(limits["main_magnetic_field"]["max"])) / 1000)
    gs.set_current(p.shot_noise_field_ma / 1000)
    gs.set_output(True)
    set_temp(ctx, True)


def prepare_demod(ctx):
    p, hfi = ctx.params, ctx.devices["lockin"]
    demod.configure_signal_input(hfi, SignalInputConfig(input_index=0, range=p.hf2_signal_range))
    demod.set_reference_frequency(hfi, p.hf2_osc_freq, osc_idx=0)
    ctx.stream_rate = demod.configure_demodulator(hfi, DemodulatorConfig(
        demod_index=p.hf2_demod_idx, enable=True, rate=p.hf2_demod_rate, input_channel=0,
        osc_select=0, harmonic=1, time_constant=p.hf2_demod_tc, order=p.hf2_demod_order), for_acquisition=True)
    if not math.isfinite(ctx.stream_rate) or ctx.stream_rate <= 0:
        raise ValueError("锁相实际传输率必须为有限正数")
    plan = select_acquisition(hfi.model, hfi.capabilities,
        DAQConfig(duration=p.acq_duration, grid_cols=int(ctx.stream_rate * p.acq_duration),
                  signal_paths=["sample.x", "sample.y"]), p.hf2_demod_rate, p.hf2_demod_idx, hfi.acquisition_mode)
    ctx.plan = plan
    ctx.grid_cols = plan.length if plan.module == "scope" else int(ctx.stream_rate * p.acq_duration)
    if ctx.grid_cols < 8:
        raise ValueError("单次实际记录至少需要 8 个样本")
    base = hfi.demod_path(p.hf2_demod_idx)
    actual_tc = float(hfi.get_double(base + "/timeconstant"))
    actual_range = float(hfi.get_double(hfi.sig_in_path(0) + "/range"))
    if not all(math.isfinite(value) and value > 0 for value in (actual_tc, actual_range)):
        raise ValueError("锁相实际滤波时间常数或输入量程无效")
    ctx.state["daq_settings"] = dict(plan=asdict(plan), stream_rate_Sa_s=float(ctx.stream_rate),
        requested_rate_Sa_s=p.hf2_demod_rate, samples=ctx.grid_cols,
        requested_time_constant_s=p.hf2_demod_tc, actual_time_constant_s=actual_tc,
        requested_input_range_v=p.hf2_signal_range, actual_input_range_v=actual_range)
    ctx.outputs["锁相"] = f"{hfi.model} {plan.module.upper()} 准备完成"
    verify_ready(ctx)


def verify_ready(ctx):
    p, hfi = ctx.params, ctx.devices["lockin"]
    base = hfi.demod_path(p.hf2_demod_idx)
    for node, expected in (("adcselect", 0), ("oscselect", 0), ("harmonic", 1),
                           ("order", p.hf2_demod_order), ("enable", int(ctx.plan.module == "daq"))):
        if hfi.get_int(base + "/" + node) != expected:
            raise ValueError(f"锁相采集模式已改变：{node}；请重新执行 A06")
    # 时间常数和量程可能被设备取整；后续与本轮实际回读比较，不把取整当作模式漂移。
    actual = ctx.state["daq_settings"]
    for path, expected in ((base + "/timeconstant", actual["actual_time_constant_s"]),
            (hfi.osc_path(0) + "/freq", p.hf2_osc_freq),
            (hfi.sig_in_path(0) + "/range", actual["actual_input_range_v"]),
            (base + "/rate", ctx.stream_rate)):
        if not np.isclose(hfi.get_double(path), expected, rtol=1e-5, atol=1e-12):
            raise ValueError(f"锁相配置回读不一致：{path}；请重新执行 A06")
    for node in ("ac", "imp50"):
        if hfi.get_int(hfi.sig_in_path(0) + "/" + node):
            raise ValueError("主电压输入必须保持 DC 和高阻；请重新执行 A06")


def shutdown(ctx, *, disconnect_tec=True):
    sg = ctx.devices.get("sg")
    return run_safety_shutdown(
        temperature_switch=TemperatureSwitchRestore(sg, ctx.channels["Temp_Switch"]) if sg else None,
        extra_actions=(ShutdownAction("恢复锁相采集传输状态", lambda: restore_demod_streams(ctx.devices["lockin"])),)
            if ctx.devices.get("lockin") else (),
        disconnect_targets=(DisconnectTarget("TEC", ctx.devices.get("tec")),) if disconnect_tec else (),
    )
