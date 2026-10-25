"""T2 设备连接、输出、示波器配置与安全收尾；导入不连接仪器。"""
from __future__ import annotations
from lab_workflows.steps import initialize_lockin_inputs
from lab_workflows.devices import create_lockin
from lab_workflows.lockin_compatibility import require_lockin_compatibility
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np
from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, HF2Instrument, SignalInputConfig, demod
from sds_acquisition import AcquisitionConfig, ChannelConfig, SDSAcquisition, SDSInstrument, TriggerConfig
from tec_controller import TECInstrument
from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeferredAcquisitionWrites, acquisition_window, DGChannelShutdown, DeviceSession, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, SafetyShutdownReport, ShutdownAction, TemperatureSwitchRestore, configure_temperature_control, connect_signal_generator_routes, create_run_directory, run_safety_shutdown, set_temperature_switch, synchronize_connected_clocks, wait_for_temperature_stable
from .models import T2CalibrationParams
def safe_shutdown(devices: dict[str, Any], channels: dict[str, int], params: T2CalibrationParams) -> SafetyShutdownReport:
    """按参考工作流恢复安全状态，保持主磁场和 Pump/Probe 输出。"""
    dg_channels: list[DGChannelShutdown] = []
    extra_actions: list[ShutdownAction] = []
    for key, safety_key in (('x_field', 'X_magnetic_field'), ('y_field', 'Y_magnetic_field'), ('z_field', 'Z_magnetic_field')):
        device = devices.get(key)
        channel = channels.get(key)
        if device is None or channel is None:
            continue
        dg_channels.append(DGChannelShutdown(device, channel, safety_key, key))
    if devices.get('z_field') is not None and 'time_sequence_2' in channels:
        dg_channels.append(DGChannelShutdown(devices['z_field'], channels['time_sequence_2'], 'Time_sequence_2', '时序通道 2'))
    temperature_switch = None
    if devices.get('temp_switch') is not None and 'temp_switch' in channels:
        temperature_switch = TemperatureSwitchRestore(devices['temp_switch'], channels['temp_switch'])
    scope = devices.get('scope')
    if scope is not None:
        extra_actions.append(ShutdownAction('停止示波器触发失败', scope.trigger_stop))
    return run_safety_shutdown(dg_channels=dg_channels, temperature_switch=temperature_switch, extra_actions=extra_actions, disconnect_targets=(DisconnectTarget('TEC', devices.get('tec')),), preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, 'Time_sequence'))

def _identity(device: Any) -> str | None:
    try:
        return str(device.idn())
    except Exception:
        return None

def _device_snapshot(mapping: dict[str, dict[str, Any]], devices: dict[str, Any]) -> dict[str, Any]:
    keys = ('main_magnetic_field', 'X_magnetic_field', 'Y_magnetic_field', 'Z_magnetic_field', 'Time_sequence_2', 'Pump_laser_power', 'Probe_laser_power', 'Pump_modulation', 'Time_sequence', 'Temp_Switch', 'scope_waveform', 'lockin_xy', 'temperature')
    identities = {name: identity for name, device in devices.items() if (identity := _identity(device)) is not None}
    return {'mapping': {key: mapping[key] for key in keys if key in mapping}, 'identity_readback': identities}

