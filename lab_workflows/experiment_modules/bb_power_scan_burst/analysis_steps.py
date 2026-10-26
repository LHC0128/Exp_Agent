"""离线资格、目录隔离、内容指纹与原科学结果存取。"""
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from lab_workflows.workflow_state import write_state
from . import run_format
from .models import BBPowerScanBurstParams
from .fitting import fit_one_shot
from .acquisition import save_npz
from .stages import ANALYSIS_KEYS


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root/name).resolve()
    if path == root or root not in path.parents:
        raise ValueError("输入或产物路径不能越出运行目录")
    return path


def load_config(run_dir):
    config = yaml.safe_load(output_path(run_dir, "experiment_config.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("实验配置必须是映射")
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config, run_dir)


def resolve_options(base_params, options):
    if unknown := options.keys()-set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数："+"、".join(sorted(unknown)))
    params = BBPowerScanBurstParams.from_external(base_params.to_external() | options)
    # 历史数据分析不重新校验当前硬件和安全配置，也不修改采集参数快照。
    if not math.isfinite(params.post_dark_time_s) or params.post_dark_time_s <= 0:
        raise ValueError("POST_DARK_TIME_S 必须为有限正秒数")
    return params


def signature(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def fingerprint(run_dir):
    load_config(run_dir)
    return dict(analysis_version="bb-power-burst-v1", config=signature(output_path(run_dir, "experiment_config.yaml")),
        raw={path.name: signature(output_path(run_dir, "raw/"+path.name))
             for path in sorted(output_path(run_dir, "raw").glob("shot_p*_avg.npz"))})


def load_inputs(run_dir, params=None, *, compute=True):
    config, base_params = load_config(run_dir)
    params = params or base_params
    paths = sorted(output_path(run_dir, "raw").glob("shot_p*_avg.npz"))
    if not paths:
        raise ValueError("未找到 shot_p*_avg.npz；只有逐帧或抢救文件不能冒充完整平均")
    reference = None
    for original in paths:
        check_cancelled()
        path = output_path(run_dir, "raw/"+original.name)
        with np.load(path, allow_pickle=False) as source:
            required = {"time_s", "signal", "pump_power_voltage", "t_burst_start_s", "t_burst_end_s"}
            if not required.issubset(source.files):
                raise ValueError(f"{path.name} 缺少必要波形字段")
            t, y = source["time_s"], source["signal"]
            start, end, voltage = (float(source[key]) for key in ("t_burst_start_s", "t_burst_end_s", "pump_power_voltage"))
            if (t.ndim != 1 or y.shape != t.shape or t.size < 4 or not np.isfinite(t).all() or not np.isfinite(y).all()
                    or not np.all(np.diff(t) > 0) or not all(math.isfinite(v) for v in (start, end, voltage)) or end <= start
                    or not np.any(t < start) or not np.any((t >= start) & (t <= end)) or not np.any(t > end)):
                raise ValueError(f"{path.name} 波形、功率或 burst 时间无效")
            dt = np.diff(t)
            if not np.allclose(dt, np.median(dt), rtol=1e-4, atol=1e-12):
                raise ValueError("实际时间轴不是均匀采样；不插值伪造波形")
            if reference is not None and (t.shape != reference.shape or not np.allclose(t, reference, rtol=0, atol=1e-9)):
                raise ValueError("不同功率的实际时间轴不一致，无法沿用原残差热图")
            reference = t.copy()
    return paths


def write_statistics(run_dir, paths):
    write_state(output_path(run_dir, "results/input_manifest.json"), {"files": [p.name for p in paths], "fingerprint": fingerprint(run_dir)})
    return {"n_points": len(paths)}


def fit(run_dir, params):
    config, _ = load_config(run_dir)
    results = []
    for path in load_inputs(run_dir, params):
        check_cancelled()
        with np.load(path, allow_pickle=False) as values:
            voltage = float(values["pump_power_voltage"])
        record = fit_one_shot(path, voltage, params.post_dark_time_s)
        record["file"] = path.name
        results.append(record)
    arrays = {}
    for key in results[0]:
        if key in ("popt_build", "popt_decay"):
            size = 3 if key == "popt_build" else 5
            arrays[key] = np.array([r[key] if r[key] is not None else np.full(size, np.nan) for r in results])
        else:
            arrays["source_file" if key == "file" else key] = np.array([np.nan if r[key] is None else r[key] for r in results])
    save_npz(output_path(run_dir, "results/fit_points.npz"), **arrays)
    summary = summarize(results, config, run_dir)
    summary.update(status="completed", scientific_quality="usable" if any(not p["low_quality"] for p in summary["per_point"]) else "diagnostic_only")
    for name in ("analysis_pub.json", "analysis.json"):
        write_state(output_path(run_dir, "results/"+name), summary)
    for name in ("analysis_pub.yaml", "analysis.yaml"):
        output_path(run_dir, "results/"+name).write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"n_points": len(results), "scientific_quality": summary["scientific_quality"]}


def load_fits(run_dir):
    with np.load(output_path(run_dir, "results/fit_points.npz"), allow_pickle=False) as source:
        records = [{"file" if key == "source_file" else key: source[key][i].copy() if source[key][i].ndim else source[key][i].item() for key in source.files}
                   for i in range(len(source["source_file"]))]
    for record in records:
        for key in ("tau_BB_s", "tau_BB_err_s", "tau_dark_s", "tau_dark_err_s", "omega_build_Hz", "phi_build_rad", "omega_decay_Hz", "phi_decay_rad"):
            if not np.isfinite(record[key]):
                record[key] = None
    return records


def write_report(run_dir):
    value = json.loads(output_path(run_dir, "results/analysis_pub.json").read_text(encoding="utf-8"))
    return {"n_points": value["n_points"], "scientific_quality": value["scientific_quality"]}


def summarize(results, config, run_dir):
    EXPERIMENT_TYPE = run_format.DATA_TYPE
    DATA_DIR = run_dir
    _fc = config.get("burst_pump", {}).get("modulation_freq_Hz", 90000) / 1000
    _dc = config.get("burst_pump", {}).get("fixed_duty_cycle_pct", 5.0)
    _Nc = config.get("burst_pump", {}).get("burst_cycles", 1000)
    COND_TEXT = (
        f"$f_L={_fc:.0f}" + r"\,\mathrm{kHz},\;"
        f"\\mathrm{{duty}}={_dc:.1f}" + r"\%,"
        rf"\;N={_Nc}$"
    )

    valid_mask = np.array([np.isfinite(r["R_pump_eff_per_s"]) for r in results])
    pumps  = np.array([r["pump_high_v"] for r in results])
    R_eff  = np.array([r["R_pump_eff_per_s"] for r in results])
    tBB    = np.array([(r["tau_BB_s"] if r["tau_BB_s"] is not None else np.nan) for r in results])
    tD     = np.array([(r["tau_dark_s"] if r["tau_dark_s"] is not None else np.nan) for r in results])
    eBB    = np.array([(r["tau_BB_err_s"] if r["tau_BB_err_s"] is not None and np.isfinite(r["tau_BB_err_s"]) else np.nan) for r in results])
    eD     = np.array([(r["tau_dark_err_s"] if r["tau_dark_err_s"] is not None and np.isfinite(r["tau_dark_err_s"]) else np.nan) for r in results])
    r2b    = np.array([(r["r2_build"] if np.isfinite(r["r2_build"]) else np.nan) for r in results])
    GBB    = np.array([(r["Gamma_BB_per_s"] if np.isfinite(r["Gamma_BB_per_s"]) else np.nan) for r in results])
    GD     = np.array([(r["Gamma_dark_per_s"] if np.isfinite(r["Gamma_dark_per_s"]) else np.nan) for r in results])

    # error propagation
    with np.errstate(divide="ignore", invalid="ignore"):
        e_R   = np.sqrt((eBB / tBB**2)**2 + (eD / tD**2)**2)
        e_GBB = eBB / tBB**2
        e_GD  = eD  / tD**2

    low_quality = (r2b < 0.7) | (R_eff < 0) | (~valid_mask)
    hq = ~low_quality

    analysis = {
        "experiment_type": EXPERIMENT_TYPE,
        "data_dir": str(DATA_DIR),
        "method": "Hilbert envelope -> build-up env fit (3 params) -> decay full-osc fit (5 params)",
        "n_points": len(results),
        "cond_text": COND_TEXT,
        "per_point": [],
    }

    for i, r in enumerate(results):
        analysis["per_point"].append({
            "pump_high_V": float(r["pump_high_v"]),
            "file": r["file"],
            "tau_BB_s": (float(r["tau_BB_s"]) if r["tau_BB_s"] is not None else None),
            "tau_BB_err_s": (float(r["tau_BB_err_s"]) if r["tau_BB_err_s"] is not None and np.isfinite(r["tau_BB_err_s"]) else None),
            "Gamma_BB_per_s": (float(r["Gamma_BB_per_s"]) if np.isfinite(r["Gamma_BB_per_s"]) else None),
            "e_Gamma_BB_per_s": (float(e_GBB[i]) if np.isfinite(e_GBB[i]) else None),
            "tau_dark_s": (float(r["tau_dark_s"]) if r["tau_dark_s"] is not None else None),
            "tau_dark_err_s": (float(r["tau_dark_err_s"]) if r["tau_dark_err_s"] is not None and np.isfinite(r["tau_dark_err_s"]) else None),
            "Gamma_dark_per_s": (float(r["Gamma_dark_per_s"]) if np.isfinite(r["Gamma_dark_per_s"]) else None),
            "e_Gamma_dark_per_s": (float(e_GD[i]) if np.isfinite(e_GD[i]) else None),
            "R_pump_eff_per_s": (float(r["R_pump_eff_per_s"]) if np.isfinite(r["R_pump_eff_per_s"]) else None),
            "e_R_pump_eff_per_s": (float(e_R[i]) if np.isfinite(e_R[i]) else None),
            "r2_build": (float(r["r2_build"]) if np.isfinite(r["r2_build"]) else None),
            "fit_reliable_BB": bool(r["fit_reliable_BB"]),
            "fit_reliable_dark": bool(r["fit_reliable_dark"]),
            "low_quality": bool(low_quality[i]),
        })

    return analysis
