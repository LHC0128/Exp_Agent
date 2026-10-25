"""本实验设备准备、初始条件与配置快照；导入无硬件副作用。"""
from lab_workflows.steps import initialize_lockin_inputs
from lab_workflows.devices import create_lockin
from datetime import datetime
import numpy as np
from lab_workflows.steps.run_directory import write_run_config
from gs200 import GS200Instrument
from lab_workflows.instrument_config import instrument_config_snapshot
from tec_controller import TECInstrument
from lab_workflows.experiment_runtime import apply_runtime_params
from lab_workflows.steps import configure_temperature_control, connect_signal_generator_routes, synchronize_connected_clocks
from functools import partial
from lab_workflows.common import validate_safety_limit
from .excitation import reset_dg_channels

def settings(ctx):
    apply_runtime_params(vars(ctx), ctx.params)
    ctx.EXPERIMENT_TYPE = 'XY_DirectAW_DC_Calibration'
    ctx.XY_AW_REPEAT_FREQ_Hz = 500.0
    ctx.XY_CTRL_PHASE_INITIAL_deg = 0.0
    ctx.XY_CTRL_QUAD_deg = 90.0
    ctx.XY_TRIGGER_FREQ_Hz = 100.0
    ctx.XY_TRIGGER_AMPLITUDE_Vpp = 5.0
    ctx.XY_TRIGGER_DUTY_pct = 50.0
    ctx.XY_TRIGGER_PHASE_deg = 0.0
    ctx.Z_RF_AMPLITUDE_Vpp = 0.01
    ctx.POINT_DURATION_s = 1.0
    ctx.PUMP_MOD_DUTY_pct = 5.0
    ctx.PUMP_MOD_AMPLITUDE_Vpp = 0.18
    ctx.DEMOD0_TC_CALIB_s = 0.001
    ctx.DEMOD0_TC_MEAS_s = 1e-06
    ctx.DEMOD0_RATE_Sa_s = 100000.0
    ctx.DEMOD3_RATE_Sa_s = 4800.0
    ctx.DEMOD3_TC_MIN_s = 0.01
    ctx.DEMOD3_AVG_INTERVAL_s = 0.01
    ctx.TEMP_SWITCH_ON_LAG_s = 1.0
    ctx.AW_AMPLITUDE_FLOOR_VPP = 0.001
    ctx.XY_CARRIER_FREQ_Hz = ctx.PUMP_MOD_FREQ_Hz
    ctx.XY_ENV_VOLTAGE_LIST_V = np.linspace(ctx.XY_ENV_VOLTAGE_START_V, ctx.XY_ENV_VOLTAGE_STOP_V, ctx.XY_ENV_VOLTAGE_POINTS)
    ctx.Z_RF_FREQ_LIST_Hz = np.arange(ctx.Z_RF_FREQ_START_Hz, ctx.Z_RF_FREQ_STOP_Hz + 0.5 * ctx.Z_RF_FREQ_STEP_Hz, ctx.Z_RF_FREQ_STEP_Hz)
    if ctx.XY_ENV_VOLTAGE_POINTS < 2:
        raise ValueError('DirectAW 扫描点数必须至少为 2')
    if ctx.XY_ENV_VOLTAGE_START_V >= ctx.XY_ENV_VOLTAGE_STOP_V:
        raise ValueError('DirectAW 扫描起始电压必须小于终止电压')
    if ctx.ACQUISITION_MODE not in ('daq_y', 'demod_rxy', 'both'):
        raise ValueError('ACQUISITION_MODE 必须是 daq_y、demod_rxy 或 both')
    for ctx.key, ctx.value in ctx.FIXED_PARAMS.items():
        validate_safety_limit(ctx.key, ctx.value, ctx.LIMITS)
    validate_safety_limit('Pump_modulation', ctx.PUMP_MOD_AMPLITUDE_Vpp, ctx.LIMITS)
    validate_safety_limit('Time_sequence', 5.0, ctx.LIMITS)
    validate_safety_limit('Z_magnetic_field', ctx.Z_RF_AMPLITUDE_Vpp, ctx.LIMITS)
    validate_safety_limit('rf_coil', 0.0, ctx.LIMITS)
    for ctx.key in ('X_magnetic_field', 'Y_magnetic_field'):
        validate_safety_limit(ctx.key, float(np.min(ctx.XY_ENV_VOLTAGE_LIST_V)), ctx.LIMITS)
        validate_safety_limit(ctx.key, float(np.max(ctx.XY_ENV_VOLTAGE_LIST_V)), ctx.LIMITS)
    ctx._nonzero_env = np.abs(ctx.XY_ENV_VOLTAGE_LIST_V[ctx.XY_ENV_VOLTAGE_LIST_V != 0.0])
    if ctx._nonzero_env.size and float(ctx._nonzero_env.min()) < 0.0005:
        raise ValueError('包络扫描存在非零但过小的点，自动 AW 幅度将低于仪器最小 0.001 Vpp')
    ctx.carrier_cycles = ctx.XY_CARRIER_FREQ_Hz / ctx.XY_AW_REPEAT_FREQ_Hz
    if not np.isclose(ctx.carrier_cycles, round(ctx.carrier_cycles)):
        raise ValueError('单个 AW 周期必须包含整数个 DirectAW 载波周期')
    ctx.trigger_ratio = ctx.XY_AW_REPEAT_FREQ_Hz / ctx.XY_TRIGGER_FREQ_Hz
    if not np.isclose(ctx.trigger_ratio, round(ctx.trigger_ratio)):
        raise ValueError('AW 重复频率必须是外部触发频率的整数倍')
    if ctx.XY_AW_POINTS > ctx.MAX_ARB_POINTS:
        raise ValueError('XY_AW_POINTS 超出当前信号源单波形点数上限')
    print(f'''DirectAW 包络扫描: {ctx.XY_ENV_VOLTAGE_LIST_V.tolist()} V''')
    print('AW 满量程自动跟随包络峰值（2 × |V_env|），offset = 0')
    print(f'''Z 频率扫描: {ctx.Z_RF_FREQ_LIST_Hz[0]:.0f} ~ {ctx.Z_RF_FREQ_LIST_Hz[-1]:.0f} Hz, step={ctx.Z_RF_FREQ_STEP_Hz:.0f} Hz''')

