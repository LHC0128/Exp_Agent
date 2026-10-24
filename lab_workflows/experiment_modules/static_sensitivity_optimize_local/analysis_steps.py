"""批次纯离线计算：冻结的单点结论、基准漂移、排名与原建议规则。"""
import csv
import math
from pathlib import Path

import numpy as np
import yaml

from ..noise_spectrum_xy.state_io import read_state, write_state
from ..static_sensitivity.run_format import format_block_reason as point_block_reason
from . import preparation, run_format
from .models import StaticSensitivityOptimizeLocalParams

FIELDNAMES = ("iteration", "parameter", "value", "is_baseline", "fit_is_valid", "success",
    "sens_flat_fT_per_sqrt_Hz", "fit_r2", "gamma_nT", "slope_V_per_fT", "fit_rejection_reasons", "error", "run_id", "run_dir")


def plain(value):
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def load_inputs(run_dir):
    run_dir = Path(run_dir)
    config = run_format.read_config(run_dir)
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    if config["raw_format_version"] == 0:
        payload = yaml.safe_load((run_dir / run_format.LEGACY_SUMMARY).read_text(encoding="utf-8"))
        rows = payload["results"]
        baseline, sequence = payload["baseline_params"], payload["optimize_sequence"]
    else:
        payload = read_state(run_dir / run_format.MANIFEST)
        if not isinstance(payload, dict) or payload.get("experiment_id") != run_format.EXPERIMENT_ID or type(payload.get("raw_format_version")) is not int or payload.get("raw_format_version") != 1:
            raise ValueError("批次原始清单身份或格式无效")
        params = StaticSensitivityOptimizeLocalParams.from_external(config["parameters"], strict=False)
        rows = payload.get("records")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("批次缺少候选记录")
        expected = preparation.planned_records(params)
        if len(rows) != len(expected) or any(any(row.get(key) != plan[key] for key in
                ("iteration", "parameter", "value", "is_baseline")) for row, plan in zip(rows, expected)):
            raise ValueError("候选清单与冻结参数不一致")
        baseline, sequence = params.baseline_params, params.sequence()
        for row in rows:
            name = row.get("run_id")
            if name is None:
                continue
            if not isinstance(name, str) or not name or any(char in name for char in '/\\:') or name in (".", ".."):
                raise ValueError("子运行引用必须是单个目录名")
            path = (run_dir.parent / name).resolve()
            if path.parent != run_dir.parent.resolve() or not path.is_dir():
                raise ValueError("子运行不存在或路径越界")
            child = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
            if child.get("experiment_id") != "static-sensitivity" or point_block_reason(child):
                raise ValueError("子运行不是已验证的 DC 静磁场格式")
            expected = params.point_parameters(row["parameter"], row["value"],
                run_tag=f"{run_dir.name}_{row['iteration']:02d}_{row['parameter']}").to_external()
            if any(child.get("parameters", {}).get(key) != value for key, value in expected.items()):
                raise ValueError("子运行参数与冻结候选不匹配")
            if row["status"] in ("completed", "quality_failed") and child.get("completion_status") != "completed":
                raise ValueError("成功候选的子运行尚未安全封存")
    if not isinstance(rows, list) or not isinstance(baseline, dict) or not isinstance(sequence, list):
        raise ValueError("局部优化汇总结构无效")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or not {"iteration", "parameter", "value", "is_baseline", "fit_is_valid"}.issubset(row):
            raise ValueError("候选结果缺少已验证的科学字段")
        if type(row["fit_is_valid"]) is not bool or type(row["is_baseline"]) is not bool:
            raise ValueError("拟合有效和基准标记必须为布尔值")
        result = plain(dict(row))
        for key in ("sens_flat_fT_per_sqrt_Hz", "fit_r2", "gamma_nT", "slope_V_per_fT"):
            result[key] = finite(result.get(key))
        normalized.append(result)
    return dict(rows=normalized, baseline_params=baseline, optimize_sequence=sequence,
        acquisition_method=config["acquisition_method"], source="frozen_point_analysis" if config["raw_format_version"] == 1 else "legacy_existing_summary")


def decide_best_result(results):
    valid = [row for row in results if row["fit_is_valid"] and finite(row.get("sens_flat_fT_per_sqrt_Hz")) is not None]
    baseline = [row["sens_flat_fT_per_sqrt_Hz"] for row in valid if row["is_baseline"]]
    decision = dict(baseline_valid_count=len(baseline), baseline_median=None, baseline_drift_ratio=None,
        baseline_drift_warning=False, best_run=None, best_improvement_ratio=None, recommendation="无有效结果，维持当前基准")
    if baseline:
        median = float(np.median(baseline))
        decision["baseline_median"] = median
        if len(baseline) >= 2 and median > 0:
            drift = float((max(baseline) - min(baseline)) / median)
            decision.update(baseline_drift_ratio=drift, baseline_drift_warning=drift > .30)
    if valid:
        best = min(valid, key=lambda row: row["sens_flat_fT_per_sqrt_Hz"])
        decision["best_run"] = best
        if decision["baseline_median"] is not None and decision["baseline_median"] > 0:
            improvement = (decision["baseline_median"] - best["sens_flat_fT_per_sqrt_Hz"]) / decision["baseline_median"]
            decision.update(best_improvement_ratio=float(improvement), recommendation="采用最佳扫描点" if improvement >= .10 else "维持当前基准")
        else:
            decision["recommendation"] = "采用最佳有效点，但缺少有效基准重复点"
    return decision


def export_report(run_dir, inputs, decision):
    results = Path(run_dir) / "results"
    rows = inputs["rows"]
    with (results / "optimization_local_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    payload = dict(baseline_params=inputs["baseline_params"], optimize_sequence=inputs["optimize_sequence"],
        results=rows, decision=decision, source=inputs["source"], acquisition_method=inputs["acquisition_method"])
    (results / "optimization_local_summary.yaml").write_text(yaml.safe_dump(plain(payload), allow_unicode=True, sort_keys=False), encoding="utf-8")
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, status="completed", success=decision["best_run"] is not None,
        point_count=len(rows), valid_point_count=sum(bool(row["fit_is_valid"]) and row["sens_flat_fT_per_sqrt_Hz"] is not None for row in rows),
        source=inputs["source"], acquisition_method=inputs["acquisition_method"], **decision)
    write_state(results / "analysis.json", summary)
    (results / "analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return summary
