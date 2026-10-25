"""本实验设备准备、初始条件与配置快照；导入无硬件副作用。"""
from lab_workflows.steps import initialize_lockin_inputs
from lab_workflows.devices import create_lockin
import numpy as np
from lab_workflows.steps.run_directory import write_run_config
from datetime import datetime
from gs200 import GS200Instrument
from lab_workflows.instrument_config import instrument_config_snapshot
from lab_workflows.experiment_runtime import apply_runtime_params
from lab_workflows.steps import configure_temperature_control, connect_signal_generator_routes, synchronize_connected_clocks
from tec_controller import TECInstrument
from sds_acquisition import SDSInstrument
from functools import partial
from lab_workflows.common import validate_safety_limit

def settings(ctx):
    apply_runtime_params(vars(ctx), ctx.params)
    ctx.EXPERIMENT_TYPE = 'RF_Field_Sensitivity_AW_FreqSweep_DirectAW'
    ctx.POINT_DURATION_s = 1.0
    ctx.XY_AW_AUTO_FLOOR_VPP = 0.001
    ctx.Z_V_TO_NT = 3517 / 2
    ctx.DEMOD3_TC_MIN_s = 0.01
    ctx.DEMOD3_AVG_INTERVAL_s = 0.01
    ctx.DEMOD0_OSC_FREQ = ctx.PUMP_MOD_FREQ
    ctx.DEMOD3_ADC_SELECT = ctx.SIGIN2_INDEX
    for ctx.k, ctx.v in ctx.FIXED_PARAMS.items():
        validate_safety_limit(ctx.k, ctx.v, ctx.LIMITS)
    validate_safety_limit('Z_magnetic_field', ctx.Z_RF_AMPLITUDE, ctx.LIMITS)
    validate_safety_limit('rf_coil', 0.0, ctx.LIMITS)
    if ctx.Z_RF_FREQ_LOG_SPACED:
        ctx.freq_list = np.logspace(np.log10(max(ctx.Z_RF_FREQ_START, 1)), np.log10(ctx.Z_RF_FREQ_STOP), ctx.Z_RF_FREQ_POINTS)
    else:
        ctx.freq_list = np.linspace(ctx.Z_RF_FREQ_START, ctx.Z_RF_FREQ_STOP, ctx.Z_RF_FREQ_POINTS) + ctx.Z_RF_FREQ_OFFSET_HZ
    ctx.has_baseline = bool(np.any(np.isclose(ctx.freq_list, 0.0)))
    print(f'''实验类型: {ctx.EXPERIMENT_TYPE}''')
    print(f'''运行标签: {ctx.RUN_TAG}''')
    print(f'''采集模式: {ctx.ACQUISITION_MODE}''')
    print(f'''Z 射频场幅度: {ctx.Z_RF_AMPLITUDE} Vpp = {ctx.Z_RF_AMPLITUDE * ctx.Z_V_TO_NT:.2f} nT p-p''')
    print('Z 射频场驱动: continuous sine (Burst OFF)')
    print(f'''频率扫描: {ctx.freq_list[0]:.1f} ~ {ctx.freq_list[-1]:.1f} Hz, {len(ctx.freq_list)} 点''')
    if ctx.has_baseline:
        print(f'''  包含 0 Hz 基线点''')
    ctx.phase_list = np.linspace(ctx.PHASE_START, ctx.PHASE_STOP, ctx.PHASE_POINTS)
    print(f'''相位扫描列表 ({ctx.PHASE_POINTS} 点):''')
    print(f'''  范围: {ctx.phase_list[0]:.1f} ~ {ctx.phase_list[-1]:.1f} deg''')
    print('配置已加载')