def connect(ctx):
    try:
        ctx.gs_cfg = ctx.MAPPING['main_magnetic_field']
        ctx.gs = ctx.session.connect('gs200', ctx.gs_cfg['resource'], lambda: GS200Instrument(ctx.gs_cfg['resource']))
        ctx.gs.set_source_function(ctx.gs_cfg['source_function'])
        ctx.gs.set_current_limit(ctx.LIMITS['main_magnetic_field']['max'] / 1000.0)
        ctx.devices['gs200'] = ctx.gs
        ctx.comp_cfg = ctx.MAPPING['X_magnetic_field']
        ctx.dg_comp, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_comp', {'x': ('X_magnetic_field', ctx.comp_cfg), 'y': ('Y_magnetic_field', ctx.MAPPING['Y_magnetic_field'])}, logical_channels={'x': 1, 'y': 2})
        ctx.devices['dg_comp'] = ctx.dg_comp
        reset_dg_channels(ctx, ctx.dg_comp)
        ctx.trigger_cfg = ctx.MAPPING['X_magnetic_field_AM']
        ctx.dg_trigger, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_trigger', {'x_am': ('X_magnetic_field_AM', ctx.trigger_cfg), 'y_am': ('Y_magnetic_field_AM', ctx.MAPPING['Y_magnetic_field_AM'])}, logical_channels={'x_am': 1, 'y_am': 2})
        ctx.devices['dg_trigger'] = ctx.dg_trigger
        reset_dg_channels(ctx, ctx.dg_trigger)
        ctx.sweep_cfg = ctx.MAPPING['Z_magnetic_field']
        ctx.dg_sweep, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_sweep', {'z': ('Z_magnetic_field', ctx.sweep_cfg), 'sequence_2': ('Time_sequence_2', ctx.MAPPING['Time_sequence_2'])}, logical_channels={'z': 1, 'sequence_2': 2})
        ctx.devices['dg_sweep'] = ctx.dg_sweep
        reset_dg_channels(ctx, ctx.dg_sweep)
        ctx.mod_cfg = ctx.MAPPING['Pump_modulation']
        ctx.dg_mod, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_mod', {'carrier': ('Pump_modulation', ctx.mod_cfg), 'gate': ('Time_sequence', ctx.MAPPING['Time_sequence'])}, logical_channels={'carrier': 1, 'gate': 2})
        ctx.devices['dg_mod'] = ctx.dg_mod
        reset_dg_channels(ctx, ctx.dg_mod)
        ctx.laser_cfg = ctx.MAPPING['Pump_laser_power']
        ctx.dg_laser, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_laser', {'pump': ('Pump_laser_power', ctx.laser_cfg), 'probe': ('Probe_laser_power', ctx.MAPPING['Probe_laser_power'])}, logical_channels={'pump': 1, 'probe': 2})
        ctx.devices['dg_laser'] = ctx.dg_laser
        ctx.temp_cfg = ctx.MAPPING['Temp_Switch']
        ctx.dg_temp, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_temp', {'temp': ('Temp_Switch', ctx.temp_cfg)}, logical_channels={'temp': 2})
        ctx.devices['dg_temp'] = ctx.dg_temp
        ctx.tec_cfg = ctx.MAPPING['temperature']
        ctx.tec = ctx.session.connect_optional('tec', ctx.tec_cfg['resource'], lambda: TECInstrument(port=ctx.tec_cfg['resource']), device_label='TEC103')
        ctx.devices['tec'] = ctx.tec
        ctx.hf2_cfg = ctx.MAPPING['lockin_r']
        ctx.hfi = ctx.session.connect('hf2', f'''hf2://{ctx.hf2_cfg.get('host', '127.0.0.1')}/{ctx.hf2_cfg['device_id']}''', lambda: create_lockin(ctx.hf2_cfg))
        initialize_lockin_inputs(ctx.hfi)
        ctx.devices['hf2'] = ctx.hfi
        ctx.clock_verification = synchronize_connected_clocks(ctx.devices, ctx.MAPPING, {'dg_comp': 'X_magnetic_field', 'dg_trigger': 'X_magnetic_field_AM', 'dg_sweep': 'Z_magnetic_field', 'dg_mod': 'Pump_modulation', 'dg_laser': 'Pump_laser_power', 'dg_temp': 'Temp_Switch', 'hf2': 'lockin_r'})
    except Exception:
        # 编排器先安全收尾，再释放连接，防止回读时钟失败后留下输出。
        raise
    print(f'''设备连接完成，共 {len(ctx.devices)} 个设备对象''')

