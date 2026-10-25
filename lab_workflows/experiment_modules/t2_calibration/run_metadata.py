"""T2 运行快照字段，与既有原始格式独立。"""
from .models import T2CalibrationParams
EXPERIMENT_ID = "t2-calibration"
DATA_TYPE = "T2_Calibration"
EXECUTION_MODE = "typed_workflow"

def initial_metadata(params):
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE, completion_status='running', recovery_directory='raw/recovery', parameters=params.to_external(), pump_power_V=params.pump_power, aom_carrier_freq_Hz=params.aom_carrier_freq, aom_carrier_amplitude_V=params.aom_carrier_amplitude, rf_gate_freq_Hz=params.rf_gate_freq, rf_gate_amplitude_V=params.rf_gate_amplitude, rf_gate_offset_V=params.rf_gate_offset, rf_gate_duty_pct=params.rf_gate_duty, burst_ncycles=params.burst_ncycles, burst_period_s=params.burst_period, probe_power_V=params.probe_power, main_field_mA=params.main_field_ma, scope={'pd_channel': params.scope_pd_channel, 'trig_channel': params.scope_trig_channel, 'trig_slope': params.scope_trig_slope, 'sample_rate_Hz': params.scope_sample_rate, 'duration_s': params.scope_duration, 'acq_repeats': params.acq_repeats}, temperature_C=params.tec_temperature, compatibility={'stable_data_type': DATA_TYPE, 'raw_files': ['fid_waveforms.npz', 'fid_pNN.npz', 'power_scan_results.npz', 'power_scan_waveforms.npz'], 'fixed_parameter_aliases': params._fixed_aliases})
