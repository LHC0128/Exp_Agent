"""标准 Mx 相图与噪声路径的离线算法；不连接仪器，不改写 raw。

本模块同时承担采集期的质量体检与判据：acquisition 只用同样的函数做帧体检、圆拟合与
工作点选择，避免两套算法。所有拟合都基于已保存的原始帧，可离线重算。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import yaml

from . import run_format
from .models import FINE_SPAN_DEG, FINE_STEP_DEG, MxStandardRfPhaseSensitivityParams
from .run_format import DIAGRAM_PLOT, NOISE_PLOT, SUMMARY, require_raw_format
from .stages import ANALYSIS_KEYS

# 帧体检：帧内最强单音不得超过直流的这一比例，否则该帧不是滤波后的解调直流。
# 只查 100 Hz 以上的单音：低频段的涨落就是待测噪声本身，不能当单音。
MAX_TONE_OVER_DC = 0.2
TONE_MIN_HZ = 100.0
MAX_STD_OVER_DC = 5.0
MIN_DC_MV = 0.5
# 与 rf_phase_response / rf_phase_sensitivity 研究同口径的低频段；50/100 Hz 工频另行报告。
BAND_HZ = (3.0, 50.0)
MAINS_HZ = (50.0, 100.0, 150.0, 200.0, 250.0)


def output_path(run_dir, name):
    """运行目录内的产物路径；越界即拒绝。"""
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir: Path):
    """读取本轮 experiment_config.yaml 与参数快照，不使用当前默认值代替。"""
    with output_path(run_dir, "experiment_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    require_raw_format(config)
    if config.get("completion_status") == "running":
        raise ValueError("本轮采集仍在写入")
    return config, MxStandardRfPhaseSensitivityParams.from_external(config.get("parameters", {}))


def resolve_options(base, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    return type(base).from_external(base.to_external() | options)


def signature(path):
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()] if path.is_file() else None


def fingerprint(run_dir):
    """分析输入指纹：运行配置与全部原始帧的大小与摘要。"""
    root = Path(run_dir)
    paths = [output_path(root, "experiment_config.yaml"), *sorted(output_path(root, "raw").glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def frame_quality(label: str, x_v: np.ndarray, y_v: np.ndarray, rate_sa_s: float) -> dict:
    """体检一段 XY 帧：直流幅度、最强单音幅度与频率；不合格直接报错，不把坏帧当有效记录。"""
    x = np.asarray(x_v, dtype=float)
    y = np.asarray(y_v, dtype=float)
    if x.size != y.size or x.size < 64:
        raise ValueError(f"{label} 帧样本数异常：x={x.size}, y={y.size}")
    z = x + 1j * y
    dc = complex(np.mean(z))
    spectrum = np.fft.fft(z - dc)
    freqs = np.fft.fftfreq(z.size, 1.0 / float(rate_sa_s))
    high = np.abs(freqs) >= TONE_MIN_HZ
    peak = int(np.argmax(np.where(high, np.abs(spectrum), -1.0)))
    tone_v = float(2.0 * abs(spectrum[peak]) / z.size)
    quality = dict(label=label, samples=int(z.size), rate_sa_s=float(rate_sa_s),
                   dc_v=float(abs(dc)), tone_v=tone_v, tone_hz=float(freqs[peak]),
                   std_x_v=float(np.std(x)), std_y_v=float(np.std(y)))
    if quality["dc_v"] * 1e3 < MIN_DC_MV:
        raise ValueError(f"{label} 直流幅度 {quality['dc_v'] * 1e3:.3f} mV 低于 {MIN_DC_MV:g} mV，"
                         "不能当解调输出")
    if tone_v > MAX_TONE_OVER_DC * quality["dc_v"]:
        raise ValueError(
            f"{label} 帧内 {quality['tone_hz']:.1f} Hz 单音 {tone_v * 1e3:.1f} mV 超过直流 "
            f"{quality['dc_v'] * 1e3:.1f} mV 的 {MAX_TONE_OVER_DC:.0%}；该帧不是滤波后的解调直流")
    if quality["std_x_v"] > MAX_STD_OVER_DC * quality["dc_v"]:
        raise ValueError(
            f"{label} 帧内标准差 {quality['std_x_v'] * 1e3:.1f} mV 超过直流 "
            f"{quality['dc_v'] * 1e3:.1f} mV 的 {MAX_STD_OVER_DC:g} 倍；该帧被宽带分量主导，"
            "不能用于噪声谱")
    return quality


def circle_fit(x: np.ndarray, y: np.ndarray) -> dict:
    """代数最小二乘圆拟合：x^2+y^2 = a*x + b*y + c。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3 or x.size != y.size:
        raise ValueError(f"圆拟合至少需要 3 个同长数据点：x={x.size}, y={y.size}")
    design = np.column_stack([x, y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(design, x * x + y * y, rcond=None)
    cx, cy = float(sol[0] / 2.0), float(sol[1] / 2.0)
    radius_sq = float(sol[2] + cx * cx + cy * cy)
    if not math.isfinite(radius_sq) or radius_sq <= 0:
        raise ValueError(f"圆拟合失败：半径平方 {radius_sq}")
    radius = math.sqrt(radius_sq)
    residual = np.hypot(x - cx, y - cy) - radius
    return dict(center_x_v=cx, center_y_v=cy, radius_v=radius,
                rms_residual_v=float(np.sqrt(np.mean(residual ** 2))),
                max_abs_residual_v=float(np.max(np.abs(residual))))


def phasor_angles_deg(phasors, fit: dict) -> np.ndarray:
    """相量相对拟合圆心的角度（度）；相图上沿圆周转过的角度，用它做差分而不是用指令相位。"""
    rel = np.asarray(phasors, dtype=complex) - complex(fit["center_x_v"], fit["center_y_v"])
    return np.degrees(np.arctan2(rel.imag, rel.real))


def diagram_metrics(phasors, fit: dict) -> dict:
    """逐段差分给出切线斜率 |dS/dphi| 与 |dY/dphi|；角度取实测相位角，闭合回绕到首点。

    圆上切线斜率恒等于半径 |dS/dphi| = R；|dY/dphi| = R|cos phi| 只是切线在 Y 上的投影，
    随相位角在 0 与 R 之间变化，报告斜率时只用前者。
    """
    phasors = np.asarray(phasors, dtype=complex)
    angles = phasor_angles_deg(phasors, fit)
    # 相位角跨 ±180° 时原始差分会出现 ±360° 跳变：先解缠再差分，闭合方向随扫描方向。
    unwrapped = np.unwrap(np.radians(angles))
    steps = np.diff(unwrapped)
    if steps.size == 0 or np.median(np.abs(steps)) <= 0:
        raise ValueError(f"相图相邻点相位角没有推进：{np.round(angles, 3).tolist()}")
    closing = unwrapped[0] + math.copysign(2.0 * math.pi, float(np.median(steps)))
    closed_angles = np.r_[unwrapped, closing]
    closed = np.r_[phasors, phasors[0]]
    dphi = np.diff(closed_angles)
    dphi_deg = np.degrees(dphi)
    step_deg = float(np.median(np.abs(dphi_deg)))
    dphasor = np.diff(closed)
    with np.errstate(invalid="ignore", divide="ignore"):
        # 弦长 = 2R sin(dphi/2)：直接用弦长除 dphi 会带 sinc 偏差（15° 步进低 0.29%），
        # 这里按 sin(dphi/2) 精确反解，圆上得到的就是半径本身。
        tangent = np.abs(dphasor) / (2.0 * np.abs(np.sin(dphi / 2.0)))
        abs_dy = np.abs(np.diff(closed.imag) / dphi)
    accepted = np.abs(np.abs(dphi_deg) - step_deg) <= max(0.2 * step_deg, 1.0)
    valid = accepted & np.isfinite(tangent) & np.isfinite(abs_dy)
    if not np.any(valid):
        raise ValueError(f"相图没有可用的段差分：步进 {step_deg:.3f}°，相位角 {np.round(angles, 3).tolist()}")
    return dict(
        phasor_angle_deg=angles.tolist(),
        tangent_v_per_rad=tangent.tolist(),
        abs_dy_v_per_rad=abs_dy.tolist(),
        segment_dphi_deg=dphi_deg.tolist(),
        segment_center_deg=(np.degrees((closed_angles[:-1] + closed_angles[1:]) / 2.0) % 360.0).tolist(),
        accepted=accepted.tolist(),
        step_deg_median=step_deg,
        tangent_median_v_per_rad=float(np.median(tangent[valid])),
        tangent_relative_spread=float((np.max(tangent[valid]) - np.min(tangent[valid]))
                                      / max(np.median(tangent[valid]), 1e-15)),
        abs_dy_median_v_per_rad=float(np.median(abs_dy[valid])),
        accepted_count=int(np.sum(valid)),
        accepted_total=int(valid.size),
    )


def choose_working_point(phases_deg, phasors, fit: dict) -> dict:
    """按圆模型取 |dY/dphi| = R|cos phi| 最大的相图点，作为细扫中心。"""
    angles = phasor_angles_deg(phasors, fit)
    score = np.abs(np.cos(np.radians(angles)))
    index = int(np.argmax(score))
    radius = float(fit["radius_v"])
    return dict(index=index,
                command_deg=float(np.asarray(phases_deg, dtype=float)[index]),
                phasor_angle_deg=float(angles[index]),
                cos_phi=float(score[index]),
                radius_v=radius,
                analytic_dy_dphi_v_per_rad=float(radius * score[index]))


def as_phasors(values) -> np.ndarray:
    """把复数相量或 [[real, imag], ...] 记录统一成一维复数数组。"""
    items = []
    for item in values:
        if isinstance(item, (complex, int, float, np.floating, np.integer)):
            items.append(complex(item))
        else:
            items.append(complex(float(item[0]), float(item[1])))
    return np.asarray(items, dtype=complex)


def fine_offsets() -> np.ndarray:
    """工作点附近等步长细扫的偏移序列（默认 ±5°、步长 1°，共 11 点）。"""
    return np.arange(-FINE_SPAN_DEG, FINE_SPAN_DEG + 0.5 * FINE_STEP_DEG, FINE_STEP_DEG)


def fine_scan_slope(commands_deg, phasors, fit: dict) -> dict:
    """用细扫各点的实测相位角线性拟合 dY/dphi，并给出最接近切向沿 Y 的那个点。"""
    values = as_phasors(phasors)
    if values.size < 3:
        raise ValueError(f"细扫点数不足 3 个，无法拟合斜率：{values.size}")
    angles = phasor_angles_deg(values, fit)
    unwrapped = np.unwrap(np.radians(angles))
    design = unwrapped - unwrapped.mean()
    slope, intercept = np.polyfit(design, values.imag, 1)
    score = np.abs(np.cos(np.radians(angles)))
    best = int(np.argmax(score))
    residual = values.imag - (intercept + slope * design)
    return dict(slope_v_per_rad=float(slope), intercept_v=float(intercept),
                residual_v=float(np.std(residual)),
                best_index=best,
                best_command_deg=float(np.asarray(commands_deg, dtype=float)[best]),
                best_phasor_angle_deg=float(angles[best]),
                best_cos_phi=float(score[best]),
                analytic_v_per_rad=float(fit["radius_v"] * score[best]),
                phasor_angle_deg=angles.tolist())


def welch_psd(signals, rate_sa_s: float, segment_s: float):
    """各帧等权平均的单边功率谱密度；段长不足时报错。"""
    from scipy.signal import welch

    nperseg = max(16, int(round(segment_s * rate_sa_s)))
    spectra = []
    freqs = None
    for values in signals:
        if values.size < nperseg:
            raise ValueError(f"单段样本 {values.size} 少于 Welch 段长 {nperseg}")
        freqs, psd = welch(values - np.mean(values), fs=rate_sa_s, nperseg=nperseg,
                           detrend="constant", scaling="density")
        spectra.append(psd)
    return freqs, np.mean(np.stack(spectra, axis=0), axis=0)


def band_median(freqs, asd, band=BAND_HZ) -> float:
    mask = (freqs >= band[0]) & (freqs <= band[1])
    return float(np.median(asd[mask])) if np.any(mask) else float("nan")


def mains_median(freqs, asd) -> float:
    """50 Hz 及其谐波附近的 ASD 中位值，单独报告工频，不并入低频段结论。"""
    values = []
    for line in MAINS_HZ:
        mask = np.abs(freqs - line) <= 1.0
        if np.any(mask):
            values.append(float(np.median(asd[mask])))
    return float(np.median(values)) if values else float("nan")


def load_frames(run_dir: Path, labels) -> list[dict]:
    """按标签读取原始 XY 帧并体检；任一帧不合格即中止。"""
    frames = []
    for label in labels:
        meta, arrays = run_format.read_frame(run_dir, label, "xy")
        quality = frame_quality(label, arrays["x_v"], arrays["y_v"], float(meta["rate_sa_s"]))
        frames.append(dict(label=label, metadata=meta, quality=quality, time_s=arrays["time_s"],
                           x_v=arrays["x_v"], y_v=arrays["y_v"]))
    return frames


def frame_phasors(frames) -> np.ndarray:
    return np.asarray([complex(np.mean(f["x_v"]), np.mean(f["y_v"])) for f in frames], dtype=complex)


def analyze_data(run_dir: Path, config: dict) -> dict:
    """读原始帧，输出 summary.json 与两张图；返回供会话摘要使用的精简结论。"""
    diagram_state = config.get("phase_diagram") or {}
    fine_state = config.get("fine_scan") or {}
    noise_state = config.get("noise") or {}
    coarse_labels = [str(item) for item in diagram_state.get("labels") or ()]
    noise_labels = [str(item) for item in noise_state.get("labels") or ()]
    if not coarse_labels or not noise_labels:
        raise ValueError("运行目录缺少相图或噪声帧清单，无法分析")
    params = MxStandardRfPhaseSensitivityParams.from_external(config.get("parameters", {}))
    segment_s = float(params.welch_segment_s)
    results = output_path(run_dir, "results")
    results.mkdir(parents=True, exist_ok=True)

    coarse = load_frames(run_dir, coarse_labels)
    phasors = frame_phasors(coarse)
    fit = circle_fit(phasors.real, phasors.imag)
    radius = float(fit["radius_v"])
    metrics = diagram_metrics(phasors, fit)
    grid = [float(value) for value in diagram_state.get("grid_deg") or ()]
    chosen = choose_working_point(np.asarray(grid, dtype=float), phasors, fit)
    tangent_over_radius = metrics["tangent_median_v_per_rad"] / radius

    frames = load_frames(run_dir, noise_labels)
    rate = float(frames[0]["metadata"]["rate_sa_s"])
    for frame in frames:
        time = frame["time_s"]
        if not np.allclose(1.0 / np.median(np.diff(time)), rate, rtol=3e-3):
            raise ValueError(f"{frame['label']} 采样率与首段不一致")

    cx, cy = float(fit["center_x_v"]), float(fit["center_y_v"])
    # 工作点斜率取噪声帧自身的实测相位角：K = R|cos phi|，与帧内数据同口径，
    # 不用指令相位，也不把弦长当斜率。
    noise_angles = phasor_angles_deg(frame_phasors(frames), fit)
    phi_deg = float(np.degrees(np.mean(np.unwrap(np.radians(noise_angles)))))
    slope_y = float(radius * abs(math.cos(math.radians(phi_deg))))
    if not math.isfinite(slope_y) or slope_y <= 1e-12:
        raise ValueError(f"工作点斜率无效：相位角 {phi_deg:.3f}°，K={slope_y}")
    fine_summary = None
    if fine_state.get("commands_deg") and fine_state.get("phasors"):
        fine_summary = fine_scan_slope([float(value) for value in fine_state["commands_deg"]],
                                       fine_state["phasors"], fit)

    tangent_dir = complex(-math.sin(math.radians(phi_deg)), math.cos(math.radians(phi_deg)))
    centered, projected = [], []
    for frame in frames:
        theta = np.arctan2(frame["y_v"] - cy, frame["x_v"] - cx)
        mean_angle = np.angle(np.mean(np.exp(1j * theta)))
        centered.append(np.angle(np.exp(1j * (theta - mean_angle))))
        dx = frame["x_v"] - cx
        dy = frame["y_v"] - cy
        projected.append((dx * tangent_dir.real + dy * tangent_dir.imag) / radius)

    freqs, psd_y = welch_psd([frame["y_v"] for frame in frames], rate, segment_s)
    _, psd_theta = welch_psd(centered, rate, segment_s)
    _, psd_proj = welch_psd(projected, rate, segment_s)
    asd_theta = np.sqrt(psd_theta)
    asd_y_route = np.sqrt(psd_y) / slope_y
    asd_proj = np.sqrt(psd_proj)
    asd_raw_y = np.sqrt(psd_y)

    ratio = asd_theta / asd_y_route
    band_mask = (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])
    ratio_band = float(np.median(ratio[band_mask])) if np.any(band_mask) else float("nan")
    consistent = math.isfinite(ratio_band) and abs(ratio_band - 1.0) <= 0.1
    verdict = (f"theta 与 y 两条路径在 3-50 Hz 的相位噪声一致（中位比 {ratio_band:.2f}）" if consistent
               else f"theta 与 y 两条路径在 3-50 Hz 的相位噪声不一致（中位比 {ratio_band:.2f}）")
    slope_tolerance = 0.05
    circle_verdict = (
        f"相图 R={radius * 1e3:.3f} mV，{metrics['accepted_count']}/{metrics['accepted_total']} 段"
        f"切线斜率中位 {metrics['tangent_median_v_per_rad'] * 1e3:.3f} mV/rad"
        f"（散布 ±{metrics['tangent_relative_spread'] * 100.0:.1f}%），与 R 之比 {tangent_over_radius:.3f}；"
        + ("切线斜率在圆上等于半径" if abs(tangent_over_radius - 1.0) <= slope_tolerance
           else "切线斜率与半径不符"))

    summary = {
        "run_dir": str(Path(run_dir).resolve()),
        "experiment_id": config.get("experiment_id"),
        "gs200_target_ma": (config.get("main_field") or {}).get("current_after_ma"),
        "calibration_formula": config.get("calibration_formula"),
        "rf_frequency_hz": params.y_rf_freq_hz,
        "rf_vpp": params.y_rf_vpp,
        "circle_fit": fit,
        "diagram": {
            "grid_deg": grid,
            "phasors": [[float(z.real), float(z.imag)] for z in phasors],
            "phasor_angle_deg": metrics["phasor_angle_deg"],
            "tangent_v_per_rad": metrics["tangent_v_per_rad"],
            "tangent_median_v_per_rad": metrics["tangent_median_v_per_rad"],
            "tangent_relative_spread": metrics["tangent_relative_spread"],
            "tangent_over_radius": tangent_over_radius,
            "abs_dy_v_per_rad": metrics["abs_dy_v_per_rad"],
            "abs_dy_median_v_per_rad": metrics["abs_dy_median_v_per_rad"],
            "segment_center_deg": metrics["segment_center_deg"],
            "segment_dphi_deg": metrics["segment_dphi_deg"],
            "accepted": metrics["accepted"],
            "step_deg_median": metrics["step_deg_median"],
            "accepted_count": metrics["accepted_count"],
            "accepted_total": metrics["accepted_total"],
        },
        "working_point": {
            "coarse_candidate": chosen,
            "command_deg": (fine_summary or {}).get("best_command_deg", chosen["command_deg"]),
            "noise_phasor_angle_deg": phi_deg,
            "coarse_phasor_angle_deg": chosen["phasor_angle_deg"],
            "noise_slope_v_per_rad": slope_y,
            "noise_cos_phi": abs(math.cos(math.radians(phi_deg))),
            "fine_scan": fine_summary,
            "first_point_drift_v": diagram_state.get("first_point_drift_v"),
            "warmup_delta_v": diagram_state.get("warmup_delta_v"),
        },
        "rate_sa_s": rate,
        "welch_segment_s": segment_s,
        "segments": len(frames),
        "demod_dynamic": False,
        "time_constant_s": (frames[0]["metadata"].get("lockin") or {}).get("time_constant_s"),
        "frame_quality": {"coarse": [frame["quality"] for frame in coarse],
                          "noise": [frame["quality"] for frame in frames]},
        "asd": {
            "frequency_hz": freqs.tolist(),
            "theta_rad_per_sqrt_hz": asd_theta.tolist(),
            "y_over_slope_rad_per_sqrt_hz": asd_y_route.tolist(),
            "projected_rad_per_sqrt_hz": asd_proj.tolist(),
            "y_v_per_sqrt_hz": asd_raw_y.tolist(),
            "theta_over_y_route_ratio": ratio.tolist(),
        },
        "band_3_50_hz": {
            "theta_rad_per_sqrt_hz": band_median(freqs, asd_theta),
            "y_over_slope_rad_per_sqrt_hz": band_median(freqs, asd_y_route),
            "projected_rad_per_sqrt_hz": band_median(freqs, asd_proj),
            "theta_over_y_route_ratio": ratio_band,
            "mains_theta_rad_per_sqrt_hz": mains_median(freqs, asd_theta),
        },
        "verdict": verdict,
        "circle_verdict": circle_verdict,
    }
    (results / SUMMARY).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_diagram(results / DIAGRAM_PLOT, summary)
    plot_noise(results / NOISE_PLOT, summary)
    return compact(summary)


def compact(summary: dict) -> dict:
    """会话状态只保存结论与关键标量，谱数组留在 results/summary.json。"""
    return {
        "circle_fit": summary["circle_fit"],
        "diagram": {key: summary["diagram"][key] for key in
                    ("tangent_median_v_per_rad", "tangent_relative_spread", "tangent_over_radius",
                     "accepted_count", "accepted_total", "step_deg_median")},
        "working_point": {key: summary["working_point"][key] for key in
                          ("command_deg", "coarse_phasor_angle_deg", "noise_phasor_angle_deg",
                           "noise_slope_v_per_rad", "first_point_drift_v", "warmup_delta_v")},
        "band_3_50_hz": summary["band_3_50_hz"],
        "segments": summary["segments"],
        "rate_sa_s": summary["rate_sa_s"],
        "circle_verdict": summary["circle_verdict"],
        "verdict": summary["verdict"],
    }


def plot_diagram(path: Path, summary: dict) -> None:
    """左：实测相图与拟合圆；右：逐段切线斜率与 R、|dY/dphi| 投影对比。"""
    from ...plotting import COLOR_GRAY, COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure,         plot_style_context, save_figure

    with plot_style_context("paper"):
        points = np.asarray(summary["diagram"]["phasors"], dtype=float)
        fit = summary["circle_fit"]
        grid = np.asarray(summary["diagram"]["grid_deg"], dtype=float)
        theta_line = np.linspace(0.0, 2.0 * math.pi, 361)
        fig, axes = new_figure(kind="wide", nrows=1, ncols=2)
        ax = axes[0]
        scatter = ax.scatter(points[:, 0] * 1e3, points[:, 1] * 1e3, c=grid, cmap="twilight", s=18,
                             zorder=3, label="measured")
        ax.plot((fit["center_x_v"] + fit["radius_v"] * np.cos(theta_line)) * 1e3,
                (fit["center_y_v"] + fit["radius_v"] * np.sin(theta_line)) * 1e3,
                "--", color=COLOR_GRAY, lw=1.0, label="circle fit")
        ax.plot([fit["center_x_v"] * 1e3], [fit["center_y_v"] * 1e3], "+", color="k", ms=8, label="center")
        ax.set_aspect("equal", adjustable="box")
        format_axis(ax, xlabel="X (mV)", ylabel="Y (mV)")
        ax.set_title(f"phase diagram, R = {fit['radius_v'] * 1e3:.2f} mV, "
                     f"rms = {fit['rms_residual_v'] * 1e3:.2f} mV")
        ax.legend(loc="best")
        fig.colorbar(scatter, ax=ax, label="commanded phase (deg)")

        ax = axes[1]
        centers = np.asarray(summary["diagram"]["segment_center_deg"], dtype=float)
        tangent = np.asarray(summary["diagram"]["tangent_v_per_rad"], dtype=float) * 1e3
        abs_dy = np.asarray(summary["diagram"]["abs_dy_v_per_rad"], dtype=float) * 1e3
        accepted = np.asarray(summary["diagram"]["accepted"], dtype=bool)
        radius = float(summary["circle_fit"]["radius_v"]) * 1e3
        ax.plot(centers, tangent, "o-", ms=2.5, lw=1.0, color=COLOR_OPTIMAL, label="|dS/dphi| tangent slope")
        if np.any(~accepted):
            ax.plot(centers[~accepted], tangent[~accepted], "x", ms=7, color=COLOR_TRAD,
                    label="excluded segment")
        ax.axhline(radius, ls="--", color=COLOR_TRAD, lw=1.0, label=f"R = {radius:.2f} mV/rad")
        ax.plot(centers, abs_dy, ":", lw=0.9, color=COLOR_GRAY, label="|dY/dphi| = R|cos phi|")
        ax.axvline(summary["working_point"]["noise_phasor_angle_deg"], ls=":", color="0.2", lw=0.9,
                   label="working-point angle")
        format_axis(ax, xlabel="segment center on the circle (deg)", ylabel="slope (mV/rad)")
        ax.set_title("tangent slope = R; |dY/dphi| is only its Y projection")
        ax.legend(loc="best", fontsize=6)
        save_figure(fig, path)


def plot_noise(path: Path, summary: dict) -> None:
    """左：三条相位噪声路径；右：theta 与 y 路径的逐频比值。"""
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        freq = np.asarray(summary["asd"]["frequency_hz"], dtype=float)
        fig, axes = new_figure(kind="wide", nrows=1, ncols=2)
        ax = axes[0]
        ax.loglog(freq, np.asarray(summary["asd"]["theta_rad_per_sqrt_hz"]) * 1e6, lw=1.0,
                  color=COLOR_OPTIMAL, label="theta = atan2(y-y0, x-x0)")
        ax.loglog(freq, np.asarray(summary["asd"]["y_over_slope_rad_per_sqrt_hz"]) * 1e6, lw=1.0,
                  color=COLOR_TRAD, label=f"Y / K, K = {summary['working_point']['noise_slope_v_per_rad']:.4f} V/rad")
        ax.loglog(freq, np.asarray(summary["asd"]["projected_rad_per_sqrt_hz"]) * 1e6, lw=0.8, ls=":",
                  color="0.35", label="tangent projection")
        ax.axvspan(*BAND_HZ, color="0.92", zorder=0)
        format_axis(ax, xlabel="Frequency (Hz)", ylabel="phase noise (urad/sqrt(Hz))")
        ax.set_title("phase sensitivity: theta vs Y route (same frames)")
        ax.legend(loc="best", fontsize=6)
        ax = axes[1]
        ax.semilogx(freq, np.asarray(summary["asd"]["theta_over_y_route_ratio"]), lw=1.0, color=COLOR_OPTIMAL)
        ax.axhline(1.0, ls="--", color="0.4", lw=0.9)
        ax.axvspan(*BAND_HZ, color="0.92", zorder=0)
        format_axis(ax, xlabel="Frequency (Hz)", ylabel="theta / Y-route ratio")
        ax.set_title("ratio of the two paths")
        save_figure(fig, path)
