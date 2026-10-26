"""父清单、标准子摘要资格与原趋势表格；不重算子实验科学结果。"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import yaml

from lab_workflows.workflow_state import write_state
from ..mx_y_rf_sensitivity.run_format import RAW_FORMAT_VERSION as CHILD_RAW_FORMAT_VERSION
from . import run_format
from .stages import ANALYSIS

FIELDNAMES = (
    "cycle_index", "scheduled_offset_s", "actual_elapsed_s", "actual_start_utc", "start_lag_s",
    "child_run_dir", "status", "valid", "sensitivity_flat_median_ft_per_sqrt_hz",
    "zero_point_sensitivity_flat_median_ft_per_sqrt_hz", "linewidth_hwhm_hz", "linewidth_kind",
    "primary_slope_v_per_vpp", "zero_point_slope_v_per_vpp", "response_fit_r_squared",
    "excluded_point_count", "flat_band_low_hz", "flat_band_high_hz", "warnings", "error",
)
TABLE_FILES = ("drift_summary.csv", "drift_summary.json")


def plain(value):
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


class AnalysisQualityError(ValueError):
    """输入、身份、格式或摘要数值不符合资格。"""


def contained(path, base):
    path, base = Path(path).resolve(), Path(base).resolve()
    if not path.is_relative_to(base):
        raise AnalysisQualityError(f"路径超出允许目录：{path}")
    return path


def project_directory(run_dir, project_root=None):
    run_dir = Path(run_dir).resolve()
    root = Path(project_root).resolve() if project_root is not None else run_dir.parents[2]
    if run_dir.parent != contained(root / "data" / run_format.DATA_TYPE, root):
        raise AnalysisQualityError("父运行必须位于项目 data/Mx_Y_RF_Sensitivity_Drift 下")
    for name in ("experiment_config.yaml", run_format.MANIFEST, "results", "analysis_state.json"):
        contained(run_dir / name, run_dir)
    outputs = ("results", "analysis_state.json", "experiment_config.yaml",
        *(f"results/{name}" for stage in ANALYSIS for name in stage.artifacts))
    for name in outputs:
        for path in (run_dir / name, run_dir / (name + ".tmp")):
            if path.resolve() != path:
                raise AnalysisQualityError("分析输出不能通过链接指向其他文件或目录")
    return root


def read_mapping(path):
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AnalysisQualityError(f"无法读取 {Path(path).name}：{exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisQualityError(f"{Path(path).name} 必须是对象")
    return payload


def file_signature(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _config_signature(config):
    clean = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _child_path(record, run_dir, root):
    reference = record.get("child_run_dir")
    if reference is None:
        return None
    if not isinstance(reference, str) or not reference.strip():
        raise AnalysisQualityError("子运行引用必须是非空路径或 null")
    path = Path(reference)
    child_base = contained(root / "data" / run_format.CHILD_DATA_TYPE, root)
    child = contained(path if path.is_absolute() else run_dir / path, child_base)
    if child.parent != child_base:
        raise AnalysisQualityError("子运行引用必须指向标准单层运行目录")
    for name in ("experiment_config.yaml", "results/analysis.json"):
        contained(child / name, child)
    return child


def input_fingerprint(run_dir, root, version):
    """坏输入仍有指纹，实际拒绝诊断由 B01 保存；不跟随逃逸引用。"""
    config = read_mapping(run_dir / "experiment_config.yaml")
    fingerprint = dict(analysis_version=version, config_sha256=_config_signature(config),
        manifest_sha256=file_signature(run_dir / run_format.MANIFEST), child_files={})
    try:
        manifest = read_mapping(run_dir / run_format.MANIFEST)
        for record in manifest.get("records", []):
            child = _child_path(record, run_dir, root)
            if child is not None:
                fingerprint["child_files"][str(child)] = {
                    name: file_signature(child / name) for name in ("experiment_config.yaml", "results/analysis.json")}
    except (AnalysisQualityError, AttributeError, TypeError) as exc:
        fingerprint["input_error"] = str(exc)
    return fingerprint


def _number(value, label, *, required=False):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AnalysisQualityError(f"{label} 必须为有限数值")
    return value


def _summary(child, record):
    path = child / "results/analysis.json" if child else None
    if path is None or not path.is_file():
        if record["status"] == "completed" or record.get("analysis"):
            raise AnalysisQualityError(f"轮次 {record['cycle_index']} 缺少标准子 analysis.json")
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisQualityError(f"轮次 {record['cycle_index']} 的子摘要损坏：{exc}") from exc
    if not isinstance(result, dict) or result.get("experiment_id") != run_format.CHILD_EXPERIMENT_ID:
        raise AnalysisQualityError(f"轮次 {record['cycle_index']} 的子摘要身份错误")
    if not isinstance(result.get("run_dir"), str) or Path(result["run_dir"]).name != child.name:
        raise AnalysisQualityError(f"轮次 {record['cycle_index']} 的子摘要运行目录不匹配")
    if record.get("analysis") and record["analysis"] != result:
        raise AnalysisQualityError("清单内嵌摘要与标准子 analysis.json 不一致")
    if type(result.get("success")) is not bool:
        raise AnalysisQualityError("子摘要 success 必须是布尔值")
    for key in ("flat_detection", "linewidth", "response_fit", "zero_point_method"):
        if result.get(key) is not None and not isinstance(result[key], dict):
            raise AnalysisQualityError(f"子摘要 {key} 必须是对象")
    if result.get("linewidth_mode") not in (None, "amplitude_equivalent"):
        raise AnalysisQualityError("长飘只汇总幅度等效线宽")
    return result


def _row(record, child, result):
    flat, linewidth = result.get("flat_detection") or {}, result.get("linewidth") or {}
    fit, zero = result.get("response_fit") or {}, result.get("zero_point_method") or {}
    if flat.get("success") is not None and type(flat["success"]) is not bool:
        raise AnalysisQualityError("子摘要 flat_detection.success 必须是布尔值")
    valid = bool(result and result["success"] and flat.get("success", False) and linewidth.get("hwhm_hz") is not None)
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise AnalysisQualityError("子摘要 warnings 必须为文本列表")
    band = result.get("flat_band_hz") or [None, None]
    if not isinstance(band, list) or len(band) != 2:
        raise AnalysisQualityError("子摘要 flat_band_hz 必须包含两个频率")
    row = dict(cycle_index=record["cycle_index"], scheduled_offset_s=record["scheduled_offset_s"],
        actual_elapsed_s=float(record["scheduled_offset_s"]) + float(record.get("start_lag_s") or 0),
        actual_start_utc=record.get("actual_start_utc"), start_lag_s=record.get("start_lag_s"),
        child_run_dir=str(child) if child else None, status=record["status"], valid=valid,
        sensitivity_flat_median_ft_per_sqrt_hz=result.get("flat_median_ft_per_sqrt_hz"),
        zero_point_sensitivity_flat_median_ft_per_sqrt_hz=zero.get("flat_median_ft_per_sqrt_hz"),
        linewidth_hwhm_hz=linewidth.get("hwhm_hz"), linewidth_kind=linewidth.get("linewidth_kind"),
        primary_slope_v_per_vpp=result.get("primary_slope_v_per_vpp"), zero_point_slope_v_per_vpp=zero.get("slope_v_per_vpp"),
        response_fit_r_squared=fit.get("r_squared"), excluded_point_count=fit.get("excluded_point_count"),
        flat_band_low_hz=band[0], flat_band_high_hz=band[1], warnings=" | ".join(warnings), error=record.get("error"))
    required = {"sensitivity_flat_median_ft_per_sqrt_hz", "linewidth_hwhm_hz", "response_fit_r_squared",
        "primary_slope_v_per_vpp", "flat_band_low_hz", "flat_band_high_hz"} if valid else set()
    for key in FIELDNAMES[8:]:
        if key not in ("linewidth_kind", "warnings", "error"):
            # 旧无效轮用 NaN 表示缺值；保留 CSV，JSON 转 null，不能据此改变有效掩码。
            if not valid and isinstance(row[key], float) and math.isnan(row[key]):
                continue
            _number(row[key], f"轮次 {record['cycle_index']} 的 {key}", required=key in required)
    if valid and (row["sensitivity_flat_median_ft_per_sqrt_hz"] < 0 or row["linewidth_hwhm_hz"] <= 0
            or row["primary_slope_v_per_vpp"] <= 0 or not 0 <= row["flat_band_low_hz"] < row["flat_band_high_hz"]):
        raise AnalysisQualityError("有效轮次的灵敏度、幅度等效线宽、斜率或平坦频段数值不合法")
    reason = "" if valid else (record.get("error") or flat.get("reason") or
        ("尚无子分析摘要" if not result else "子分析未成功" if not result["success"] else
         "未找到平坦频段" if not flat.get("success") else "缺少幅度等效线宽"))
    return row, reason


def load_inputs(run_dir, *, project_root=None, allow_running=False):
    run_dir = Path(run_dir).resolve()
    root = project_directory(run_dir, project_root)
    config = read_mapping(run_dir / "experiment_config.yaml")
    manifest = read_mapping(run_dir / run_format.MANIFEST)
    if reason := run_format.analysis_block_reason(config, manifest, allow_running=allow_running):
        raise AnalysisQualityError(reason)
    rows, invalid, seen = [], [], set()
    for record in manifest["records"]:
        child = _child_path(record, run_dir, root)
        lag = _number(record.get("start_lag_s"), "实际开始延迟")
        if lag is not None and lag < 0:
            raise AnalysisQualityError("实际开始延迟不能为负")
        if record.get("actual_start_utc") is not None and not isinstance(record["actual_start_utc"], str):
            raise AnalysisQualityError("实际开始时间必须为 ISO 时间文本或 null")
        if child is not None:
            if child in seen:
                raise AnalysisQualityError("多个轮次不能引用同一子运行")
            seen.add(child)
            child_config = read_mapping(child / "experiment_config.yaml")
            if (child_config.get("experiment_id") != run_format.CHILD_EXPERIMENT_ID
                    or child_config.get("data_type") != run_format.CHILD_DATA_TYPE
                    or not isinstance(child_config.get("parameters"), dict)
                    or child_config.get("parent_experiment_id") != run_format.EXPERIMENT_ID
                    or type(child_config.get("drift_cycle_index")) is not int
                    or child_config["drift_cycle_index"] != record["cycle_index"]):
                raise AnalysisQualityError(f"轮次 {record['cycle_index']} 的标准子运行身份或轮次不匹配")
            version = child_config.get("raw_format_version")
            if version is not None and (type(version) is not int or version != CHILD_RAW_FORMAT_VERSION):
                raise AnalysisQualityError("不支持该标准子运行原始格式")
            if (not isinstance(child_config.get("parent_run_dir"), str)
                    or Path(child_config["parent_run_dir"]).name != run_dir.name):
                raise AnalysisQualityError("标准子运行指向其他父运行")
            if not allow_running and child_config.get("completion_status") in ("running", "acquired"):
                raise AnalysisQualityError("引用的标准子运行仍在写入")
        elif record["status"] not in ("pending", "cancelled", "acquisition_failed"):
            raise AnalysisQualityError("已执行轮次缺少标准子运行引用")
        row, reason = _row(record, child, _summary(child, record))
        rows.append(row)
        if not row["valid"]:
            invalid.append(dict(cycle_index=record["cycle_index"], status=record["status"], reason=str(reason)))
    diagnostics = dict(cycle_count=len(rows), valid_cycle_count=sum(row["valid"] for row in rows), invalid_cycles=invalid)
    return dict(config=config, rows=rows, **diagnostics)


def write_table(results_dir, rows):
    path = results_dir / TABLE_FILES[0]
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    write_state(results_dir / TABLE_FILES[1], plain(rows))


def write_inputs(results_dir, inputs, fingerprint):
    write_state(results_dir / "analysis_inputs.json", dict(fingerprint=fingerprint,
        **{key: inputs[key] for key in ("cycle_count", "valid_cycle_count", "invalid_cycles")}))


def read_table(results_dir):
    return json.loads((results_dir / TABLE_FILES[1]).read_text(encoding="utf-8"))


def read_summary(run_dir, *, project_root=None):
    """只读旧父结果，允许展示在线缓存；非有限缺值转为标准 JSON null。"""
    run_dir = Path(run_dir).resolve()
    project_directory(run_dir, project_root)
    config = read_mapping(run_dir / "experiment_config.yaml")
    manifest = read_mapping(run_dir / run_format.MANIFEST)
    if reason := run_format.analysis_block_reason(config, manifest, allow_running=True):
        raise AnalysisQualityError(reason)
    path = contained(run_dir / "results/analysis.json", run_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisQualityError(f"父分析摘要损坏：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("experiment_id") not in (None, run_format.EXPERIMENT_ID):
        raise AnalysisQualityError("父分析摘要身份错误")
    if not isinstance(payload.get("run_dir"), str) or Path(payload["run_dir"]).name != run_dir.name:
        raise AnalysisQualityError("父分析摘要运行目录不匹配")
    return plain(payload)
