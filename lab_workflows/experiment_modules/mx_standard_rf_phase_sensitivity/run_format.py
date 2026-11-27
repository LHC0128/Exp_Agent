"""原始格式 v1：单帧 XY 时序、模块时间轴与 JSON 回读快照；参数版本独立。

采集、离线分析与历史读取共用本模块的实验身份、帧字段与资格判断。参数版本用
schema_version，原始格式用 raw_format_version；参数升级不改变原始文件约定。
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

from .models import MxStandardRfPhaseSensitivityParams

from ..z_chain_common.keys import (
    LOCKIN_KEY, MAIN_FIELD_KEY, SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY,
)

EXPERIMENT_ID = "mx-standard-rf-phase-sensitivity"
DATA_TYPE = "mx_standard_rf_phase_sensitivity"
RAW_FORMAT_VERSION = 1

SUMMARY = "summary.json"
DIAGRAM_PLOT = "phase_diagram.png"
NOISE_PLOT = "noise_paths.png"

# 本次实验用到的物理量键；Temp_Switch / Time_sequence_2 / Z_magnetic_field 只读或关断，但必须有映射。
REQUIRED_MAPPING_KEYS: tuple[str, ...] = (
    SOURCE_KEY, X_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY, MAIN_FIELD_KEY, TEC_KEY,
)

_ARRAYS = {"xy": ("time_s", "x_v", "y_v")}
_MAX_POINTS = 5_000_000
_LABEL_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}")


def require_raw_format(config: dict) -> None:
    """拒绝不属于本实验或格式未知的运行配置。"""
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("运行配置不属于本实验")
    version = config.get("raw_format_version")
    if type(version) is not int or version != RAW_FORMAT_VERSION:
        raise ValueError("原始数据格式未知或不受支持；历史格式须显式识别并验证")


def parameters(config: dict):
    """从运行配置读参数快照；参数版本或键不符直接报错，不当成当前格式。"""
    if not isinstance(config, dict):
        raise ValueError("运行配置不是有效 YAML 映射")
    version = config.get("schema_version")
    if version != MxStandardRfPhaseSensitivityParams.schema_version:
        raise ValueError("不支持该参数版本")
    values = config.get("parameters")
    if not isinstance(values, dict):
        raise ValueError("运行参数快照格式无效")
    return MxStandardRfPhaseSensitivityParams.from_external(values, schema_version=version)


def recorded_parameters(config: dict) -> dict:
    """历史回填用的规范化参数快照。"""
    return parameters(config).to_external()


def format_block_reason(config) -> str:
    """原始资格判定：身份、格式版本与参数快照都通过才读取该目录。"""
    if not isinstance(config, dict):
        return "运行配置不是有效 YAML 映射"
    if (config.get("experiment_id"), config.get("data_type")) != (EXPERIMENT_ID, DATA_TYPE):
        return "运行目录属于其他实验"
    if type(config.get("raw_format_version")) is not int or config["raw_format_version"] != RAW_FORMAT_VERSION:
        return "不支持该原始数据格式版本"
    try:
        if errors := parameters(config).validate_model():
            raise ValueError("；".join(errors))
    except (KeyError, TypeError, ValueError) as exc:
        return f"历史参数快照无效：{exc}"
    return ""


def analysis_block_reason(config) -> str:
    """分析资格：格式通过且本轮已有明确终态，避免读仍在写入的目录。"""
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") not in {"completed", "failed", "cancelled"}:
        return "本轮采集未结束或缺少明确终态，不读取可能仍在写入的目录"
    return ""


def initial_metadata(params) -> dict:
    """新运行目录的身份与格式快照；分步模式由 StagedWorkflow 写入。"""
    return dict(
        experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, raw_format_version=RAW_FORMAT_VERSION,
        execution_mode="typed_workflow", completion_status="running", data_files=[], actual_rates={},
        temperature_gating=dict(scope="each_atomic_record", off_voltage_v=0.0, on_voltage_v=5.0,
                                system_settle_s=params.system_settle_s,
                                on_lag_s=params.temp_switch_on_lag_s),
        temperature=dict(target_temperature_c=params.target_temperature_c if params.set_tec_target else None,
                         actual_temperature_c=None, controlled_by_experiment=False),
        calibration_formula=dict(k_hz_per_ma=params.cal_k_hz_per_ma, b_hz=params.cal_b_hz,
                                 main_field_current_ma=params.main_field_current_ma),
        carrier=dict(frequency_hz=params.y_rf_freq_hz, vpp=params.y_rf_vpp,
                     pm_sidebands_enabled=False),
        z_pulse_chain={}, settle={}, phase_diagram={}, fine_scan={}, working_point={}, noise={})


def read_config(path) -> dict | None:
    """读取运行配置；损坏时返回 None 交给调用方判定。"""
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None


def frame_path(run_dir, label):
    """raw/<label>.npz 的受校验路径；标签只允许字母数字、下划线与连字符。"""
    if not isinstance(label, str) or not _LABEL_PATTERN.fullmatch(label):
        raise ValueError("原始帧标签无效")
    return Path(run_dir) / "raw" / (label + ".npz")


def _validate_frame(label, kind, metadata, arrays):
    """v1 帧资格：字段、身份、真实采样率、锁相回读与连续时间轴。"""
    if kind not in _ARRAYS or set(arrays) != set(_ARRAYS[kind]):
        raise ValueError(f"{label} 原始帧数组字段不符合 v1")
    if not isinstance(metadata, dict) or metadata.get("kind") != kind or metadata.get("label") != label:
        raise ValueError(f"{label} 原始帧身份不符")
    rate = metadata.get("rate_sa_s")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"{label} 缺少真实采样率")
    if kind == "xy" and (metadata.get("time_source") != "module_timestamp"
                         or not isinstance(metadata.get("lockin"), dict)):
        raise ValueError(f"{label} 缺少锁相回读或仪器时间戳")
    time = arrays["time_s"]
    if not 8 <= time.size <= _MAX_POINTS or any(
        values.ndim != 1 or values.shape != time.shape or values.dtype.kind not in "fiu"
        or not np.isfinite(values).all()
        for values in arrays.values()
    ):
        raise ValueError(f"{label} 原始帧数据长度、维度或数值无效")
    dt = np.diff(time)
    if not np.allclose(dt, 1 / rate, atol=0, rtol=0.003):
        raise ValueError(f"{label} 时间轴不连续或与实际采样率不一致")


def write_frame(run_dir, label, kind, metadata, **arrays):
    """先校验再原子写入；同一轮不覆盖已存在的帧。"""
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
    """读取并按 v1 校验一帧原始数据。"""
    path = frame_path(run_dir, label)
    try:
        with np.load(path, allow_pickle=False) as archive:
            if kind not in _ARRAYS or set(archive.files) != {"metadata", *_ARRAYS[kind]}:
                raise ValueError(f"{label} 原始帧字段不符合 v1")
            meta = json.loads(archive["metadata"].item())
            arrays = {key: archive[key] for key in _ARRAYS[kind]}
    except (OSError, BadZipFile, EOFError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 原始帧无法读取：{exc}") from exc
    _validate_frame(label, kind, meta, arrays)
    return meta, arrays
