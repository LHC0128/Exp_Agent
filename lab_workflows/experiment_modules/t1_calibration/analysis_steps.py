"""纯离线输入资格、历史兼容、内容指纹与科学结果，不连接仪器。"""
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from ..noise_spectrum_xy.state_io import write_state
from . import run_format
from .models import T1CalibrationParams
from .fitting import fit_decay
from .storage import save_npz
from .stages import ANALYSIS_KEYS


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root/name).resolve()
    if path == root or root not in path.parents:
        raise ValueError("输入或产物路径不能越出运行目录")
    return path


def load_config(run_dir):
    config = run_format.load_config(run_dir)
    return config, run_format.parameters(config, run_dir)


def resolve_options(base, options):
    if unknown := options.keys()-set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数："+"、".join(sorted(unknown)))
    p = T1CalibrationParams.from_external(base.to_external() | options)
    if not 0 < p.t1_min_s < p.t1_max_s:
        raise ValueError("T1 拟合上下限必须为递增的正秒数")
    return p


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def fingerprint(run_dir):
    load_config(run_dir)
    paths = [output_path(run_dir, "raw/"+name) for name in run_format.RAW_FILES]
    paths += sorted(output_path(run_dir, "raw").glob("point_p*.npz"))
    return dict(analysis_version="t1-v1", config=signature(output_path(run_dir, "experiment_config.yaml")),
        raw={p.name: signature(output_path(run_dir, "raw/"+p.name)) for p in paths if p.exists()})


def _wave(t, voltage):
    t, voltage = np.asarray(t, dtype=float), np.asarray(voltage, dtype=float)
    if (t.ndim != 1 or t.size < 10 or voltage.shape != t.shape or not np.isfinite(t).all()
            or not np.isfinite(voltage).all() or not np.all(np.diff(t) > 0) or not np.any(t >= 0)):
        raise ValueError("衰减波形或时间轴无效，不插值或伪造数据")
    return t, voltage


def load_inputs(run_dir, params=None, *, compute=True):
    _, base = load_config(run_dir)
    raw = output_path(run_dir, "raw")
    points = []
    paths = sorted(raw.glob("point_p*.npz"), key=lambda path: int(path.stem.removeprefix("point_p")))
    if paths:
        for original in paths:
            check_cancelled()
            path = output_path(run_dir, "raw/"+original.name)
            with np.load(path, allow_pickle=False) as data:
                t, y = _wave(data["t_axis"], data["vdiff_avg"])
                points.append(dict(probe_power=float(data["probe_power"]), t_axis=t, voltage=y))
        scan = base.do_power_scan
    elif (raw/"power_scan_results.npz").is_file():
        path = output_path(run_dir, "raw/power_scan_results.npz")
        with np.load(path, allow_pickle=False) as data:
            powers, t1, errors = (np.asarray(data[k], dtype=float) for k in ("probe_power", "T1", "T1_err"))
        if powers.ndim != 1 or not powers.size or t1.shape != powers.shape or errors.shape != powers.shape:
            raise ValueError("旧扫描结果的功率、T1 和误差维度不一致")
        points = [dict(probe_power=float(p), T1=float(v), T1_err=float(e), t_axis=None, voltage=None) for p, v, e in zip(powers, t1, errors)]
        wavepath = output_path(run_dir, "raw/power_scan_waveforms.npz")
        if wavepath.is_file():
            with np.load(wavepath, allow_pickle=False) as data:
                t, stack, wp = data["t_axis"], data["vdiff_stack"], data["probe_power"]
                if stack.shape != (len(points), len(t)) or wp.shape != powers.shape or not np.array_equal(wp, powers):
                    raise ValueError("旧波形堆叠与功率索引不一致")
                for point, voltage in zip(points, stack):
                    point["t_axis"], point["voltage"] = _wave(t, voltage)
        scan = True
    else:
        path = output_path(run_dir, "raw/t1_measurement.npz")
        with np.load(path, allow_pickle=False) as data:
            t, y = _wave(data["t_axis"], data["vdiff_avg"])
            points = [dict(probe_power=float(data["probe_power"]), t_axis=t, voltage=y)]
        scan = False
    if not all(math.isfinite(p["probe_power"]) for p in points):
        raise ValueError("Probe 控制电压必须有限")
    return dict(points=points, scan=scan)


def write_statistics(run_dir, data):
    write_state(output_path(run_dir, "results/input_manifest.json"),
        dict(fingerprint=fingerprint(run_dir), n_points=len(data["points"]), scan=data["scan"],
             has_waveforms=all(p["t_axis"] is not None for p in data["points"])))
    return {"n_points": len(data["points"])}


def fit(run_dir, params):
    data = load_inputs(run_dir)
    points = data["points"]
    for point in points:
        check_cancelled()
        if point["t_axis"] is not None:
            point.update(fit_decay(point["t_axis"], point["voltage"], params.t1_bounds))
        else:
            valid = math.isfinite(point["T1"]) and point["T1"] > 0 and math.isfinite(point["T1_err"]) and point["T1_err"] >= 0
            point.update(popt=np.full(3, np.nan), pcov=np.zeros((3, 3)), reason="" if valid else "历史 T1 或误差无效")
    arrays = {key: np.array([p[key] for p in points]) for key in ("probe_power", "T1", "T1_err", "popt", "pcov", "reason")}
    valid = np.isfinite(arrays["T1"]) & (arrays["T1"] > 0) & np.isfinite(arrays["T1_err"]) & (arrays["T1_err"] >= 0) & (arrays["reason"] == "")
    arrays["valid_mask"] = valid
    powers, t1, errors = (arrays[key][valid] for key in ("probe_power", "T1", "T1_err"))
    inv, inv_err = 1/t1, errors/t1**2
    linear = data["scan"] and len(powers) >= 2 and len(np.unique(powers)) >= 2
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, n_points=len(points), valid_points=int(valid.sum()),
        mode="scan" if data["scan"] else "single", scientific_quality="usable" if valid.any() else "diagnostic_only",
        has_waveforms=all(p["t_axis"] is not None for p in points), linear_fit_available=bool(linear),
        fit_bounds_s=list(params.t1_bounds), probe_power=powers.tolist(), inv_T1=inv.tolist(), inv_T1_err=inv_err.tolist(),
        rejection_reasons=[str(p["reason"]) for p in points])
    if linear:
        slope, intercept = np.polyfit(powers, inv, 1, w=1/np.maximum(inv_err, 1e-6))
        summary.update(T1_0_inv=float(intercept), T1_0_ms=float(1e3/intercept) if intercept > 0 else None,
                       alpha=float(slope), extrapolation_usable=bool(intercept > 0))
        if intercept <= 0:
            summary["extrapolation_reason"] = "截距非正，不能报告本征 T1"
    elif data["scan"]:
        summary["linear_fit_reason"] = "至少需要两个不同 Probe 电压的有效 T1 点"
    if not summary["has_waveforms"]:
        summary["waveform_note"] = "旧扫描缺少波形，沿用已保存 T1；拟合上下限未用于重新拟合"
    if not data["scan"]:
        summary.update(T1=float(t1[0]) if len(t1) else None, T1_err=float(errors[0]) if len(errors) else None)
    save_npz(output_path(run_dir, "results/fit_points.npz"), **arrays)
    write_state(output_path(run_dir, "results/analysis.json"), summary)
    output_path(run_dir, "results/analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return summary


def write_report(run_dir):
    return json.loads(output_path(run_dir, "results/analysis.json").read_text(encoding="utf-8"))
