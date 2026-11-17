"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import PiWidthScanParams

EXPERIMENT_ID = "pi-width-scan"
DATA_TYPE = "pi_width_scan"
RAW_FORMAT_VERSION = 1

SOURCE_KEY = "Z_magnetic_field"
X_KEY = "X_magnetic_field"
RF_KEY = "Y_magnetic_field"
TRIGGER_KEY = "Time_sequence_2"
TEMP_SWITCH_KEY = "Temp_Switch"
LOCKIN_KEY = "lockin_r"
AMPLIFIER_KEY = "coil_drive_amplifier"
SCOPE_KEY = "coil_current_scope"
MAIN_FIELD_KEY = "main_magnetic_field"
TEC_KEY = "temperature"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
REQUIRED_MAPPING_KEYS = (SOURCE_KEY, X_KEY, RF_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY,
                         AMPLIFIER_KEY, SCOPE_KEY, MAIN_FIELD_KEY, TEC_KEY)
# raw/：scan.csv 每行一个读数；R_<width>_<repeat>.csv 保存该次读数的完整序列；
# phase_scan.csv 保存 A03 相位粗扫每个角度的读数；相位曲线在 results/ 下。
SCAN_FILE = "raw/scan.csv"
PHASE_SCAN_FILE = "raw/phase_scan.csv"
PHASE_PLOT_FILE = "results/phase_vs_phase.png"
POINT_FILE_PREFIX = "raw/R_"


def point_file(width_us: float, repeat: int) -> str:
    return f"{POINT_FILE_PREFIX}{width_us:g}_{repeat}.csv"


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def parameters(config) -> PiWidthScanParams:
    return PiWidthScanParams.from_external(config["parameters"],
                                           schema_version=config.get("schema_version", 1))


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


def initial_metadata(p: PiWidthScanParams):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
                execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
                completion_status="running", data_files=[], actual_rates={},
                temperature_gating={"scope": "each_temperature_group", "off_voltage_v": 0.0,
                                    "on_voltage_v": 5.0, "group_size": p.temp_group_size,
                                    "on_lag_s": p.temp_switch_on_lag_s,
                                    "reason": "普通气室采集：温控关闭窗口内只读不写盘，组末恢复温控后再保存"},
                predicted={"amps_per_drive_volt": p.amps_per_drive_volt,
                           "loop_resistance_ohm": p.loop_resistance_ohm, "pulse_vpp": p.pulse_vpp,
                           "pulse_period_us": p.pulse_period_us, "widths_us": p.widths_us(),
                           "rf_phase_deg": p.y_rf_phase_deg,
                           "rf_phase_offset_us": p.rf_phase_offset_us(),
                           "rf_phase_angles_deg": p.rf_phase_angles_deg(),
                           "phase_check_enabled": p.phase_check_enabled,
                           "predicted_width_us": p.predicted_width_us(),
                           "phase_check_width_us": p.phase_check_width_us,
                           "x_bias_v": p.x_bias_v})
