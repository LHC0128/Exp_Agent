"""无硬件预检、共享连接、参考时钟、固定条件、TEC 目标与安全收尾。"""
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from ...common import load_mapping, validate_safety_limit
from ...devices import create_signal_generator, create_lockin
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (initialize_lockin_inputs, synchronize_connected_clocks,
    DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
    TemperatureSwitchRestore, configure_fixed_dc_field, configure_main_field,
    configure_temperature_control, set_temperature_switch, run_safety_shutdown)
from ._helpers import sleep
from .definition import validate_hardware
from .run_format import EXPERIMENT_ID, REQUIRED_MAPPING_KEYS, DG_KEYS, OFF_KEYS
from .excitation import set_gate_frequency, set_demod_frequency, configure_pump, configure_response


def preflight(params, root):
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_hardware(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def connect_devices(session, mapping):
    devices = {}
    for key in DG_KEYS:
        config = mapping[key]
        devices[key] = session.connect(key, config["resource"], lambda c=config: create_signal_generator(c))
    config = mapping["main_magnetic_field"]
    devices["gs200"] = session.connect("gs200", config["resource"], lambda: GS200Instrument(config["resource"]))
    config = mapping["lockin_r"]
    devices["hf2"] = session.connect(
        "hf2", f"hf2://{config.get('host', '127.0.0.1')}/{config['device_id']}",
        lambda: create_lockin(config),
    )
    initialize_lockin_inputs(devices["hf2"])
    config = mapping["temperature"]
    devices["tec"] = session.connect_optional("tec", config["resource"], lambda: TECInstrument(port=config["resource"]), device_label="TEC103")
    return devices


def configure_clocks(devices, mapping):
    return synchronize_connected_clocks(devices, mapping, {**{key: key for key in DG_KEYS}, "hf2": "lockin_r"}, settle_wait=sleep)


def configure_initial_outputs(params, devices, channels, mapping):
    for key in OFF_KEYS:
        configure_fixed_dc_field(devices[key], channels[key], key, 0.0)
    devices["Z_magnetic_field"].set_sync_state(False, channel=channels["Z_magnetic_field"])
    configure_main_field(params, devices["gs200"], mapping["main_magnetic_field"])
    for key, voltage in (("Pump_laser_power", params.pump_laser_power_v), ("Probe_laser_power", params.probe_laser_power_v)):
        validate_safety_limit(key, voltage)
        devices[key].setup_dc(voltage, channel=channels[key])
        devices[key].set_output(True, channel=channels[key])
    set_temperature_switch(devices["Temp_Switch"], True, channel=channels["Temp_Switch"])


def configure_outputs(params, devices, channels, mapping, directory):
    clocks = configure_clocks(devices, mapping)
    configure_initial_outputs(params, devices, channels, mapping)
    temperature = configure_temperature_control(devices.get("tec"), params.temperature_c,
        channel=mapping["temperature"]["channel"])
    configure_pump(params, devices, channels)
    actual_rate, snapshot = configure_response(params, devices)
    directory.update_config(clock_sources=clocks, temperature_control=temperature.to_dict(),
        actual_rates={"response_sa_s": actual_rate}, hf2_configuration=snapshot)
    return actual_rate


def safe_shutdown(params, devices, channels, *, demod_params=None, disconnect_tec=True, restore_pump=True, restore_demod=True):
    actions = []
    if restore_pump and "Time_sequence" in devices:
        actions.append(ShutdownAction("恢复 Pump 零偏参考频率失败", lambda: set_gate_frequency(params, devices["Time_sequence"], channels["Time_sequence"], params.zero_bias_center_frequency_hz)))
    if restore_demod and "hf2" in devices:
        actions.append(ShutdownAction("恢复锁相零偏参考频率失败", lambda: set_demod_frequency(devices["hf2"], (demod_params or params).zero_bias_center_frequency_hz)))
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(devices[key], channels[key], key, key,
                                        disable_sync=key == "Z_magnetic_field") for key in OFF_KEYS if key in devices],
        temperature_switch=TemperatureSwitchRestore(devices["Temp_Switch"], channels["Temp_Switch"]) if "Temp_Switch" in devices else None,
        extra_actions=tuple(actions),
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence"),
    )