def connect(ctx):
    try:
        ctx.gs_cfg = ctx.MAPPING['main_magnetic_field']
        ctx.gs = ctx.session.connect('gs200', ctx.gs_cfg['resource'], lambda: GS200Instrument(ctx.gs_cfg['resource']))
        print(f'''GS200 已连接: {ctx.gs.idn()}''')
        ctx.gs.set_source_function(ctx.gs_cfg['source_function'])
        ctx.gs.set_current_limit(ctx.LIMITS['main_magnetic_field']['max'] / 1000.0)
        ctx.devices['gs200'] = ctx.gs
        ctx.dg_comp_cfg = ctx.MAPPING['X_magnetic_field']
        ctx.dg_comp, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_comp', {'x': ('X_magnetic_field', ctx.dg_comp_cfg), 'y': ('Y_magnetic_field', ctx.MAPPING['Y_magnetic_field'])}, logical_channels={'x': 1, 'y': 2})
        print(f'''补偿场 DG4000 已连接: {ctx.dg_comp.idn()}''')
        for ctx.ch in (1, 2):
            ctx.dg_comp.set_burst_state(False, channel=ctx.ch)
            ctx.dg_comp.set_mod_state(False, channel=ctx.ch)
            ctx.dg_comp.setup_dc(0.0, channel=ctx.ch)
            ctx.dg_comp.set_output(False, channel=ctx.ch)
        ctx.devices['dg_comp'] = ctx.dg_comp
        ctx.dg_am_cfg = ctx.MAPPING['X_magnetic_field_AM']
        ctx.dg_am, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_am', {'x_am': ('X_magnetic_field_AM', ctx.dg_am_cfg), 'y_am': ('Y_magnetic_field_AM', ctx.MAPPING['Y_magnetic_field_AM'])}, logical_channels={'x_am': 1, 'y_am': 2})
        print(f'''dg_am 已连接: {ctx.dg_am.idn()}''')
        for ctx.ch in (1, 2):
            ctx.dg_am.set_burst_state(False, channel=ctx.ch)
            ctx.dg_am.set_mod_state(False, channel=ctx.ch)
            ctx.dg_am.setup_dc(0.0, channel=ctx.ch)
            ctx.dg_am.set_output(False, channel=ctx.ch)
        ctx.devices['dg_am'] = ctx.dg_am
        ctx.dg_sweep_cfg = ctx.MAPPING['Z_magnetic_field']
        ctx.dg_sweep, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_sweep', {'z': ('Z_magnetic_field', ctx.dg_sweep_cfg), 'sequence_2': ('Time_sequence_2', ctx.MAPPING['Time_sequence_2'])}, logical_channels={'z': 1, 'sequence_2': 2})
        print(f'''dg_sweep DG4000 已连接: {ctx.dg_sweep.idn()}''')
        for ctx.ch in (1, 2):
            ctx.dg_sweep.set_burst_state(False, channel=ctx.ch)
            ctx.dg_sweep.set_mod_state(False, channel=ctx.ch)
            ctx.dg_sweep.setup_dc(0.0, channel=ctx.ch)
            ctx.dg_sweep.set_output(False, channel=ctx.ch)
        ctx.devices['dg_sweep'] = ctx.dg_sweep
        ctx.dg_mod_cfg = ctx.MAPPING['Pump_modulation']
        ctx.dg_mod, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_mod', {'carrier': ('Pump_modulation', ctx.dg_mod_cfg), 'gate': ('Time_sequence', ctx.MAPPING['Time_sequence'])}, logical_channels={'carrier': 1, 'gate': 2})
        print(f'''调制 DG4000 已连接: {ctx.dg_mod.idn()}''')
        ctx.devices['dg_mod'] = ctx.dg_mod
        ctx.dg_temp_cfg = ctx.MAPPING['Temp_Switch']
        ctx.dg_temp, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_temp', {'temp': ('Temp_Switch', ctx.dg_temp_cfg)}, logical_channels={'temp': 2})
        print(f'''温控 DG900 已连接: {ctx.dg_temp.idn()}''')
        ctx.devices['dg_temp'] = ctx.dg_temp
        ctx.tec_cfg = ctx.MAPPING['temperature']
        ctx.tec = ctx.session.connect_optional('tec', ctx.tec_cfg['resource'], lambda: TECInstrument(port=ctx.tec_cfg['resource']), device_label='TEC103')
        ctx.devices['tec'] = ctx.tec
        ctx.dg_laser_cfg = ctx.MAPPING['Pump_laser_power']
        ctx.dg_laser, ctx._ = connect_signal_generator_routes(ctx.session, 'dg_laser', {'pump': ('Pump_laser_power', ctx.dg_laser_cfg), 'probe': ('Probe_laser_power', ctx.MAPPING['Probe_laser_power'])}, logical_channels={'pump': 1, 'probe': 2})
        print(f'''光功率 DG900 已连接: {ctx.dg_laser.idn()}''')
        ctx.devices['dg_laser'] = ctx.dg_laser
        ctx.hf2_cfg = ctx.MAPPING['lockin_r']
        ctx.hfi = ctx.session.connect('hf2', f'''hf2://{ctx.hf2_cfg.get('host', '127.0.0.1')}/{ctx.hf2_cfg['device_id']}''', lambda: create_lockin(ctx.hf2_cfg))
        initialize_lockin_inputs(ctx.hfi)
        print(f'''HF2 已连接: {ctx.hfi.idn}''')
        ctx.devices['hf2'] = ctx.hfi
        ctx.clock_sources = synchronize_connected_clocks(ctx.devices, ctx.MAPPING, {'dg_comp': 'X_magnetic_field', 'dg_am': 'X_magnetic_field_AM', 'dg_sweep': 'Z_magnetic_field', 'dg_mod': 'Pump_modulation', 'dg_temp': 'Temp_Switch', 'dg_laser': 'Pump_laser_power', 'hf2': 'lockin_r'})
        ctx.scope = None
        try:
            ctx.scope_cfg = ctx.MAPPING.get('scope_waveform', {})
            ctx.scope = SDSInstrument(ctx.scope_cfg.get('resource', ''))
            ctx.scope.connect()
            print(f'''示波器已连接: {ctx.scope.idn()}''')
        except Exception as e:
            print(f'''示波器连接失败 (可选设备，继续): {e}''')
            ctx.scope = None
        ctx.devices['scope'] = ctx.scope
    except Exception as e:
        print(f'''设备连接失败: {e}''')
        # 编排器先安全收尾，再释放连接，防止回读时钟失败后留下输出。
        raise
    print(f'''\n所有设备连接完成，共 {len(ctx.devices)} 个设备''')

