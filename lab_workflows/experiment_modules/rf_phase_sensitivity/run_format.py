"""原始格式 v1：单帧时序、实际时间轴及 JSON 回读快照；参数版本独立。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

import numpy as np
import yaml

from ..z_chain_common.keys import (
    AMPLIFIER_KEY, LOCKIN_KEY, MAIN_FIELD_KEY, SCOPE_KEY,
    SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY,
)
from .models import RfPhaseSensitivityParams

REQUIRED_MAPPING_KEYS = (
    SOURCE_KEY, X_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, LOCKIN_KEY,
    AMPLIFIER_KEY, SCOPE_KEY, MAIN_FIELD_KEY, TEC_KEY,
)

EXPERIMENT_ID = "rf-phase-sensitivity"
DATA_TYPE = "rf_phase_sensitivity"
RAW_FORMAT_VERSION = 1
SUMMARY = "phase_sensitivity_summary.json"
TRANSFER_TABLE = "phase_transfer.csv"
TRANSFER_PLOT = "phase_transfer.png"
NOISE_TABLE = "phase_noise.csv"
NOISE_PLOT = "phase_noise.png"
_ARRAYS = {"xy": ("time_s", "x_v", "y_v"), "scope": ("time_s", "reference_v", "coil_v")}
_MAX_POINTS = 1_000_000


def initial_metadata(p: RfPhaseSensitivityParams):
    return dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, data_type=DATA_TYPE,
                execution_mode="typed_workflow", raw_format_version=RAW_FORMAT_VERSION, completion_status="running",
                data_files=[], actual_rates={}, phase_response={}, calibration={}, transfer=[], noise={},
                temperature_gating=dict(scope="each_atomic_record", off_voltage_v=0., on_voltage_v=5.,
                                        system_settle_s=p.system_settle_s, on_lag_s=p.temp_switch_on_lag_s),
                temperature=dict(target_temperature_c=p.target_temperature_c if p.set_tec_target else None,
                                 actual_temperature_c=None, controlled_by_experiment=False),
                predicted=dict(z_pulse_vpp=p.pulse_vpp, pulse_period_us=p.pulse_period_us,
                               pm_sweep_order_hz=p.sweep_order_hz))


def read_config(path):
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None


def parameters(config):
    if config.get("schema_version") != RfPhaseSensitivityParams.schema_version:
        raise ValueError("不支持该参数版本")
    values = config["parameters"]
    if not isinstance(values, dict) or values.keys() != RfPhaseSensitivityParams.external_names().keys():
        raise ValueError("运行参数快照缺键或包含未知字段")
    return RfPhaseSensitivityParams.from_external(values, schema_version=config["schema_version"])


def recorded_parameters(config):
    return parameters(config).to_external()


def format_block_reason(config):
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


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") not in {"completed", "failed", "cancelled"}:
        return "本轮采集未结束或缺少明确终态，不读取可能仍在写入的目录"
    return ""


def frame_path(run_dir, label):
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", label):
        raise ValueError("原始帧标签无效")
    root = Path(run_dir).resolve()
    path = root / "raw" / (label + ".npz")
    if path.resolve() != path:
        raise ValueError("原始帧路径不能含链接或越界")
    return path


def _validate_frame(label, kind, metadata, arrays):
    if kind not in _ARRAYS or set(arrays) != set(_ARRAYS[kind]):
        raise ValueError(f"{label} 原始帧数组字段不符合 v1")
    if not isinstance(metadata, dict) or metadata.get("kind") != kind or metadata.get("label") != label:
        raise ValueError(f"{label} 原始帧身份不符")
    rate = metadata.get("rate_sa_s")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"{label} 缺少真实采样率")
    if kind == "xy" and (metadata.get("time_source") != "module_timestamp" or not isinstance(metadata.get("lockin"), dict)):
        raise ValueError(f"{label} 缺少锁相回读或仪器时间戳")
    time = arrays["time_s"]
    if not 8 <= time.size <= _MAX_POINTS or any(
        values.ndim != 1 or values.shape != time.shape or values.dtype.kind not in "fiu" or not np.isfinite(values).all()
        for values in arrays.values()
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
            if kind not in _ARRAYS or set(archive.files) != {"metadata", *_ARRAYS[kind]}:
                raise ValueError(f"{label} 原始帧字段不符合 v1")
            meta = json.loads(archive["metadata"].item())
            arrays = {key: archive[key] for key in _ARRAYS[kind]}
    except (OSError, BadZipFile, EOFError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 原始帧无法读取：{exc}") from exc
    _validate_frame(label, kind, meta, arrays)
    return meta, arrays
