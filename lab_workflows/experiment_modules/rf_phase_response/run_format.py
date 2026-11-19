"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..z_chain_common.keys import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, LOCKIN_KEY, MAIN_FIELD_KEY,
                                   REQUIRED_MAPPING_KEYS, RF_KEY, SCOPE_KEY, SOURCE_KEY, TEC_KEY,
                                   TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY)
from .models import RfPhaseResponseParams

EXPERIMENT_ID = "rf-phase-response"
DATA_TYPE = "rf_phase_response"
# 1：首个格式；phase_response.csv 每行一次读数，模型残差与最佳相位在 B01 生成。
RAW_FORMAT_VERSION = 1
SUPPORTED_RAW_FORMAT_VERSIONS = (1,)

PHASE_RESPONSE_FILE = "raw/phase_response.csv"
PHASE_RESPONSE_PLOT = "results/phase_response.png"
SUMMARY_FILE = "results/phase_response_summary.json"
POINT_FILE_PREFIX = "raw/phase_"


def point_file(phase_deg: float, repeat: int) -> str:
    return f"{POINT_FILE_PREFIX}{phase_deg:g}_{repeat}.csv"


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def parameters(config) -> RfPhaseResponseParams:
    return RfPhaseResponseParams.from_external(config["parameters"],
                                               schema_version=config.get("schema_version", 1))


def recorded_parameters(config):
    return parameters(config).to_external()


def format_block_reason(config):
    if not isinstance(config, dict):
        return "运行配置必须为映射"
    if config.get("experiment_id") != EXPERIMENT_ID or config.get("data_type") != DATA_TYPE:
        return "运行目录属于其他实验"
    if config.get("raw_format_version") not in SUPPORTED_RAW_FORMAT_VERSIONS:
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


def initial_metadata(p: RfPhaseResponseParams):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
                execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
                completion_status="running", data_files=[], actual_rates={},
                temperature_gating={"scope": "each_temperature_group", "off_voltage_v": 0.0,
                                    "on_voltage_v": 5.0, "group_size": p.temp_group_size,
                                    "on_lag_s": p.temp_switch_on_lag_s,
                                    "reason": "普通气室采集：温控关闭窗口内只读不写盘，组末恢复温控后再保存"},
                predicted={"amps_per_drive_volt": p.amps_per_drive_volt,
                           "loop_resistance_ohm": p.loop_resistance_ohm, "pulse_vpp": p.pulse_vpp,
                           "pulse_period_us": p.pulse_period_us, "width_us": p.z_pulse_width_us,
                           "tail_comp": p.z_tail_comp_enabled,
                           "tail_tau_c_us": p.z_tail_tau_c_us,
                           "rf_phase_deg": p.y_rf_phase_deg,
                           "rf_phase_offset_us": p.rf_phase_offset_us(),
                           "phase_angles_deg": p.phase_angles_deg(),
                           "x_bias_v": p.x_bias_v, "y_bias_v": p.y_bias_v})
