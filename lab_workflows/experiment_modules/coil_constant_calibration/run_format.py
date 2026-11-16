"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import CoilConstantCalibrationParams

EXPERIMENT_ID = "coil-constant-calibration"
DATA_TYPE = "Coil_Constant_Calibration"
RAW_FORMAT_VERSION = 1
SOURCE_KEY = "Z_magnetic_field"
AMPLIFIER_KEY = "coil_drive_amplifier"
SCOPE_KEY = "coil_current_scope"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
REQUIRED_MAPPING_KEYS = (
    "main_magnetic_field", SOURCE_KEY, AMPLIFIER_KEY, SCOPE_KEY,
    "X_magnetic_field", "rf_coil", "Pump_laser_power", "Probe_laser_power",
    "Pump_modulation", "Time_sequence", "Temp_Switch", "lockin_r", "temperature",
)
# raw/*.npz：DC 核对帧（time/sense_v/meta）、引导频扫与逐电流点频扫。
FRAME_KINDS = ("zero_off", "zero_on", "dc_check", "current")


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def parameters(config) -> CoilConstantCalibrationParams:
    return CoilConstantCalibrationParams.from_external(
        config["parameters"], schema_version=config.get("schema_version", 1))


def recorded_parameters(config):
    return parameters(config).to_external()


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    if config.get("experiment_id") != EXPERIMENT_ID or config.get("data_type") != DATA_TYPE:
        return "运行目录属于其他实验"
    if config.get("raw_format_version") != RAW_FORMAT_VERSION:
        return "不支持该原始数据格式版本"
    try:
        parameters(config)
    except (KeyError, TypeError, ValueError) as exc:
        return f"历史参数快照无效：{exc}"
    return ""


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") == "running":
        return "测量模块仍在写入，请等待当前模块完成"
    return ""


def initial_metadata(p: CoilConstantCalibrationParams):
    return dict(
        experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
        execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
        completion_status="running", data_files=[],
        geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y",
                  "z_current_scan": "bidirectional" if p.bidirectional_scan else "single_pass"},
        main_field={"enabled": bool(p.main_field_enabled),
                    "current_ma": float(p.main_field_ma) if p.main_field_enabled else None},
        temperature_gating={"scope": "every_frequency_point", "off_voltage_v": 0.0, "on_voltage_v": 5.0,
                            "combined_settle_time_s": p.system_settle_s, "on_lag_s": p.temp_switch_on_lag_s},
        dc_check={},
        predicted={"amps_per_drive_volt": p.amps_per_drive_volt,
                   "loop_resistance_ohm": p.loop_resistance_ohm, "gain": p.gain},
    )