def _connect_devices(mapping: dict[str, dict[str, Any]], session: DeviceSession) -> tuple[dict[str, Any], dict[str, int]]:
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    gs_cfg = mapping['main_magnetic_field']
    devices['gs200'] = session.connect('gs200', gs_cfg['resource'], lambda: GS200Instrument(gs_cfg['resource']))
    devices['gs200'].set_current_limit(0.015)
    for semantic, key in (('x_field', 'X_magnetic_field'), ('y_field', 'Y_magnetic_field')):
        cfg = mapping[key]
        devices[semantic] = session.connect(semantic, cfg['resource'], lambda cfg=cfg: create_signal_generator(cfg))
        channels[semantic] = int(cfg['channel'])
    z_cfg = mapping['Z_magnetic_field']
    time_sequence_2_cfg = mapping['Time_sequence_2']
    devices['z_field'], routed_channels = connect_signal_generator_routes(session, 'z_field', {'z_field': ('Z_magnetic_field', z_cfg), 'time_sequence_2': ('Time_sequence_2', time_sequence_2_cfg)})
    channels.update(routed_channels)
    pump_cfg = mapping['Pump_laser_power']
    probe_cfg = mapping['Probe_laser_power']
    devices['laser'], routed_channels = connect_signal_generator_routes(session, 'laser', {'pump': ('Pump_laser_power', pump_cfg), 'probe': ('Probe_laser_power', probe_cfg)})
    channels.update(routed_channels)
    carrier_cfg = mapping['Pump_modulation']
    gate_cfg = mapping['Time_sequence']
    devices['rf_switch'], routed_channels = connect_signal_generator_routes(session, 'rf_switch', {'aom_carrier': ('Pump_modulation', carrier_cfg), 'rf_gate': ('Time_sequence', gate_cfg)})
    channels.update(routed_channels)
    temp_cfg = mapping['Temp_Switch']
    devices['temp_switch'] = session.connect('temp_switch', temp_cfg['resource'], lambda: create_signal_generator(temp_cfg))
    channels['temp_switch'] = int(temp_cfg['channel'])
    scope_cfg = mapping['scope_waveform']
    devices['scope'] = session.connect('scope', scope_cfg['resource'], lambda: SDSInstrument(scope_cfg['resource']))
    devices['acquirer'] = session.bind('acquirer', SDSAcquisition(devices['scope']))
    hf_cfg = mapping['lockin_xy']
    devices['hf2'] = session.connect('hf2', f'''hf2://{hf_cfg.get('host', '127.0.0.1')}/{hf_cfg['device_id']}''', lambda: create_lockin(hf_cfg))
    initialize_lockin_inputs(devices['hf2'])
    tec_cfg = mapping['temperature']
    devices['tec'] = session.connect_optional('tec', tec_cfg['resource'], lambda: TECInstrument(port=tec_cfg['resource']), device_label='TEC103')
    return (devices, channels)

def _configure_scope(params: T2CalibrationParams, devices: dict[str, Any], *, wait) -> tuple[AcquisitionConfig, dict[str, Any]]:
    channel_configs: list[ChannelConfig] = []
    for channel in range(1, 5):
        if channel == params.scope_pd_channel:
            channel_configs.append(ChannelConfig(number=channel, enabled=True, scale=0.5, offset=0.0, coupling='DC', impedance='ONEMeg', probe=1.0))
        elif channel == params.scope_trig_channel:
            channel_configs.append(ChannelConfig(number=channel, enabled=True, scale=2.0, offset=2.5, coupling='DC', impedance='ONEMeg', probe=1.0))
        else:
            channel_configs.append(ChannelConfig(number=channel, enabled=False))
    config = AcquisitionConfig(sampling_rate=params.scope_sample_rate, sampling_time=params.scope_duration, acquire_type='NORMal', acquire_delay=0.5, channels=channel_configs, trigger=TriggerConfig(mode='NORMal', source=f'''C{params.scope_trig_channel}''', type='EDGE', slope=params.scope_trig_slope, level=1.5))
    acquirer: SDSAcquisition = devices['acquirer']
    scope: SDSInstrument = devices['scope']
    acquirer.apply_config(config)
    scope.set_memory_depth('100K')
    wait(0.1)
    return (config, {'requested_sample_rate_Sa_s': params.scope_sample_rate, 'actual_sample_rate_Sa_s': float(scope.get_sampling_rate()), 'memory_depth': str(scope.get_memory_depth()), 'actual_points': int(scope.get_actual_points()), 'pd_channel': params.scope_pd_channel, 'trigger_channel': params.scope_trig_channel, 'trigger_source': f'''C{params.scope_trig_channel}''', 'trigger_slope': params.scope_trig_slope, 'trigger_level_V': 1.5})

