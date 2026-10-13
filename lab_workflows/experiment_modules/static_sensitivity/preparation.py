"""静磁场预检、设备准备、温度稳定与安全恢复。"""

import os
from datetime import datetime

from gs200 import GS200Instrument
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import STATIC_SENSITIVITY
from ...steps import (
    DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore, configure_fixed_dc_field, configure_temperature_control,
    wait_for_temperature_stable,
    connect_signal_generator_routes, initialize_lockin_inputs, run_safety_shutdown,
    synchronize_connected_clocks,
)
from .acquisition import RuntimeCancellation, set_temp_switch, wait_for_settle
from .phase import demod_config
from lockin_amplifier import demod
from ...steps.temperature import TemperatureControlStatus

EXPERIMENT_ID = "static-sensitivity"
DATA_TYPE = "Static_Magnetic_Field_Sensitivity"
DG_ROUTES = {
    "dg_sweep": ("Z_magnetic_field", "Time_sequence_2"),
    "dg_laser": ("Pump_laser_power", "Probe_laser_power"),
    "dg_comp": ("X_magnetic_field", "Y_magnetic_field"),
    "dg_mod": ("Pump_modulation", "Time_sequence"),
    "dg_temp": ("Temp_Switch",),
}


def initial_state():
    return dict(experiment_id=EXPERIMENT_ID, execution_mode="typed_workflow", acquisition_method="dc_step",
                completion_status="running", analysis_status="not_started",
                started_at=datetime.now().astimezone().isoformat(), failure_reason="",
                temperature_switch_off_mode="output_off", noise_z_condition="zero_dc_output_off",
                purpose="dispersion_and_sensitivity", data_files=[], actual_rates={})


def preflight(params, root):
    if any(key in os.environ for key in ("LAB_STATIC_CONFIG", "LAB_STATIC_ACQUISITION_ONLY")):
        raise ValueError("旧 LAB_STATIC_* 桥接已移除，请清除这些环境变量，使用 params/experiments/static-sensitivity.yaml")
    validate_mapping_usage(STATIC_SENSITIVITY, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    return params, mapping


def connect_devices(session, mapping):
    devices, channels = {}, {}
    for name, keys in DG_ROUTES.items():
        devices[name], routed = connect_signal_generator_routes(
            session, name, {key: (key, mapping[key]) for key in keys})
        channels.update(routed)
    config = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", config["resource"], lambda: GS200Instrument(config["resource"]))
    config = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2", f"hf2://{config.get('host', '127.0.0.1')}/{config['device_id']}", lambda: create_lockin(config))
    initialize_lockin_inputs(devices["hf2"])
    config = mapping["temperature"]
    devices["tec"] = session.connect_optional(
        "tec", config["resource"], lambda: TECInstrument(port=config["resource"]), device_label="TEC103")
    return devices, channels


def synchronize_clocks(ctx):
    ctx.state["clock_sources"] = synchronize_connected_clocks(
        ctx.devices, ctx.mapping, {**{name: keys[0] for name, keys in DG_ROUTES.items()}, "hf2": "lockin_r"},
        settle_wait=wait_for_settle)


def configure_initial_outputs(ctx):
    params, devices, channels = ctx.params, ctx.devices, ctx.channels
    # 时序 2 只参与连接路由，不写入该通道的任何状态。
    configure_fixed_dc_field(devices["dg_sweep"], channels["Z_magnetic_field"], "Z_magnetic_field", 0.0)
    devices["dg_sweep"].set_sync_state(False, channel=channels["Z_magnetic_field"])
    for key, voltage in (("X_magnetic_field", params.x_magnetic_field), ("Y_magnetic_field", params.y_magnetic_field)):
        configure_fixed_dc_field(devices["dg_comp"], channels[key], key, voltage)
    for key, voltage in (("Pump_laser_power", params.pump_laser_power), ("Probe_laser_power", params.probe_laser_power)):
        validate_safety_limit(key, voltage)
        devices["dg_laser"].setup_dc(voltage, channel=channels[key])
    gs = devices["gs200"]
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field)
    gs.set_source_function(ctx.mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(load_safety_limits(ctx.root)["main_magnetic_field"]["max"] / 1000)
    gs.set_current(params.main_magnetic_field / 1000)
    gs.set_output(True)
    set_temp_switch(devices["dg_temp"], channels["Temp_Switch"], True)
    channel = int(ctx.mapping["temperature"]["channel"])
    if devices["tec"] is None:
        ctx.state["temperature_control"] = configure_temperature_control(None, params.temperature, channel=channel).to_dict()
    else:
        validate_safety_limit("temperature", params.temperature)
        devices["tec"].set_target_temperature(params.temperature, channel=channel)
        devices["tec"].set_enable(True, channel=channel)


def stabilize_temperature(ctx):
    params = ctx.params
    if ctx.devices["tec"] is None:
        return
    actual = wait_for_temperature_stable(
        ctx.devices["tec"], params.temperature, channel=int(ctx.mapping["temperature"]["channel"]),
        tolerance_c=params.temp_tolerance_c, stable_reads=params.temp_stable_reads,
        poll_interval_s=params.temp_poll_interval_s, timeout_s=params.max_temp_wait_s,
        cancellation=RuntimeCancellation())
    ctx.state["temperature_control"] = TemperatureControlStatus(params.temperature, actual, True, "tec103").to_dict()


def safe_shutdown(params, devices, channels, restore_phase, *, disconnect_tec=True):
    actions = () if restore_phase is None or "hf2" not in devices else (ShutdownAction(
        "恢复锁相色散配置失败", lambda: demod.configure_demodulator(devices["hf2"], demod_config(params, restore_phase))),)
    z = devices.get("dg_sweep")
    return run_safety_shutdown(
        dg_channels=() if z is None else (
            DGChannelShutdown(z, channels["Z_magnetic_field"], "Z_magnetic_field", "Z 直流偏置", disable_sync=True),),
        temperature_switch=TemperatureSwitchRestore(devices.get("dg_temp"), channels.get("Temp_Switch", 1)),
        extra_actions=actions,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence", "Time_sequence_2", "X_magnetic_field", "Y_magnetic_field"))
