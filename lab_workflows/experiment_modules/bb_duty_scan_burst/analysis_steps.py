"""离线输入资格、原拟合数值与报告；不连接仪器，不重写历史 raw。"""
from pathlib import Path
import hashlib
import json
import math
import re
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from . import run_format, fitting
from .stages import ANALYSIS_KEYS


class AnalysisQualityError(ValueError):
    """拟合质量不足；诊断仍保存，后继不得伪装完成。"""


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir):
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def resolve_options(base, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    value = options.get("POST_DARK_TIME_S", base.post_dark_time_s)
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("POST_DARK_TIME_S 必须是有限正秒数")
    return type(base).from_external(base.to_external() | options)


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def fingerprint(run_dir):
    root = Path(run_dir)
    paths = [output_path(root, "experiment_config.yaml"), *sorted(output_path(root, "raw").glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(output_path(root, p.relative_to(root))) for p in paths}


def load_inputs(run_dir, params, *, compute=True):
    config, _ = load_config(run_dir)
    raw = output_path(run_dir, "raw")
    index = output_path(run_dir, "raw/duty_scan_summary.npz")
    duties = None
    if index.is_file():
        with np.load(index, allow_pickle=False) as archive:
            names = archive["files"].tolist()
            duties = np.asarray(archive["duty_cycle"], dtype=float)
            if not isinstance(names, list) or duties.ndim != 1 or len(names) != len(duties) or int(archive["n_shots"]) != len(names):
                raise ValueError("占空比汇总索引长度无效")
    elif config.get("raw_format_version") is None:
        names = [p.name for p in sorted(raw.glob("shot_d*_avg.npz"))]
    else:
        raise ValueError("缺少原始占空比汇总索引")
    if not names or len(set(names)) != len(names):
        raise ValueError("未找到有效平均波形，或索引包含重复文件")
    files, reference = [], None
    for i, name in enumerate(names):
        check_cancelled()
        if not isinstance(name, str) or not re.fullmatch(r"shot_d\d+_avg\.npz", name):
            raise ValueError("索引只能引用本实验的平均波形文件")
        path = output_path(run_dir, "raw/" + name)
        with np.load(path, allow_pickle=False) as archive:
            time_s, signal = np.asarray(archive["time_s"], dtype=float), np.asarray(archive["signal"], dtype=float)
            duty, start, end = (float(archive[key]) for key in ("duty_cycle", "t_burst_start_s", "t_burst_end_s"))
        if time_s.ndim != 1 or signal.ndim != 1 or len(time_s) != len(signal) or len(time_s) < 2:
            raise ValueError(f"{name} 的时间轴/波形长度无效")
        dt = np.diff(time_s)
        if not np.all(np.isfinite(time_s)) or not np.all(np.isfinite(signal)) or np.any(dt <= 0) or not np.allclose(dt, np.median(dt), rtol=1e-4, atol=1e-12):
            raise ValueError(f"{name} 的波形或实际采样网格无效")
        if not all(math.isfinite(v) for v in (duty, start, end)) or not 0 < duty < 1 or end <= start:
            raise ValueError(f"{name} 的占空比或 Burst 时间无效")
        if time_s[0] >= start or time_s[-1] < end + params.post_dark_time_s - 2 * np.median(dt):
            raise ValueError(f"{name} 不包含基线或请求的完整暗衰减窗口")
        if duties is not None and not np.isclose(duties[i], duty, rtol=0, atol=1e-12):
            raise ValueError("索引占空比与平均波形不一致")
        if reference is not None and (time_s.shape != reference.shape or not np.allclose(time_s, reference, rtol=0, atol=1e-9)):
            raise ValueError("不同占空比的实际时间网格不一致，不插值伪造残差热图")
        reference = time_s
        files.append((name, duty))
    return files


def write_inputs(run_dir, files):
    output_path(run_dir, "results/inputs.json").write_text(json.dumps(dict(files=[dict(file=n, duty_cycle=d) for n, d in files]), indent=2), encoding="utf-8")
    return dict(n_points=len(files))


def statistics(results):
    def finite_or_nan(v):
        return v if v is not None and np.isfinite(v) else np.nan
    valid_mask = np.array([np.isfinite(r["R_pump_eff_per_s"]) for r in results])
    duty_pct = np.array([r["duty_cycle"] for r in results], dtype=float) * 100
    R_eff = np.array([r["R_pump_eff_per_s"] for r in results], dtype=float)
    tBB, tD, eBB, eD, r2b, GBB, GD, rms_d = [np.array([finite_or_nan(r[k]) for r in results], dtype=float) for k in
        ("tau_BB_s", "tau_dark_s", "tau_BB_err_s", "tau_dark_err_s", "r2_build", "Gamma_BB_per_s", "Gamma_dark_per_s", "rms_res_decay_V")]
    with np.errstate(divide="ignore", invalid="ignore"):
        # 保留原上游误差规则，不在绘图再次缩短误差棒。
        e_GBB = np.minimum(eBB / tBB**2, np.abs(GBB))
        e_GD = np.minimum(eD / tD**2, np.abs(GD))
        e_R = np.sqrt(e_GBB**2 + e_GD**2)
    finite_rms = rms_d[np.isfinite(rms_d)]
    if finite_rms.size:
        med_rms = float(np.nanmedian(finite_rms))
        mad_rms = float(np.nanmedian(np.abs(finite_rms - med_rms)))
        rms_limit = med_rms + max(6 * mad_rms, 5 * med_rms)
    else:
        rms_limit = np.inf
    suspect = (~valid_mask) | (r2b < 0.7) | (rms_d > rms_limit) | (e_R > np.maximum(np.abs(R_eff), 1.0) * 3)
    hq = ~suspect
    selected = hq if hq.sum() >= 3 else valid_mask
    quality_errors = []
    coeff = np.full(2, np.nan)
    if selected.sum() < 2 or len(np.unique(duty_pct[selected])) < 2:
        quality_errors.append("有效泵浦率线性拟合至少需要两个不同的有限占空比点")
    elif not np.all(np.isfinite(e_R[selected])):
        quality_errors.append("有效点拟合不确定度无效，不能伪造权重")
    else:
        # 与原脚本一致：优先 >=3 个高质量点；不足时使用全部有效点，仍按原误差加权。
        coeff = np.polyfit(duty_pct[selected], R_eff[selected], 1, w=1 / np.maximum(e_R[selected], 1.0))
    return {key: value for key, value in locals().items() if key in (
        "valid_mask", "duty_pct", "R_eff", "tBB", "tD", "eBB", "eD", "r2b", "GBB", "GD", "rms_d",
        "e_GBB", "e_GD", "e_R", "rms_limit", "suspect", "hq", "coeff", "quality_errors")}


def to_builtin(value):
    if isinstance(value, np.ndarray):
        return to_builtin(value.tolist())
    if isinstance(value, np.generic):
        return to_builtin(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    return value


def write_fit_report(run_dir, results, stats):
    report = dict(experiment_id=run_format.EXPERIMENT_ID, experiment_type=run_format.DATA_TYPE, data_dir=str(run_dir),
        method="Hilbert envelope -> build-up env fit (3 params) -> decay full-osc fit (5 params)",
        n_points=len(results), quality_passed=not stats["quality_errors"], quality_errors=stats["quality_errors"],
        suspect_rules=dict(r2_build_min=0.7, rms_decay_limit_V=stats["rms_limit"], max_relative_error_ratio=3.0),
        linear_fit=dict(slope_per_s_per_percent=stats["coeff"][0], intercept_per_s=stats["coeff"][1]), per_point=[])
    for i, result in enumerate(results):
        point = {k: v for k, v in result.items() if not isinstance(v, np.ndarray)}
        point.update(duty_cycle_pct=stats["duty_pct"][i], e_Gamma_BB_per_s=stats["e_GBB"][i],
            e_Gamma_dark_per_s=stats["e_GD"][i], e_R_pump_eff_per_s=stats["e_R"][i], suspect=bool(stats["suspect"][i]))
        reasons = []
        if not stats["valid_mask"][i]: reasons.append("invalid R")
        if stats["r2b"][i] < 0.7: reasons.append(f"R²={stats['r2b'][i]:.2f}")
        if stats["rms_d"][i] > stats["rms_limit"]: reasons.append("decay RMS outlier")
        if stats["e_R"][i] > max(abs(stats["R_eff"][i]), 1) * 3: reasons.append("relative error > 300%")
        point["suspect_reason"] = "; ".join(reasons) or "clean"
        report["per_point"].append(point)
    report = to_builtin(report)
    output_path(run_dir, "results/analysis_pub.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    output_path(run_dir, "results/analysis_pub.yaml").write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dict(n_points=len(results), quality_passed=report["quality_passed"], suspect_points=int(stats["suspect"].sum()),
                linear_fit=report["linear_fit"], quality_errors=report["quality_errors"])


def fit(run_dir, params, files):
    results = []
    for name, duty in files:
        check_cancelled()
        result = fitting.fit_one_shot(output_path(run_dir, "raw/" + name), duty, post_dark_time_s=params.post_dark_time_s)
        result["file"] = name
        results.append(result)
    payload = {f"p{i}_{key}": (np.nan if value is None else value) for i, result in enumerate(results) for key, value in result.items()}
    np.savez(output_path(run_dir, "results/fits.npz"), count=len(results), **payload)
    stats = statistics(results)
    summary = write_fit_report(run_dir, results, stats)
    if stats["quality_errors"]:
        raise AnalysisQualityError("；".join(stats["quality_errors"]))
    return summary


def load_fits(run_dir):
    with np.load(output_path(run_dir, "results/fits.npz"), allow_pickle=False) as archive:
        return [{key.split("_", 1)[1]: (archive[key].item() if archive[key].ndim == 0 else archive[key])
                 for key in archive.files if key.startswith(f"p{i}_")} for i in range(int(archive["count"]))]
