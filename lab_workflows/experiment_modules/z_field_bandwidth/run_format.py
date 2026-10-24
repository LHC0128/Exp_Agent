"""实验身份、独立原始版本及原手工快照的明确迁移。"""
from .models import ZFieldBandwidthParams

EXPERIMENT_ID = "z-field-bandwidth"
DATA_TYPE = "Z_Field_Bandwidth_Measurement"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = ("main_magnetic_field", "Z_magnetic_field", "Time_sequence_2", "X_magnetic_field", "Y_magnetic_field",
    "Pump_laser_power", "Probe_laser_power", "Pump_modulation", "Time_sequence", "Temp_Switch", "temperature", "lockin_r")


def format_block_reason(config):
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("experiment_type", config.get("data_type")) != DATA_TYPE:
        return "运行目录不是 Z 场带宽实验"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的 Z 场带宽原始数据格式版本"
    elif config.get("schema_version", 1) not in (1, 2) or not all(isinstance(config.get(key), dict) for key in
            ("freq_sweep", "hf2_demod0", "hf2_demod_r")):
        return "缺少已验证的旧 Z 带宽配置结构，不能猜测原始格式"
    if config.get("acquisition_mode") not in ("daq_fft_y", "demod_r", "both"):
        return "未知 Z 场带宽采集模式"
    return None


def analysis_block_reason(config):
    return format_block_reason(config) or ("采集仍在写入，请等待本轮收尾" if config.get("completion_status") == "running" else None)


def parameters(config):
    if reason := format_block_reason(config):
        raise ValueError(reason)
    if "parameters" in config:
        return ZFieldBandwidthParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    values = {"RUN_TAG": config.get("run_tag", "z_bw"), "ACQUISITION_MODE": config["acquisition_mode"]}
    groups = {
        "freq_sweep": {"Z_FREQ_START_Hz": "Z_FREQ_START", "Z_FREQ_STOP_Hz": "Z_FREQ_STOP", "Z_FREQ_POINTS": "Z_FREQ_POINTS",
            "Z_DRIVE_AMPLITUDE_Vpp": "Z_DRIVE_AMPLITUDE", "POINT_DURATION_s": "POINT_DURATION", "FREQ_SETTLE_TIME_s": "FREQ_SETTLE_TIME"},
        "hf2_demod0": {"demod_idx": "HF2_DEMOD0_IDX", "osc_idx": "HF2_DEMOD0_OSC_IDX", "osc_freq_Hz": "HF2_DEMOD0_OSC_FREQ", "signal_range_V": "HF2_DEMOD0_SIGNAL_RANGE",
            "rate_Sa_s_requested": "HF2_DEMOD0_RATE", "TC_meas_s": "HF2_DEMOD0_TC", "TC_calib_s": "HF2_DEMOD0_TC_CALIB", "order": "HF2_DEMOD0_ORDER"},
        "hf2_demod_r": {"demod_idx": "DEMOD_R_IDX", "osc_idx": "DEMOD_R_OSC_IDX", "adcselect_requested": "DEMOD_R_ADC_SELECT", "rate_Sa_s_requested": "DEMOD_R_RATE", "TC_min_s": "DEMOD_R_TC_MIN",
            "TC_per_period": "DEMOD_R_TC_PER_PERIOD", "order_requested": "DEMOD_R_ORDER", "n_avg": "DEMOD_R_N_AVG", "avg_interval_s": "DEMOD_R_AVG_INTERVAL", "read_settle_time_s": "DEMOD_R_READ_SETTLE_TIME"},
        "hf2_aux_out_2": {"enabled": "CONFIGURE_AUXOUT2_TO_DEMOD0_Y", "auxout_index": "AUXOUT_INDEX", "source_demod_requested": "AUXOUT_SOURCE_DEMOD_IDX",
            "source_select_requested": "AUXOUT_SOURCE_SELECT", "scale_requested": "AUXOUT_SCALE", "offset_requested": "AUXOUT_OFFSET"},
        "hf2_signal_input_2": {"input_index": "SIGIN2_INDEX", "range_V_requested": "SIGIN2_RANGE"},
        "pump_modulation": {"PUMP_MOD_FREQ_Hz": "PUMP_MOD_FREQ", "PUMP_MOD_DUTY_pct": "PUMP_MOD_DUTY", "PUMP_MOD_AMPLITUDE_Vpp": "PUMP_MOD_AMPLITUDE",
            "RF_GATE_AMPLITUDE_Vpp": "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET_V": "RF_GATE_OFFSET"},
    }
    for group, keys in groups.items():
        record = config.get(group, {})
        values.update({external: record[key] for key, external in keys.items() if key in record})
    values.update({"FIXED_PARAMS."+key: value for key, value in config.get("fixed_params", {}).items()
                   if "FIXED_PARAMS."+key in ZFieldBandwidthParams.external_names()})
    if "Z_V_to_nT" in config:
        values["Z_V_TO_NT"] = config["Z_V_to_nT"]
    return ZFieldBandwidthParams.from_external(values)


def initial_metadata(p):
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
        completion_status="running", data_files=[], acquisition_mode=p.acquisition_mode,
        freq_sweep=dict(Z_FREQ_START_Hz=p.z_freq_start, Z_FREQ_STOP_Hz=p.z_freq_stop, Z_FREQ_POINTS=p.z_freq_points,
            freq_list_Hz=p.frequency_axis().tolist(), Z_DRIVE_AMPLITUDE_Vpp=p.z_drive_amplitude,
            POINT_DURATION_s=p.point_duration_s, FREQ_SETTLE_TIME_s=p.system_settle_s,
            is_baseline_handling="0 Hz 为 Z 0 V DC + OFF 背景，不参与带宽或峰值；X/Y 输出保持关闭"),
        fixed_params={key.removeprefix("FIXED_PARAMS."): value for key, value in p.to_external().items() if key.startswith("FIXED_PARAMS.")},
        Z_V_to_nT=p.z_v_to_nt, Z_V_to_fT=p.z_v_to_nt*1e6,
        temperature_gating=dict(scope="each_frequency", off_voltage_v=0, on_voltage_v=5))