def initial_outputs(ctx):
    ctx.hfi = ctx.devices['hf2']
    ctx.tec = ctx.devices['tec']
    ctx.gs = ctx.devices['gs200']
    ctx.dg_laser = ctx.devices['dg_laser']
    ctx.dg_comp = ctx.devices['dg_comp']
    ctx.dg_sweep = ctx.devices['dg_sweep']
    ctx.dg_am = ctx.devices['dg_am']
    ctx.dg_mod = ctx.devices['dg_mod']
    ctx.dg_temp = ctx.devices['dg_temp']
    ctx.dg_laser.setup_dc(ctx.FIXED_PARAMS['Pump_laser_power'], channel=1)
    print(f'''Pump 光功率: {ctx.FIXED_PARAMS['Pump_laser_power']} V DC''')
    ctx.dg_laser.setup_dc(ctx.FIXED_PARAMS['Probe_laser_power'], channel=2)
    print(f'''Probe 光功率: {ctx.FIXED_PARAMS['Probe_laser_power']} V DC''')
    ctx.gs.set_current(ctx.FIXED_PARAMS['main_magnetic_field'] / 1000.0)
    ctx.gs.set_output(True)
    print(f'''主磁场: {ctx.FIXED_PARAMS['main_magnetic_field']} mA''')
    ctx.dg_temp.setup_dc(ctx.FIXED_PARAMS['Temp_Switch'], channel=2)
    print(f'''温度开关: ON ({ctx.FIXED_PARAMS['Temp_Switch']} V)''')
    ctx.temperature_status = configure_temperature_control(ctx.tec, ctx.FIXED_PARAMS['temperature'], channel=1)
    ctx.temp_now = ctx.temperature_status.actual_temperature_c
    if ctx.temp_now is not None:
        print(f'''TEC 目标温度已下发: {ctx.temp_now:.2f} °C''')
    for ctx.ch in (1, 2):
        ctx.dg_am.set_burst_state(False, channel=ctx.ch)
        ctx.dg_am.set_mod_state(False, channel=ctx.ch)
        ctx.dg_am.setup_dc(0.0, channel=ctx.ch)
        ctx.dg_am.set_output(False, channel=ctx.ch)
    print('dg_am CH1/CH2: standby DC=0.000V, 输出 OFF')
    print('\n--- Pump 调制配置 ---')
    ctx.dg_mod.set_burst_state(False, channel=1)
    ctx.dg_mod.set_mod_state(False, channel=1)
    ctx.dg_mod.set_burst_state(False, channel=2)
    ctx.dg_mod.set_mod_state(False, channel=2)
    validate_safety_limit('Pump_modulation', ctx.PUMP_MOD_AMPLITUDE, ctx.LIMITS)
    ctx.dg_mod.setup_sine(freq=100000000.0, amplitude=ctx.PUMP_MOD_AMPLITUDE, offset=0.0, phase=0.0, channel=1)
    print(f'''CH1: 100MHz 正弦, {ctx.PUMP_MOD_AMPLITUDE * 1000:.0f} mVpp → RF 开关 IN''')
    validate_safety_limit('Time_sequence', 5.0, ctx.LIMITS)
    ctx.dg_mod.setup_pulse(freq=ctx.PUMP_MOD_FREQ, amplitude=5.0, offset=2.5, channel=2)
    ctx.dg_mod.set_pulse_dcycle(ctx.PUMP_MOD_DUTY, channel=2)
    print(f'''CH2: 脉冲 {ctx.PUMP_MOD_FREQ / 1000:.0f} kHz, 占空比 {ctx.PUMP_MOD_DUTY}%, 5Vpp+2.5V offset''')
    ctx.dg_mod.set_sync_state(False, channel=2)
    print('CH2 SYNC: OFF (Z RF uses continuous sine)')

