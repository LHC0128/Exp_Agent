"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import CoilDriveCharacterizationParams

EXPERIMENT_ID = "coil-drive-characterization"
DATA_TYPE = "coil_drive_characterization"
RAW_FORMAT_VERSION = 1
SOURCE_KEY = "Z_magnetic_field"
AMPLIFIER_KEY = "coil_drive_amplifier"
SCOPE_KEY = "coil_current_scope"
CURRENT_LIMIT_KEY = "z_pulse_coil_current"
REQUIRED_MAPPING_KEYS = (SOURCE_KEY, AMPLIFIER_KEY, SCOPE_KEY)
# raw/*.npz：time(s)、sense_v(V)、sync_v(V)、meta(JSON 字符串：kind 与该帧激励)。
FRAME_KINDS = ("zero_off", "zero_on", "gain_check", "sine", "pulse")


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def parameters(config) -> CoilDriveCharacterizationParams:
    return CoilDriveCharacterizationParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))


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


def initial_metadata(p: CoilDriveCharacterizationParams):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
                execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION,
                completion_status="running", data_files=[],
                temperature_gating={"scope": "none", "reason": "纯电学工具，不连接气室温控"},
                predicted={"amps_per_drive_volt": p.amps_per_drive_volt,
                           "loop_resistance_ohm": p.loop_resistance_ohm, "gain": p.gain})