def initial_outputs(ctx):
    ctx.dg_laser.setup_dc(ctx.FIXED_PARAMS['Pump_laser_power'], channel=1)
    ctx.dg_laser.setup_dc(ctx.FIXED_PARAMS['Probe_laser_power'], channel=2)
    ctx.gs.set_current(ctx.FIXED_PARAMS['main_magnetic_field'] / 1000.0)
    ctx.gs.set_output(True)
    ctx.dg_temp.setup_dc(ctx.FIXED_PARAMS['Temp_Switch'], channel=2)
    ctx.switch(True)
    ctx.temperature_status = configure_temperature_control(ctx.tec, ctx.FIXED_PARAMS['temperature'], channel=1)
    for ctx.ch in (1, 2):
        ctx.dg_mod.set_burst_state(False, channel=ctx.ch)
        ctx.dg_mod.set_mod_state(False, channel=ctx.ch)
    ctx.dg_mod.setup_sine(freq=100000000.0, amplitude=ctx.PUMP_MOD_AMPLITUDE_Vpp, offset=0.0, phase=0.0, channel=1)
    ctx.dg_mod.setup_pulse(freq=ctx.PUMP_MOD_FREQ_Hz, amplitude=5.0, offset=2.5, channel=2)
    ctx.dg_mod.set_pulse_dcycle(ctx.PUMP_MOD_DUTY_pct, channel=2)
    ctx.dg_mod.set_sync_state(False, channel=2)
    for ctx.ch in (1, 2):
        ctx.dg_comp.set_output(False, channel=ctx.ch)
        ctx.dg_trigger.set_output(False, channel=ctx.ch)
    ctx.dg_sweep.set_output(False, channel=1)

