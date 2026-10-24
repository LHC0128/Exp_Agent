"""实验身份、独立 raw 版本及已验证的旧 ConstXY 配置迁移。"""
from .models import RFConstXYFrequencyParams

EXPERIMENT_ID = "rf-sensitivity-constxy-frequency"
DATA_TYPE = "RF_Field_Sensitivity_ConstXY_FreqSweep"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = ("main_magnetic_field", "Z_magnetic_field", "Time_sequence_2", "X_magnetic_field", "Y_magnetic_field",
    "X_magnetic_field_AM", "Y_magnetic_field_AM", "Pump_laser_power", "Probe_laser_power", "Pump_modulation",
    "Time_sequence", "Temp_Switch", "temperature", "lockin_r")


def format_block_reason(config):
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("experiment_type", config.get("data_type")) != DATA_TYPE:
        return "运行目录不是 ConstXY RF 扫频实验"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的 ConstXY 原始数据格式版本"
    elif config.get("schema_version", 1) not in (1, 2) or not all(isinstance(config.get(key), dict) for key in
            ("freq_sweep", "xy_const_field", "hf2_demod0", "hf2_demod_r")):
        return "缺少已验证的旧 ConstXY 配置结构，不能猜测原始格式"
    if config.get("acquisition_mode") not in ("daq_fft_y", "demod_r", "both"):
        return "未知 ConstXY 采集模式"
    return None


def analysis_block_reason(config):
    return format_block_reason(config) or ("采集仍在写入，请等待本轮收尾" if config.get("completion_status") == "running" else None)


def parameters(config):
    if reason := format_block_reason(config):
        raise ValueError(reason)
    if "parameters" in config:
        return RFConstXYFrequencyParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    values = {"RUN_TAG": config.get("run_tag", "constxy_freq"), "ACQUISITION_MODE": config["acquisition_mode"]}
    groups = {
        "xy_const_field": {"LARMOR_FREQ_Hz": "LARMOR_FREQ_HZ", "A_ENV_K_Hz_per_V": "A_ENV_K", "A_ENV_B_Hz": "A_ENV_B"},
        "freq_sweep": {"Z_RF_FREQ_START_Hz": "Z_RF_FREQ_START", "Z_RF_FREQ_STOP_Hz": "Z_RF_FREQ_STOP", "Z_RF_FREQ_POINTS": "Z_RF_FREQ_POINTS",
            "Z_RF_FREQ_LOG_SPACED": "Z_RF_FREQ_LOG_SPACED", "Z_RF_AMPLITUDE_Vpp": "Z_RF_AMPLITUDE", "POINT_DURATION_s": "POINT_DURATION_s", "FREQ_SETTLE_TIME_s": "FREQ_SETTLE_TIME"},
        "hf2_demod0": {"demod_idx": "HF2_DEMOD0_IDX", "osc_idx": "HF2_DEMOD0_OSC_IDX", "osc_freq_Hz": "HF2_DEMOD0_OSC_FREQ", "signal_range_V": "HF2_DEMOD0_SIGNAL_RANGE",
            "rate_Sa_s_requested": "HF2_DEMOD0_RATE", "TC_meas_s": "HF2_DEMOD0_TC", "TC_calib_s": "HF2_DEMOD0_TC_CALIB", "order": "HF2_DEMOD0_ORDER"},
        "hf2_demod_r": {"demod_idx": "DEMOD3_IDX", "osc_idx": "DEMOD3_OSC_IDX", "rate_Sa_s_requested": "DEMOD3_RATE", "TC_min_s": "DEMOD3_TC_MIN_s",
            "TC_per_period_fraction": "DEMOD3_TC_PERIOD_FRACTION", "order_requested": "DEMOD3_ORDER", "n_avg": "DEMOD3_N_AVG", "avg_interval_s": "DEMOD3_AVG_INTERVAL_s"},
        "hf2_aux_out_2": {"enabled": "CONFIGURE_AUXOUT2_TO_DEMOD0_Y", "auxout_index": "AUXOUT_INDEX", "source_demod_requested": "AUXOUT_SOURCE_DEMOD_IDX",
            "source_select_requested": "AUXOUT_SOURCE_SELECT", "scale_requested": "AUXOUT_SCALE", "offset_requested": "AUXOUT_OFFSET"},
        "hf2_signal_input_2": {"input_index": "SIGIN2_INDEX", "range_V_requested": "SIGIN2_RANGE"},
        "xy_carrier": {"XY_CARRIER_FREQ_Hz": "XY_CARRIER_FREQ", "X_CARRIER_AMPLITUDE_Vpp": "X_CARRIER_AMPLITUDE", "Y_CARRIER_AMPLITUDE_Vpp": "Y_CARRIER_AMPLITUDE",
            "X_CARRIER_PHASE_deg": "X_CARRIER_PHASE", "Y_CARRIER_PHASE_deg": "Y_CARRIER_PHASE", "X_AM_DEPTH_pct": "X_AM_DEPTH", "Y_AM_DEPTH_pct": "Y_AM_DEPTH"},
        "pump_modulation": {"PUMP_MOD_FREQ_Hz": "PUMP_MOD_FREQ", "PUMP_MOD_DUTY_pct": "PUMP_MOD_DUTY", "PUMP_MOD_AMPLITUDE_Vpp": "PUMP_MOD_AMPLITUDE",
            "RF_GATE_AMPLITUDE_Vpp": "RF_GATE_AMPLITUDE", "RF_GATE_OFFSET_V": "RF_GATE_OFFSET"},
    }
    for group, keys in groups.items():
        record = config.get(group, {})
        values.update({external: record[key] for key, external in keys.items() if key in record})
    values.update({"FIXED_PARAMS."+key: value for key, value in config.get("fixed_params", {}).items()
                   if "FIXED_PARAMS."+key in RFConstXYFrequencyParams.external_names()})
    calibration = config.get("xy_carrier_phase_calibration", {})
    values["ENABLE_XY_CARRIER_PHASE_CAL"] = calibration.get("enabled", False)
    if "x_carrier_phase_initial_deg" in calibration:
        values["X_CARRIER_PHASE"] = calibration["x_carrier_phase_initial_deg"]
    return RFConstXYFrequencyParams.from_external(values)


