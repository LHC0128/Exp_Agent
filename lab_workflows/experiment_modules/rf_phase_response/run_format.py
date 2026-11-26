"""采集、离线分析与历史接口共用的实验身份和原始数据格式资格。

格式 3（当前）：A03 整周扫描每个相位写一帧 ``raw/coarse_XX.npz``（``kind="xy"``，含
``time_s/x_v/y_v`` 与 ``burst_command_deg``、``carrier_command_deg``、锁相与 DAQ 快照）。
格式 1/2（历史）：``raw/phase_response.csv`` 逐行读数，格式 2 起带 X/Y 正交分量；历史目录
继续可分析，读取入口与原常量保持不变。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

import numpy as np
import yaml

from ..z_chain_common.keys import (LOCKIN_KEY, MAIN_FIELD_KEY, SOURCE_KEY, TEC_KEY,
                                   TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY)
from .models import RfPhaseResponseParams

EXPERIMENT_ID = "rf-phase-response"
DATA_TYPE = "rf_phase_response"
# 1：首个格式，仅 R/θ；2：phase_response.csv 与逐点序列增加 X/Y 正交分量；
# 3：整周扫描改存逐相位 XY 帧 raw/coarse_XX.npz，并记录指令相位与锁相/DAQ 快照。
RAW_FORMAT_VERSION = 3
SUPPORTED_RAW_FORMAT_VERSIONS = (1, 2, 3)
# 射频由 MFLI Out1 提供，不再要求 Y 通道、功放与示波器映射。
REQUIRED_MAPPING_KEYS = (SOURCE_KEY, X_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY,
                         MAIN_FIELD_KEY, TEC_KEY)

PHASE_RESPONSE_FILE = "raw/phase_response.csv"
PHASE_RESPONSE_PLOT = "results/phase_response.png"
PHASOR_PLOT = "results/phase_response_phasor.png"
SUMMARY_FILE = "results/phase_response_summary.json"
POINT_FILE_PREFIX = "raw/phase_"
COARSE_FILE_PREFIX = "raw/coarse_"
COARSE_LABEL_PATTERN = re.compile(r"coarse_(\d+)")
_FRAME_ARRAYS = {"xy": ("time_s", "x_v", "y_v")}
_MAX_POINTS = 5_000_000
# 1/2 是历史参数快照；3 起读出改到 Demod 4 并去掉 DEMOD0_PHASE_DEG。
_KNOWN_SCHEMA_VERSIONS = frozenset({1, 2, RfPhaseResponseParams.schema_version})


def point_file(phase_deg: float, repeat: int) -> str:
    """历史逐点序列文件名；格式 1/2 的运行目录里仍按此命名保存。"""
    return f"{POINT_FILE_PREFIX}{phase_deg:g}_{repeat}.csv"


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None


def parameters(config) -> RfPhaseResponseParams:
    version = config.get("schema_version", 1)
    if version not in _KNOWN_SCHEMA_VERSIONS:
        raise ValueError("不支持该参数版本")
    values = config["parameters"]
    if not isinstance(values, dict):
        raise ValueError("运行参数快照格式无效")
    return RfPhaseResponseParams.from_external(values, schema_version=version)


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
                phase_response={}, phase_response_acquisition={},
                temperature_gating={"scope": "each_atomic_record", "off_voltage_v": 0.0,
                                    "on_voltage_v": 5.0, "system_settle_s": p.system_settle_s,
                                    "on_lag_s": p.temp_switch_on_lag_s,
                                    "reason": "每点独立进出温控窗口：关温控后只采不写，恢复温控并等待后再落盘"},
                temperature={"target_temperature_c": p.target_temperature_c if p.set_tec_target else None,
                             "actual_temperature_c": None, "controlled_by_experiment": False},
                predicted={"y_rf_vpp": p.y_rf_vpp, "y_rf_vpk": p.y_carrier_vpk, "y_bias_v": p.y_bias_v,
                           "demod0_idx": p.demod0_idx, "pulse_vpp": p.pulse_vpp,
                           "pulse_period_us": p.pulse_period_us,
                           "estimated_peak_current_ma": p.estimated_peak_current_ma,
                           "z_drive_amplifier_enabled": p.z_drive_amplifier_enabled,
                           "z_drive_source_impedance_ohm": p.z_drive_source_impedance_ohm,
                           "loop_resistance_ohm": p.loop_resistance_ohm,
                           "main_field_current_ma": p.main_field_current_ma,
                           "tail_comp": p.z_tail_comp_enabled,
                           "tail_tau_c_us": p.z_tail_tau_c_us,
                           "phase_angles_deg": p.phase_angles_deg(),
                           "x_bias_v": p.x_bias_v})


def frame_path(run_dir, label):
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", label):
        raise ValueError("原始帧标签无效")
    root = Path(run_dir).resolve()
    path = root / "raw" / (label + ".npz")
    if path.resolve() != path:
        raise ValueError("原始帧路径不能含链接或越界")
    return path


def _validate_frame(label, kind, metadata, arrays):
    if kind not in _FRAME_ARRAYS or set(arrays) != set(_FRAME_ARRAYS[kind]):
        raise ValueError(f"{label} 原始帧数组字段不符合 v3")
    if not isinstance(metadata, dict) or metadata.get("kind") != kind or metadata.get("label") != label:
        raise ValueError(f"{label} 原始帧身份不符")
    rate = metadata.get("rate_sa_s")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"{label} 缺少真实采样率")
    phi = metadata.get("burst_command_deg")
    if type(phi) not in (int, float) or not math.isfinite(phi):
        raise ValueError(f"{label} 缺少写入的射频指令相位")
    if (metadata.get("time_source") != "module_timestamp" or not isinstance(metadata.get("lockin"), dict)
            or not isinstance(metadata.get("daq"), dict)):
        raise ValueError(f"{label} 缺少锁相/DAQ 回读或仪器时间戳")
    time = arrays["time_s"]
    if not 8 <= time.size <= _MAX_POINTS or any(
        values.ndim != 1 or values.shape != time.shape or values.dtype.kind not in "fiu"
        or not np.isfinite(values).all() for values in arrays.values()
    ):
        raise ValueError(f"{label} 原始帧数据长度、维度或数值无效")
    dt = np.diff(time)
    if not np.allclose(dt, 1 / rate, atol=0, rtol=0.003):
        raise ValueError(f"{label} 时间轴不连续或与实际采样率不一致")


def write_frame(run_dir, label, kind, metadata, **arrays):
    path = frame_path(run_dir, label)
    if path.exists():
        raise FileExistsError(f"本轮原始帧已存在，不覆盖 {path.name}")
    meta = dict(metadata, label=label, kind=kind)
    _validate_frame(label, kind, meta, arrays)
    payload = json.dumps(meta, ensure_ascii=False, allow_nan=False)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".frame_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, metadata=np.asarray(payload), **arrays)
        temporary.rename(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "raw/" + path.name


def read_frame(run_dir, label, kind):
    path = frame_path(run_dir, label)
    try:
        with np.load(path, allow_pickle=False) as archive:
            if kind not in _FRAME_ARRAYS or set(archive.files) != {"metadata", *_FRAME_ARRAYS[kind]}:
                raise ValueError(f"{label} 原始帧字段不符合 v3")
            meta = json.loads(archive["metadata"].item())
            arrays = {key: archive[key] for key in _FRAME_ARRAYS[kind]}
    except (OSError, BadZipFile, EOFError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 原始帧无法读取：{exc}") from exc
    _validate_frame(label, kind, meta, arrays)
    return meta, arrays