def _configure_outputs(params: T2CalibrationParams, devices: dict[str, Any], channels: dict[str, int]) -> dict[str, Any]:
    temperature_status = configure_temperature_control(devices.get('tec'), params.tec_temperature, channel=1)
    actual_temperature = temperature_status.actual_temperature_c
    gs200 = devices['gs200']
    validate_safety_limit('main_magnetic_field', params.main_field_ma)
    gs200.set_current(params.main_field_ma / 1000.0)
    validate_safety_limit('main_magnetic_field', params.main_field_ma)
    gs200.set_output(True)
    laser = devices['laser']
    validate_safety_limit('Pump_laser_power', params.pump_power)
    laser.setup_dc(params.pump_power, channel=channels['pump'])
    initial_probe = params.probe_power_values[0] if params.do_power_scan else params.probe_power
    validate_safety_limit('Probe_laser_power', initial_probe)
    laser.setup_dc(initial_probe, channel=channels['probe'])
    rf = devices['rf_switch']
    validate_safety_limit('Pump_modulation', params.aom_carrier_amplitude)
    rf.setup_sine(params.aom_carrier_freq, params.aom_carrier_amplitude, offset=0.0, channel=channels['aom_carrier'])
    validate_safety_limit('Pump_modulation', params.aom_carrier_amplitude)
    rf.set_output(True, channel=channels['aom_carrier'])
    hf2 = devices['hf2']
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range, diff=False))
    actual_rate = demod.configure_demodulator(hf2, DemodulatorConfig(demod_index=params.hf2_demod_idx, enable=True, rate=params.hf2_demod_rate, input_channel=0, osc_select=0, harmonic=1, time_constant=params.hf2_demod_tc, order=params.hf2_demod_order, phase=0.0))
    hf2.set_double(f'''{hf2.osc_path(0)}/freq''', params.hf2_osc_freq)
    hf2.sync()
    demod_path = hf2.demod_path(params.hf2_demod_idx)
    gate_low = params.rf_gate_offset - params.rf_gate_amplitude / 2
    gate_high = params.rf_gate_offset + params.rf_gate_amplitude / 2
    validate_safety_limit('Time_sequence', gate_low)
    validate_safety_limit('Time_sequence', gate_high)
    gate_channel = channels['rf_gate']
    rf.set_burst_state(False, channel=gate_channel)
    rf.set_mod_state(False, channel=gate_channel)
    rf.apply_wave('PULSe', freq=params.rf_gate_freq, amp=params.rf_gate_amplitude, offset=params.rf_gate_offset, channel=gate_channel)
    validate_safety_limit('PUMP_MOD_DUTY', params.rf_gate_duty)
    rf.set_pulse_dcycle(params.rf_gate_duty, channel=gate_channel)
    rf.set_burst_mode('TRIGgered', channel=gate_channel)
    rf.set_burst_ncycles(params.burst_ncycles, channel=gate_channel)
    rf.set_burst_period(params.burst_period, channel=gate_channel)
    rf.set_burst_trigger_source('INTernal', channel=gate_channel)
    validate_safety_limit('Time_sequence', gate_high)
    rf.set_burst_state(True, channel=gate_channel)
    validate_safety_limit('Time_sequence', gate_high)
    rf.set_sync_state(True, channel=gate_channel)
    validate_safety_limit('Time_sequence', gate_high)
    rf.set_output(True, channel=gate_channel)
    return {'temperature': {'target_C': params.tec_temperature, 'actual_C': actual_temperature, 'channel': 1, 'controlled_by_experiment': temperature_status.controlled_by_experiment, 'control_source': temperature_status.control_source}, 'main_field': {'current_mA': params.main_field_ma, 'hardware_current_limit_A': 0.015}, 'laser': {'pump_V': params.pump_power, 'initial_probe_V': float(initial_probe), 'pump_channel': channels['pump'], 'probe_channel': channels['probe']}, 'rf_gate': {'carrier_frequency_Hz': params.aom_carrier_freq, 'carrier_amplitude_V': params.aom_carrier_amplitude, 'gate_frequency_Hz': params.rf_gate_freq, 'gate_amplitude_Vpp': params.rf_gate_amplitude, 'gate_offset_V': params.rf_gate_offset, 'gate_duty_pct': params.rf_gate_duty, 'burst_ncycles': params.burst_ncycles, 'burst_period_s': params.burst_period, 'trigger_source': 'INTernal'}, 'hf2': {'device_id': hf2.device_id, 'demod_index': params.hf2_demod_idx, 'requested_rate_Sa_s': params.hf2_demod_rate, 'actual_rate_Sa_s': float(actual_rate), 'actual_rate_readback_Sa_s': float(hf2.get_double(f'''{demod_path}/rate''')), 'actual_time_constant_s': float(hf2.get_double(f'''{demod_path}/timeconstant''')), 'actual_order': int(hf2.get_int(f'''{demod_path}/order''')), 'actual_adcselect': int(hf2.get_int(f'''{demod_path}/adcselect''')), 'actual_oscselect': int(hf2.get_int(f'''{demod_path}/oscselect''')), 'actual_oscillator_frequency_Hz': float(hf2.get_double(f'''{hf2.osc_path(0)}/freq'''))}}