def initial_metadata(p):
    return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
        completion_status="running", data_files=[], acquisition_mode=p.acquisition_mode,
        xy_const_field=dict(LARMOR_FREQ_Hz=p.larmor_freq_hz, XY_DC_VOLTAGE_V=p.xy_dc_voltage, A_ENV_K_Hz_per_V=p.a_env_k, A_ENV_B_Hz=p.a_env_b),
        freq_sweep=dict(Z_RF_FREQ_START_Hz=p.z_rf_freq_start, Z_RF_FREQ_STOP_Hz=p.z_rf_freq_stop, Z_RF_FREQ_POINTS=p.z_rf_freq_points,
            Z_RF_FREQ_LOG_SPACED=p.z_rf_freq_log_spaced, freq_list_Hz=p.frequency_axis().tolist(), Z_RF_AMPLITUDE_Vpp=p.z_rf_amplitude,
            POINT_DURATION_s=p.point_duration_s, FREQ_SETTLE_TIME_s=p.system_settle_s,
            is_baseline_handling="0 Hz 仅关闭 Z（0 V DC + OFF）；XY 载波与 DC 包络保持开启；背景点不参与带宽及峰值"),
        fixed_params={key.removeprefix("FIXED_PARAMS."): value for key, value in p.to_external().items() if key.startswith("FIXED_PARAMS.")},
        Z_V_to_nT=3517/2, Z_V_to_fT=3517/2*1e6,
        temperature_gating=dict(scope="each_frequency", off_voltage_v=0, on_voltage_v=5),
        note_rf_coil="不连接 rf_coil 别名；Y 通道由 XY 载波独占")