def write_configuration(ctx):
    ctx.timestamp = datetime.now().strftime('%m%d_%H%M')
    ctx.instrument_snapshot = instrument_config_snapshot(ctx.project_root)
    ctx.config = {'experiment_id': 'xy-direct-aw-dc-calibration', 'schema_version': ctx.PARAMS.schema_version, 'execution_mode': 'typed_workflow', 'parameters': ctx.PARAMS.to_external(), 'experiment_type': ctx.EXPERIMENT_TYPE, 'run_tag': ctx.RUN_TAG, 'timestamp': ctx.timestamp, 'purpose': '使用 DirectAW 自动满量程（2 × |V_env|，offset = 0）输出，标定恒定包络电压与实际响应峰位的映射', 'acquisition_mode': ctx.ACQUISITION_MODE, 'xy_direct_aw_scan': {'XY_ENV_VOLTAGE_LIST_V': ctx.XY_ENV_VOLTAGE_LIST_V.tolist(), 'aw_vpp_mode': 'auto_per_envelope', 'aw_vpp_formula': '2 * |V_env| per scan point', 'aw_offset_V': 0.0, 'repeat_freq_Hz': ctx.XY_AW_REPEAT_FREQ_Hz, 'waveform_points': ctx.XY_AW_POINTS, 'carrier_freq_Hz': ctx.XY_CARRIER_FREQ_Hz, 'quadrature_deg': ctx.XY_CTRL_QUAD_deg, 'calibrated_phase_deg': float(ctx.xy_phase_deg), 'negative_voltage_note': 'negative envelope is equivalent to a 180-degree rotating-field phase flip'}, 'xy_trigger': {'freq_Hz': ctx.XY_TRIGGER_FREQ_Hz, 'amplitude_Vpp': ctx.XY_TRIGGER_AMPLITUDE_Vpp, 'offset_V': ctx.XY_TRIGGER_OFFSET_V, 'duty_pct': ctx.XY_TRIGGER_DUTY_pct, 'restart_policy': 'trigger outputs ON and phase init once -> dg_comp CH1/CH2 upload -> burst ON -> outputs ON'}, 'z_frequency_scan': {'freq_list_Hz': ctx.Z_RF_FREQ_LIST_Hz.tolist(), 'amplitude_Vpp': ctx.Z_RF_AMPLITUDE_Vpp, 'settle_time_s': ctx.SYSTEM_SETTLE_S, 'point_duration_s': ctx.POINT_DURATION_s}, 'phase_calibration': {'enabled': ctx.ENABLE_XY_PHASE_CAL, 'envelope_V': ctx.XY_PHASE_CAL_ENVELOPE_V, 'success': ctx.phase_cal_success, 'minimum_r_V': ctx.XY_PHASE_CAL_MIN_R_V, 'minimum_r_ratio': ctx.XY_PHASE_CAL_MIN_R_RATIO, 'best_r_V': ctx.direct_aw_phase_result.best_r_v if ctx.ENABLE_XY_PHASE_CAL else None, 'history': ctx.phase_history, 'demod0_calibrated_phase_deg': float(ctx.calibrated_phase_0_deg)}, 'hf2': {'demod0_actual_rate_cal_Sa_s': float(ctx.actual_rate_d0_cal), 'demod0_actual_rate_meas_Sa_s': float(ctx.actual_rate_d0_meas), 'demod3_initial_actual_rate_Sa_s': float(ctx.actual_rate_d3_initial), 'physical_loopback': 'Demod0 Y -> AuxOut2 -> SignalInput2 DC -> Demod3'}, 'pump_modulation': {'freq_Hz': ctx.PUMP_MOD_FREQ_Hz, 'duty_pct': ctx.PUMP_MOD_DUTY_pct, 'amplitude_Vpp': ctx.PUMP_MOD_AMPLITUDE_Vpp}, 'fixed_params': ctx.FIXED_PARAMS, 'clock_verification': ctx.clock_verification, 'device_library_revision': ctx.instrument_snapshot['device_library_revision'], 'physical_mapping_revision': ctx.instrument_snapshot['physical_mapping_revision'], 'device_library_snapshot': ctx.instrument_snapshot['device_library'], 'physical_mapping_snapshot': ctx.instrument_snapshot['physical_mappings'], 'mapping_snapshot': ctx.instrument_snapshot['resolved_mapping'], 'safety_limits_snapshot': ctx.LIMITS, 'data_files': []}
    ctx.config_path = ctx.run_dir / 'experiment_config.yaml'
    write_run_config(ctx.config_path, ctx.config)
    print(f'''运行目录: {ctx.run_dir}''')
