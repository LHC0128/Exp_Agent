"""无硬件预检、设备连接、初始条件和安全收尾。"""
from lab_workflows.steps import initialize_lockin_inputs
from lockin_amplifier import HF2Instrument, MFLIInstrument
from datetime import datetime
from . import run_format
import math
from gs200 import GS200Instrument
from tec_controller import TECInstrument
from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin, signal_generator_max_arb_points
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...lockin_control import _bounded_rule
from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from ...steps import (DeviceSession, connect_signal_generator_routes, configure_temperature_control,
    set_temperature_switch, DGChannelShutdown, TemperatureSwitchRestore, DisconnectTarget,
    STANDARD_PRESERVED_OUTPUTS, run_safety_shutdown)
from ._helpers import wait
from .waveform import load_envelope
from .run_format import EXPERIMENT_ID


def check_rf_aux_voltage(params, root, device_id, input_range):
    if not math.isfinite(input_range) or input_range <= 0:
        raise ValueError("Demod0 输入量程无效，无法检查 RF 辅助输出的电压边界")
    key, rule = _bounded_rule(device_id, "aux_output", params.rf_aux_output_idx, project_root=root)
    swing = abs(params.loopback_aux_scale) * math.sqrt(2) * input_range
    for level in (params.loopback_aux_offset_v - swing, params.loopback_aux_offset_v + swing):
        validate_safety_limit(key, level, {key: rule})