def write_configuration(ctx):
    ctx.timestamp = datetime.now().strftime('%m%d_%H%M')
    ctx.instrument_snapshot = instrument_config_snapshot(ctx.project_root)
    ctx.config = {'experiment_id': 'rf-sensitivity-direct-aw-frequency', 'schema_version': ctx.PARAMS.schema_version, 'execution_mode': 'typed_workflow', 'clock_sources': ctx.clock_sources, 'parameters': ctx.PARAMS.to_external(), 'experiment_type': ctx.EXPERIMENT_TYPE, 'purpose': 'RF field frequency response with direct X/Y arbitrary waveforms', 'timestamp': ctx.timestamp, 'run_tag': ctx.RUN_TAG, 'acquisition_mode': ctx.ACQUISITION_MODE, 'freq_sweep': {'Z_RF_FREQ_START_Hz': ctx.Z_RF_FREQ_START, 'Z_RF_FREQ_STOP_Hz': ctx.Z_RF_FREQ_STOP, 'Z_RF_FREQ_POINTS': ctx.Z_RF_FREQ_POINTS, 'Z_RF_FREQ_LOG_SPACED': ctx.Z_RF_FREQ_LOG_SPACED, 'freq_list_Hz': ctx.freq_list.tolist(), 'Z_RF_AMPLITUDE_Vpp': ctx.Z_RF_AMPLITUDE, 'Z_RF_DRIVE_MODE': ctx.Z_RF_DRIVE_MODE, 'Z_RF_PHASE_CONTROL': 'source_phase_adjust', 'FREQ_SETTLE_TIME_s': ctx.SYSTEM_SETTLE_S, 'POINT_DURATION_s': ctx.POINT_DURATION_s, 'RESPONSE_N_AVG': ctx.DEMOD3_N_AVG, 'has_baseline': ctx.has_baseline}, 'phase_sweep': {'PHASE_START_deg': ctx.PHASE_START, 'PHASE_STOP_deg': ctx.PHASE_STOP, 'PHASE_POINTS': ctx.PHASE_POINTS, 'phase_list_deg': ctx.phase_list.tolist(), 'PHASE_SETTLE_TIME_s': ctx.SYSTEM_SETTLE_S}, 'xy_direct_aw': {'waveform_source': ctx.ARB_WAVEFORM_SOURCE, 'corrected_control_source_run': ctx.CORRECTED_CONTROL_SOURCE_RUN if ctx.ARB_WAVEFORM_SOURCE == 'corrected_run' else None, 'A_ENV_FREQ_Hz': ctx.A_ENV_FREQ, 'XY_CTRL_K_Hz_per_V': ctx.XY_CTRL_K_HZ_PER_V, 'XY_CTRL_B_Hz': ctx.XY_CTRL_B_HZ, 'XY_AW_OUTPUT_VPP_auto': None, 'XY_AW_OUTPUT_OFFSET_V': 0.0, 'XY_CTRL_PHASE_initial_deg': ctx.XY_CTRL_PHASE, 'XY_CTRL_QUAD_deg': ctx.XY_CTRL_QUAD, 'PUMP_MOD_FREQ_Hz': ctx.PUMP_MOD_FREQ, 'ARB_WAVEFORM_FILE': ctx.ARB_WAVEFORM_FILE}, 'xy_trigger': {'source': 'dg_am CH1/CH2 fixed square -> dg_comp Ext Trig', 'XY_TRIGGER_FREQ_Hz': ctx.XY_TRIGGER_FREQ, 'XY_TRIGGER_AMPLITUDE_Vpp': ctx.XY_TRIGGER_AMPLITUDE, 'XY_TRIGGER_OFFSET_V': ctx.XY_TRIGGER_OFFSET, 'XY_TRIGGER_DUTY_pct': ctx.XY_TRIGGER_DUTY, 'XY_TRIGGER_PHASE_deg': ctx.XY_TRIGGER_PHASE}, 'hf2_demod0': {'demod_idx': ctx.DEMOD0_IDX, 'osc_idx': ctx.DEMOD0_OSC_IDX, 'osc_freq_Hz': ctx.DEMOD0_OSC_FREQ, 'signal_range_V': ctx.DEMOD0_SIGNAL_RANGE, 'rate_Sa_s': ctx.DEMOD0_RATE, 'TC_calib_s': ctx.DEMOD0_TC_CALIB, 'TC_meas_s': ctx.DEMOD0_TC_MEAS, 'order': ctx.DEMOD0_ORDER}, 'hf2_demod3': {'demod_idx': ctx.DEMOD3_IDX, 'osc_idx': ctx.DEMOD3_OSC_IDX, 'adcselect': ctx.DEMOD3_ADC_SELECT, 'signal_source': 'physical_loopback_auxout2_demod0_y_to_signal_input2', 'physical_link': 'AuxOut2 → physical cable → Signal Input 2 DC → Demod3', 'TC_min_s': ctx.DEMOD3_TC_MIN_s, 'TC_period_fraction': ctx.DEMOD3_TC_PERIOD_FRACTION, 'rate_Sa_s': ctx.DEMOD3_RATE, 'order': ctx.DEMOD3_ORDER, 'N_AVG': ctx.DEMOD3_N_AVG, 'AVG_INTERVAL_s': ctx.DEMOD3_AVG_INTERVAL_s, 'READ_SETTLE_TIME_s': ctx.SYSTEM_SETTLE_S}, 'physical_demod_chain': {'auxout_index': ctx.AUXOUT_INDEX, 'auxout_demod_source': ctx.AUXOUT_SOURCE_DEMOD_IDX, 'auxout_signal_select': {0: 'X', 1: 'Y', 2: 'R', 3: 'Theta'}[ctx.AUXOUT_SOURCE_SELECT], 'auxout_scale': ctx.AUXOUT_SCALE, 'auxout_offset': ctx.AUXOUT_OFFSET, 'sigin2_index': ctx.SIGIN2_INDEX, 'sigin2_DC_coupling': not ctx.SIGIN2_AC_COUPLING, 'sigin2_range_V': ctx.SIGIN2_RANGE, 'note': 'Legacy adcselect=2 is NOT usable. Physical loopback is required.'}, 'pump_modulation': {'PUMP_MOD_FREQ_Hz': ctx.PUMP_MOD_FREQ, 'PUMP_MOD_DUTY_pct': ctx.PUMP_MOD_DUTY, 'PUMP_MOD_AMPLITUDE_Vpp': ctx.PUMP_MOD_AMPLITUDE}, 'fixed_params': ctx.FIXED_PARAMS, 'Z_V_to_nT': ctx.Z_V_TO_NT, 'rf_coil_shared_channel_note': 'RF coil and Y_magnetic_field share the endpoint declared by the current mapping. When Z RF field output is ON, Y compensation field output MUST be OFF.', 'device_library_revision': ctx.instrument_snapshot['device_library_revision'], 'physical_mapping_revision': ctx.instrument_snapshot['physical_mapping_revision'], 'device_library_snapshot': ctx.instrument_snapshot['device_library'], 'physical_mapping_snapshot': ctx.instrument_snapshot['physical_mappings'], 'mapping_snapshot': ctx.instrument_snapshot['resolved_mapping'], 'safety_limits_snapshot': {key: ctx.LIMITS[key] for key in ctx.LIMITS if key in ('X_magnetic_field', 'Y_magnetic_field', 'Z_magnetic_field', 'X_magnetic_field_AM', 'Y_magnetic_field_AM', 'rf_coil', 'main_magnetic_field', 'Pump_modulation', 'Time_sequence', 'Temp_Switch', 'temperature')}}
    ctx.config_path = ctx.run_dir / 'experiment_config.yaml'
    write_run_config(ctx.config_path, ctx.config)
    print(f'''实验配置已保存: {ctx.config_path}''')
    print('\n初始值设置完成')
