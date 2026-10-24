"""实验身份、独立原始版本与旧配置的只读兼容。"""
from pathlib import Path
import numpy as np
from .models import BBPowerScanBurstParams

EXPERIMENT_ID = "bb-power-scan-burst"
DATA_TYPE = "bb_rpump_power_scan_burst"
RAW_FORMAT_VERSION = 1
REQUIRED_MAPPING_KEYS = ("main_magnetic_field", "Pump_laser_power", "Probe_laser_power", "Pump_modulation",
                         "Time_sequence", "Temp_Switch", "temperature", "scope_waveform")


def format_block_reason(config):
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("experiment_type", config.get("data_type")) != DATA_TYPE:
        return "运行目录不是 BB Burst Pump 功率扫描"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的 BB 功率扫描原始数据版本"
    elif not all(isinstance(config.get(key), dict) for key in
                 ("burst_pump", "pump_power_scan", "main_field", "probe", "aom_carrier", "scope")):
        return "缺少已验证的旧 BB 功率扫描配置结构"
    return None


def analysis_block_reason(config):
    return format_block_reason(config) or ("采集仍在写入，请等待本轮收尾" if config.get("completion_status") == "running" else None)


def parameters(config, run_dir=None):
    if reason := format_block_reason(config):
        raise ValueError(reason)
    if "parameters" in config:
        return BBPowerScanBurstParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    values = {"RUN_TAG": config.get("run_tag", "rpump_eff"), "TEC_TEMPERATURE": config.get("temperature_C", 100)}
    groups = {
        "burst_pump": {"modulation_freq_Hz": "MODULATION_FREQUENCY_HZ", "fixed_duty_cycle_pct": "FIXED_DUTY_CYCLE",
            "burst_cycles": "BURST_CYCLES", "burst_period_s": "BURST_BURST_PERIOD_S", "pre_baseline_s": "PRE_BASELINE_TIME_S",
            "post_dark_s": "POST_DARK_TIME_S", "rf_gate_high_V": "PUMP_RF_GATE_HIGH_V", "rf_gate_low_V": "PUMP_RF_GATE_LOW_V"},
        "pump_power_scan": {"pump_power_voltage_list_V": "PUMP_POWER"},
        "main_field": {"z_field_setpoint_mA": "Z_FIELD_SETPOINT"},
        "probe": {"probe_power_V": "PROBE_POWER"},
        "aom_carrier": {"freq_Hz": "AOM_CARRIER_FREQ_HZ", "amplitude_Vpp": "AOM_CARRIER_AMPLITUDE_V", "offset_V": "AOM_CARRIER_OFFSET_V"},
        "scope": {"sample_rate_Hz": "SDS_SAMPLE_RATE", "pd_channel": "SDS_PD_CHANNEL", "trig_channel": "SDS_TRIG_CHANNEL",
            "trig_level_V": "SDS_TRIG_LEVEL", "trig_slope": "SDS_TRIG_SLOPE", "volt_scale_pd_V_per_div": "SDS_VOLT_SCALE_PD",
            "timebase_delay_s": "SDS_TIMEBASE_DELAY_S"},
    }
    for group, keys in groups.items():
        values.update({external: config[group][key] for key, external in keys.items() if key in config[group]})
    for key in ("SYSTEM_SETTLE_S", "TEMP_SWITCH_OFF_DELAY_S", "TEMP_SWITCH_ON_DELAY_S", "ACQ_REPEATS"):
        if key in config:
            values[key] = config[key]
    if run_dir:
        shots = sorted((Path(run_dir) / "raw").glob("shot_p*_avg.npz"))
        if shots:
            with np.load(shots[0], allow_pickle=False) as shot:
                if "acq_repeats" in shot:
                    values["ACQ_REPEATS"] = int(shot["acq_repeats"])
    return BBPowerScanBurstParams.from_external(values)


def initial_metadata(p):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
        execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION, completion_status="running", data_files=[],
        burst_pump=dict(modulation_freq_Hz=p.modulation_frequency_hz, fixed_duty_cycle_pct=p.fixed_duty_cycle,
            burst_cycles=p.burst_cycles, burst_duration_s=p.burst_duration_s, burst_period_s=p.burst_burst_period_s,
            pre_baseline_s=p.pre_baseline_time_s, post_dark_s=p.post_dark_time_s, rf_gate_high_V=p.pump_rf_gate_high_v,
            rf_gate_low_V=p.pump_rf_gate_low_v, burst_trigger_source="INTernal", burst_mode="TRIGgered"),
        pump_power_scan=dict(pump_power_voltage_list_V=p.pump_power, control_channel="Pump_laser_power",
            rf_gate_role="Time_sequence 固定 TTL，不作为扫描轴"),
        main_field=dict(z_field_setpoint_mA=p.z_field_setpoint, larmor_freq_Hz=p.modulation_frequency_hz),
        probe=dict(probe_power_V=p.probe_power, control_channel="Probe_laser_power"),
        aom_carrier=dict(freq_Hz=p.aom_carrier_freq_hz, amplitude_Vpp=p.aom_carrier_amplitude_v, offset_V=p.aom_carrier_offset_v),
        scope=dict(sample_rate_Hz=p.sds_sample_rate, timebase_delay_s=p.sds_timebase_delay_s, total_acq_s=p.total_acq_s,
            pd_channel=p.sds_pd_channel, trig_channel=p.sds_trig_channel, trig_level_V=p.sds_trig_level,
            trig_slope=p.sds_trig_slope, volt_scale_pd_V_per_div=p.sds_volt_scale_pd),
        temperature_C=p.tec_temperature, temperature_gating=dict(scope="each_shot", off_voltage_v=0, on_voltage_v=5),
        SYSTEM_SETTLE_S=p.system_settle_s, TEMP_SWITCH_ON_DELAY_S=p.temp_switch_on_delay_s, ACQ_REPEATS=p.acq_repeats,
        preserved_end_policy="保留 Time_sequence 输出/Burst/SYNC 与最后写入 Pump 电压；恢复温控，只断开 TEC")
