"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import BperpNullParams

EXPERIMENT_ID = "bperp-null"
DATA_TYPE = "bperp_null"
RAW_FORMAT_VERSION = 1

SOURCE_KEY = "Z_magnetic_field"
X_KEY = "X_magnetic_field"
Y_KEY = "Y_magnetic_field"
TRIGGER_KEY = "Time_sequence_2"
TEMP_SWITCH_KEY = "Temp_Switch"
LOCKIN_KEY = "lockin_r"
AMPLIFIER_KEY = "coil_drive_amplifier"
MAIN_FIELD_KEY = "main_magnetic_field"
TEC_KEY = "temperature"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
REQUIRED_MAPPING_KEYS = (SOURCE_KEY, X_KEY, Y_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY,
                         AMPLIFIER_KEY, MAIN_FIELD_KEY, TEC_KEY)

GRID_FILE = "raw/bias_grid.csv"
GRID_FIELDS = ("width_us", "x_offset_mv", "y_offset_mv", "x_bias_v", "y_bias_v", "repeat",
               "x2_v", "y2_v", "x1_v", "y1_v", "temp_c", "timestamp")
SUMMARY_FILE = "results/null_summary.json"
NEXT_RUN_FILE = "results/next_run.yaml"
MAP_PLOT_FILE = "results/bias_map.png"
RESIDUAL_PLOT_FILE = "results/residuals.png"


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def require_raw_format(config: dict) -> None:
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("运行配置不属于本实验")
    version = config.get("raw_format_version")
    if type(version) is not int or version != RAW_FORMAT_VERSION:
        raise ValueError("原始数据格式未知或不受支持；历史格式须显式识别并验证")


def parameters(config) -> BperpNullParams:
    return BperpNullParams.from_external(config["parameters"],
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


def initial_metadata(p: BperpNullParams):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
                execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
                completion_status="running", data_files=[], actual_rates={},
                temperature_gating={"scope": "each_temperature_group", "off_voltage_v": 0.0,
                                    "on_voltage_v": 5.0, "group_size": p.temp_group_size,
                                    "on_lag_s": p.temp_switch_on_lag_s,
                                    "reason": "普通气室采集：温控关闭窗口内只读不写盘，组末恢复温控后再保存"},
                predicted={"amps_per_drive_volt": p.amps_per_drive_volt,
                           "loop_resistance_ohm": p.loop_resistance_ohm, "pulse_vpp": p.pulse_vpp,
                           "pulse_period_us": p.pulse_period_us,
                           "predicted_width_us": p.predicted_width_us(),
                           "x_bias_mv": p.x_bias_mv, "y_bias_mv": p.y_bias_mv,
                           "x_offsets_mv": p.x_offsets_mv(), "y_offsets_mv": p.y_offsets_mv(),
                           "grid_points": len(p.scan_voltages())})