def preflight(params, root, *, experiment_id=EXPERIMENT_ID):
    validate_mapping_usage(RF_SENSITIVITY_DIRECT_AW, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(experiment_id, params.to_external(), mapping=mapping)
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    model = mapping["lockin_r"].get("model", "HF2").upper()
    driver = HF2Instrument if model == "HF2" else MFLIInstrument
    params.demod3_adc_select = driver.demod_input_sources[params.rf_input_source]
    check_rf_aux_voltage(params, root, mapping["lockin_r"]["device_library_id"], params.demod0_signal_range)
    envelope = load_envelope(params, min(signal_generator_max_arb_points(mapping[key])
        for key in ("X_magnetic_field", "Y_magnetic_field")))
    return params, mapping, envelope

def connect_devices(mapping: dict, *, params, experiment_id=EXPERIMENT_ID, connect_tec=True) -> dict:
    session = DeviceSession()
    try:
        signals, channels = connect_signal_generator_routes(session, "signals", {
            name: (key, mapping[key]) for name, key in (
                ("x", "X_magnetic_field"), ("y", "Y_magnetic_field"),
                ("z", "Z_magnetic_field"), ("trigger", "Time_sequence_2"),
                ("pump_power", "Pump_laser_power"), ("probe_power", "Probe_laser_power"),
                ("pump_rf", "Pump_modulation"), ("pump_gate", "Time_sequence"),
                ("temp", "Temp_Switch"),
            )
        })
        gs_config, hf_config, tec_config = (mapping[key] for key in ("main_magnetic_field", "lockin_r", "temperature"))
        gs = session.connect("gs200", gs_config["resource"], lambda: GS200Instrument(gs_config["resource"]))
        hf2 = session.connect("hf2", f"hf2://{hf_config['host']}:{hf_config['port']}/{hf_config['device_id']}", lambda: create_lockin(hf_config))
        if hf2.model.upper() != hf_config.get("model", "HF2").upper():
            raise ValueError("已连接锁相型号与设备库不一致，禁止启动输出")
        # 设备库声明只用于无硬件预检，输出启动前必须检查已连接设备的真实选件。
        actual_mapping = mapping | {"lockin_r": hf_config | {"model": hf2.model, "capabilities": hf2.capabilities}}
        require_lockin_compatibility(experiment_id, params.to_external(), mapping=actual_mapping)
        initialize_lockin_inputs(hf2)
        tec = session.connect_optional("tec", tec_config["resource"], lambda: TECInstrument(tec_config["resource"]), device_label="TEC") if connect_tec else None
        return {"signals": signals, "channels": channels, "gs200": gs, "hf2": hf2, "tec": tec, "optional_connection_errors": session.optional_connection_errors()}
    except BaseException:
        session.cleanup_connection_failure()
        raise

def configure_initial_outputs(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    gs, mapping = devices["gs200"], ctx.mapping
    # setup_dc 会顺带打开输出（DG900 的 :APPLy:DC 自动开启），先归 DC 再统一关闭，
    # 确保 Demod0 校相时 XY/Z 控制场是真正关闭而不是 DC 0 V 输出。
    for name, key in (("x", "X_magnetic_field"), ("y", "Y_magnetic_field"), ("z", "Z_magnetic_field"), ("trigger", "Time_sequence_2"), ("pump_gate", "Time_sequence")):
        validate_safety_limit(key, 0.0)
        signals.setup_dc(0.0, channel=channels[name])
        signals.set_burst_state(False, channel=channels[name])
        signals.set_mod_state(False, channel=channels[name])
        signals.set_output(False, channel=channels[name])
    for name, key, voltage in (("pump_power", "Pump_laser_power", params.pump_laser_power), ("probe_power", "Probe_laser_power", params.probe_laser_power)):
        validate_safety_limit(key, voltage)
        signals.set_burst_state(False, channel=channels[name])
        signals.set_mod_state(False, channel=channels[name])
        signals.setup_dc(voltage, channel=channels[name])
        signals.set_output(True, channel=channels[name])
    validate_safety_limit("main_magnetic_field", params.main_magnetic_field)
    gs.set_source_function(mapping["main_magnetic_field"]["source_function"])
    gs.set_current_limit(load_safety_limits(ctx.root)["main_magnetic_field"]["max"] / 1000)
    gs.set_current(params.main_magnetic_field / 1000)
    gs.set_output(True)
    validate_safety_limit("Temp_Switch", params.temp_switch)
    signals.set_burst_state(False, channel=channels["temp"])
    signals.set_mod_state(False, channel=channels["temp"])
    set_temperature_switch(signals, True, channel=channels["temp"])


def configure_temperature(ctx):
    ctx.state["temperature_control"] = configure_temperature_control(ctx.devices["tec"],
        ctx.params.temperature, channel=ctx.mapping["temperature"]["channel"],
        ).to_dict()
    ctx.state["optional_connection_errors"] = ctx.devices["optional_connection_errors"]


def shutdown(devices, *, disconnect_tec=True):
    signals, channels = devices.get("signals"), devices.get("channels", {})
    return run_safety_shutdown(
        dg_channels=[DGChannelShutdown(signals, channels[name], key, label)
            for name, key, label in (("x", "X_magnetic_field", "X DirectAW"),
                ("y", "Y_magnetic_field", "Y DirectAW"), ("z", "Z_magnetic_field", "Z RF"))
            if name in channels],
        temperature_switch=TemperatureSwitchRestore(signals, channels["temp"]) if "temp" in channels else None,
        disconnect_targets=(DisconnectTarget("TEC", devices.get("tec")),) if disconnect_tec else (),
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, "Time_sequence_2", "Time_sequence"))


def initial_state(params, *, identity=run_format):
    return {
        "experiment_id": identity.EXPERIMENT_ID, "execution_mode": "typed_workflow",
        "acquisition_method": identity.ACQUISITION_METHOD, "response_component": identity.RESPONSE_COMPONENT,
        "noise_component": "X", "started_at": datetime.now().isoformat(),
        "completion_status": "running", "analysis_status": "not_started",
        "phase_calibration_status": "not_started", "xy_phase_calibration": {"converged": False, "history": []},
        "rf_phase_calibration": {"points_completed": 0},
        "signal_chain": params.signal_chain(identity.RESPONSE_COMPONENT), "common_trigger": {"mapping_key": "Time_sequence_2", "output": "channel", "targets": ["X Ext Trig", "Y Ext Trig", "Z Ext Trig"], "frequency_Hz": params.xy_trigger_freq},
        "calibration": {"z_positive_field_phase_deg": params.z_burst_phase_init % 360, "z_negative_field_phase_deg": (params.z_burst_phase_init + 180) % 360, "z_v_to_nT": params.z_v_to_nt},
        "actual_rates": {}, "data_files": [], "response_points_completed": 0, "noise_records_completed": 0,
        "bandwidth_points_completed": 0,
    }


def restore_temperature(ctx):
    set_temperature_switch(ctx.devices["signals"], True, channel=ctx.devices["channels"]["temp"])
    ctx.outputs["温控"] = "ON"
    wait(ctx.params.temp_switch_on_lag_s)
