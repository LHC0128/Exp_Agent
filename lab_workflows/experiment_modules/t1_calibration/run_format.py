"""采集/分析/历史共用身份与格式资格；不重写历史原始数据。"""
from pathlib import Path
import numpy as np
import yaml
from .models import T1CalibrationParams

EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION = "t1-calibration", "T1_Calibration", 1
REQUIRED_MAPPING_KEYS = ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "Probe_laser_power",
                        "Pump_modulation", "Time_sequence", "Temp_Switch", "temperature", "scope_waveform")
RAW_FILES = ("t1_measurement.npz", "power_scan_results.npz", "power_scan_waveforms.npz")


def format_block_reason(config):
    if not isinstance(config, dict):
        return "T1 配置必须为映射"
    if config.get("experiment_id") not in (None, EXPERIMENT_ID) or config.get("experiment_type", config.get("data_type")) != DATA_TYPE:
        return "运行目录不是 T1 标定"
    version = config.get("raw_format_version")
    if version is not None:
        if type(version) is not int or version != RAW_FORMAT_VERSION:
            return "不支持的 T1 原始数据版本"
    elif not (config.get("legacy_raw_only") is True or
            isinstance(config.get("scope"), dict) and all(k in config for k in
                ("pump_switch_freq_Hz", "pump_mod_amplitude_V", "pump_prep_time_s", "probe_power_V", "x_field_amplitude_V", "temperature_C"))):
        return "缺少已验证的旧 T1 配置结构"
    return None


def analysis_block_reason(config):
    return format_block_reason(config) or ("采集仍在写入，请等待本轮收尾" if config.get("completion_status") == "running" else None)


def raw_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root/"raw"/name).resolve()
    if root not in path.parents:
        raise ValueError("原始数据不能越出运行目录")
    return path


def load_config(run_dir):
    path = Path(run_dir)/"experiment_config.yaml"
    if path.exists():
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        # 早期真实运行仅有这组单点键；缺配置只读识别，不把任意无标记 NPZ 当成新版。
        single = raw_path(run_dir, "t1_measurement.npz")
        with np.load(single, allow_pickle=False) as data:
            if not {"probe_power", "T1", "T1_err", "t_axis", "vdiff_avg", "popt", "pcov"}.issubset(data.files):
                raise ValueError("缺配置且不是已验证的早期 T1 单点格式")
        config = dict(experiment_type=DATA_TYPE, legacy_raw_only=True)
    if reason := analysis_block_reason(config):
        raise ValueError(reason)
    return config


def parameters(config, run_dir=None):
    if reason := format_block_reason(config):
        raise ValueError(reason)
    if "parameters" in config:
        return T1CalibrationParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    fields = {"probe_power_V": "PROBE_POWER", "pump_switch_freq_Hz": "PUMP_SWITCH_FREQ",
        "pump_mod_amplitude_V": "PUMP_MOD_AMPLITUDE", "pump_prep_time_s": "PUMP_PREP_TIME",
        "x_field_amplitude_V": "X_FIELD_AMPLITUDE", "temperature_C": "TEC_TEMPERATURE"}
    values = {external: config[key] for key, external in fields.items() if key in config}
    scope_fields = {"sampling_rate_Hz": "SCOPE_SAMPLING_RATE", "acq_duration_s": "ACQ_DURATION",
        "acq_repeats": "ACQ_REPEATS", "trigger_source": "TRIGGER_SOURCE", "volt_scale_V_per_div": "SCOPE_VOLT_SCALE"}
    values.update({external: config["scope"][key] for key, external in scope_fields.items() if key in config.get("scope", {})})
    if run_dir:
        summary = raw_path(run_dir, "power_scan_results.npz")
        single = raw_path(run_dir, "t1_measurement.npz")
        values["RUN_TAG"] = config.get("run_tag") or Path(run_dir).name.split("_", 2)[-1]
        values["DO_POWER_SCAN"] = summary.is_file()
        path = summary if summary.is_file() else single
        if path.is_file():
            with np.load(path, allow_pickle=False) as data:
                if summary.is_file():
                    values["PROBE_POWER_LIST"] = data["probe_power"].tolist()
                else:
                    values["PROBE_POWER"] = float(data["probe_power"])
    if "SYSTEM_SETTLE_S" in config:
        values["SYSTEM_SETTLE_S"] = config["SYSTEM_SETTLE_S"]
    return T1CalibrationParams.from_external(values)


def initial_metadata(p):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
        execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION, completion_status="running", data_files=[],
        pump_switch_freq_Hz=p.pump_switch_freq, pump_mod_amplitude_V=p.pump_mod_amplitude,
        pump_prep_time_s=p.pump_prep_time, probe_power_V=p.probe_power, x_field_amplitude_V=p.x_field_amplitude,
        temperature_C=p.tec_temperature, SYSTEM_SETTLE_S=p.system_settle_s,
        scope=dict(sampling_rate_Hz=p.scope_sampling_rate, acq_duration_s=p.acq_duration, acq_repeats=p.acq_repeats,
            trigger_source=p.trigger_source, volt_scale_V_per_div=p.scope_volt_scale, signal_channel=1, trigger_slope="FALLing"),
        end_policy="X/Y/Z 归零关闭；保留 Probe、Pump 载波和 Time_sequence；温控 5 V + ON，只断开 TEC")
