"""离线分析：多相位运行按射频相位分解求 c−1 定 π 宽度；单相位运行沿用 R(τ) 拟合。

多相位（scan.csv 含 ≥3 个不同 ``rf_phase_deg``）：每个脉宽把各相位的复读数 Z = R·e^{iθ}
按 ``Z(φ) = c0 + c+1 e^{iφ} + c−1 e^{−iφ}`` 做最小二乘分解。c0 与射频无关，c+1 在没有脉冲时
也存在，c−1 只由脉冲产生，因此用 |c−1| 的峰位（相邻至多 7 点的加权抛物线顶点）作 τ_π；
c−1 在峰之后最接近零的位置（实部、虚部各做二次拟合）给出 2π 宽度，按「面积 = 宽度 − 沿宽」
换算出第二个 τ_π 作交叉核对。

单相位（旧格式或 ``SCAN_RF_PHASE_COUNT`` = 1）：判据模型 ``R(τ) = R_max |sin(π(τ − τ0)/(2τ_π))|``，
逐点标准误作权重；残差 RMS 超过单点标准误中位数 3 倍时标记 ``fit_quality: poor``。该模式下 R 峰
受 c0 影响随射频相位移动，结论只作兼容参考。

两种模式下信号小于单点标准误中位数的 5 倍时标记 ``signal_low``。本实验不做漂移校正。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import run_format
from .stages import ANALYSIS_KEYS

# 单点离开拟合曲线超过 3 倍标准误即不保留；残差 RMS 超过 3 倍中位标准误即判拟合质量差。
KEEP_SIGMA = 3.0
POOR_FIT_SIGMA = 3.0
# R_max 低于单点标准误中位数的 5 倍时判信号可忽略（相位可能落在零点附近）。
LOW_SIGNAL_MULTIPLE = 5.0
MIN_WIDTH_POINTS = 4
# 多相位分解：至少 3 个相位才能解 c0、c+1、c−1；峰位抛物线取峰两侧各至多 3 点。
MIN_PHASES = 3
PEAK_HALF_WINDOW = 3
HARMONIC_KEYS = ("c0", "c_plus1", "c_minus1")


class AnalysisQualityError(ValueError):
    """输入或拟合不满足分析条件；诊断仍保存。"""


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
    """本实验没有独立分析覆盖值；出现未知键时明确拒绝。"""
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    return base


def signature(path):
    if not path.is_file():
        return None
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]


def fingerprint(run_dir):
    """分步分析用的输入指纹：配置、扫描表与全部逐点序列。"""
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def model_r(width_us, r_max, tau0_us, tau_pi_us):
    """R(τ) 的判据模型；用于拟合与作图。"""
    x = np.asarray(width_us, dtype=float)
    return r_max * np.abs(np.sin(np.pi * (x - tau0_us) / (2.0 * tau_pi_us)))


def fit_r_vs_width(widths, values, sem):
    """加权最小二乘拟合 (R_max, τ0, τπ)；返回参数、1σ 与残差。

    网格给出稳健初值（幅度对固定 τ0、τπ 是线性的），再用 ``least_squares`` 在带边界的
    参数上收敛。协方差按加权最小二乘的 ``inv(JᵀJ)`` 计，残差超过权重时按约化 χ² 放大。
    """
    from scipy.optimize import least_squares

    x = np.asarray(widths, dtype=float)
    y = np.asarray(values, dtype=float)
    s = np.asarray(sem, dtype=float)
    if x.size != y.size or x.size != s.size:
        raise AnalysisQualityError("宽度、R 与标准误长度不一致")
    if x.size < MIN_WIDTH_POINTS:
        raise AnalysisQualityError(f"至少需要 {MIN_WIDTH_POINTS} 个宽度点才能拟合 3 个参数")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise AnalysisQualityError("宽度或 R 含非有限值")
    usable = np.isfinite(s) & (s > 0)
    sigma = s if np.all(usable) else np.full(x.size, float(np.median(s[usable])) if np.any(usable) else 1.0)
    sqrt_w = 1.0 / sigma
    span = float(x.max() - x.min())
    if not math.isfinite(span) or span <= 0:
        raise AnalysisQualityError("宽度点必须有不同的取值")
    steps = np.diff(np.unique(x))
    step = float(np.min(steps)) if steps.size else span

    best = None
    for tau0 in np.linspace(x.min() - 2.0 * span, x.min() + 0.25 * span, 41):
        for tau_pi in np.linspace(0.25 * span, 3.0 * span, 80):
            shape = np.abs(np.sin(np.pi * (x - tau0) / (2.0 * tau_pi)))
            denominator = float(np.sum((shape / sigma) ** 2))
            if denominator <= 0:
                continue
            amplitude = float(np.sum(y * shape / sigma ** 2) / denominator)
            chi2 = float(np.sum(((y - amplitude * shape) / sigma) ** 2))
            if best is None or chi2 < best[0]:
                best = (chi2, amplitude, tau0, tau_pi)
    if best is None:
        raise AnalysisQualityError("模型在扫描区间内没有任何有效取值")
    _, amplitude, tau0, tau_pi = best
    amplitude_ceiling = max(4.0 * float(np.max(np.abs(y))), 1e-12)

    def residual(parameters):
        r_max, tau0_value, tau_pi_value = parameters
        return (model_r(x, r_max, tau0_value, tau_pi_value) - y) / sigma

    result = least_squares(
        residual, x0=[amplitude, tau0, tau_pi],
        bounds=([0.0, x.min() - 2.0 * span, 0.25 * step],
                [amplitude_ceiling, x.max(), 4.0 * span]),
        xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=20000)
    if not result.success or not np.all(np.isfinite(result.x)):
        raise AnalysisQualityError(f"R(τ) 拟合未收敛：{result.message}")
    r_max, tau0_value, tau_pi_value = (float(value) for value in result.x)
    if r_max <= 0 or tau_pi_value <= 0:
        raise AnalysisQualityError("拟合得到的幅度或 τπ 非正")
    model = model_r(x, r_max, tau0_value, tau_pi_value)
    residuals = y - model
    dof = int(x.size - 3)
    reduced_chi2 = float(2.0 * result.cost / dof) if dof > 0 else float("nan")
    try:
        covariance = np.linalg.inv(result.jac.T @ result.jac) * max(1.0, reduced_chi2 if dof > 0 else 1.0)
        errors = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    except np.linalg.LinAlgError as exc:
        raise AnalysisQualityError(f"拟合协方差奇异：{exc}") from exc
    return dict(r_max_v=r_max, r_max_sem_v=float(errors[0]),
                tau0_us=tau0_value, tau0_sem_us=float(errors[1]),
                tau_pi_us=tau_pi_value, tau_pi_sem_us=float(errors[2]),
                residual_rms_v=float(np.sqrt(np.mean(residuals ** 2))),
                reduced_chi2=reduced_chi2, dof=dof,
                model_v=model.tolist(), residual_v=residuals.tolist())


def harmonic_matrix(phases_deg):
    """分解矩阵：列依次为 1、e^{iφ}、e^{−iφ}（φ 为写入 DG 的 burst 相位）。"""
    phi = np.radians(np.asarray(phases_deg, dtype=float))
    return np.stack([np.ones_like(phi), np.exp(1j * phi), np.exp(-1j * phi)], axis=1).astype(complex)


def decompose(phases_deg, values):
    """最小二乘解 (c0, c+1, c−1) 与残差 RMS；相位数为 3 时残差恒为 0。"""
    matrix = harmonic_matrix(phases_deg)
    values = np.asarray(values, dtype=complex)
    coefficients, *_ = np.linalg.lstsq(matrix, values, rcond=None)
    residual = values - matrix @ coefficients
    return coefficients, float(np.sqrt(np.mean(np.abs(residual) ** 2)))


def _complex_sem(values):
    """复数均值的每轴标准误：sqrt((var Re + var Im)/2 / n)；不足 2 个时为 0。"""
    values = np.asarray(values, dtype=complex)
    if values.size < 2:
        return 0.0
    variance = (np.var(values.real, ddof=1) + np.var(values.imag, ddof=1)) / 2.0
    return float(math.sqrt(variance / values.size))


def _complex_record(value):
    return dict(re_v=float(value.real), im_v=float(value.imag), abs_v=float(abs(value)),
                arg_deg=float(math.degrees(math.atan2(value.imag, value.real))))


def _summarize_harmonics(rows, phases):
    """按宽度分解；缺任一相位的宽度不参与并单独计数。

    系数由各相位的均值分解；重复次数 ≥2 且每次都齐全时，对每次重复单独分解给出标准误。
    """
    by_width = {}
    for row in rows:
        width = float(row["width_us"])
        phase = float(row["rf_phase_deg"])
        value = float(row["R_v"]) * np.exp(1j * math.radians(float(row["theta_deg"])))
        if not np.isfinite(value):
            continue
        entry = by_width.setdefault(width, dict(values={}, temperatures=[]))
        entry["values"].setdefault(phase, {})[int(row["repeat"])] = value
        if str(row.get("temp_c", "")).strip():
            entry["temperatures"].append(float(row["temp_c"]))
    points, incomplete = [], []
    for width in sorted(by_width):
        entry = by_width[width]
        if any(phase not in entry["values"] for phase in phases):
            incomplete.append(width)
            continue
        means = [np.mean(list(entry["values"][phase].values())) for phase in phases]
        coefficients, residual = decompose(phases, means)
        repeats = sorted(set.intersection(*(set(entry["values"][phase]) for phase in phases)))
        per_repeat = np.array([decompose(phases, [entry["values"][phase][r] for phase in phases])[0]
                               for r in repeats]) if repeats else np.zeros((0, 3), dtype=complex)
        sems = [_complex_sem(per_repeat[:, k]) for k in range(3)]
        point = dict(width_us=float(width), repeats=len(repeats), residual_rms_v=residual,
                     phase_r_mean_v=[float(abs(value)) for value in means],
                     phase_sem_v=[_complex_sem(list(entry["values"][phase].values())) for phase in phases],
                     temperature_c=float(np.mean(entry["temperatures"])) if entry["temperatures"] else None)
        for key, value, sem in zip(HARMONIC_KEYS, coefficients, sems):
            point[key] = _complex_record(value) | dict(sem_v=sem)
        points.append(point)
    return points, incomplete


def _weighted_quadratic(u, y, sigma):
    """y ≈ a·u² + b·u + c 的加权最小二乘；返回系数与按约化 χ²（≥1）放大的协方差。"""
    design = np.stack([u ** 2, u, np.ones_like(u)], axis=1)
    weights = 1.0 / sigma
    coefficients, *_ = np.linalg.lstsq(design * weights[:, None], y * weights, rcond=None)
    dof = u.size - 3
    chi2 = float(np.sum(((design @ coefficients - y) * weights) ** 2))
    scale = max(1.0, chi2 / dof) if dof > 0 else 1.0
    covariance = np.linalg.pinv((design * weights[:, None]).T @ (design * weights[:, None])) * scale
    return coefficients, covariance, dof


def locate_peak(widths, magnitudes, sems):
    """|c−1| 峰位：峰在内部时取相邻至多 7 点的加权抛物线顶点，否则取网格峰并标 at_edge。"""
    x = np.asarray(widths, dtype=float)
    y = np.asarray(magnitudes, dtype=float)
    s = np.asarray(sems, dtype=float)
    k = int(np.argmax(y))
    grid = dict(peak_us=float(x[k]), peak_v=float(y[k]), peak_sem_us=None, method="grid",
                at_edge=k in (0, x.size - 1), window_us=None, curve=None)
    if grid["at_edge"]:
        return grid
    lo, hi = max(0, k - PEAK_HALF_WINDOW), min(x.size, k + PEAK_HALF_WINDOW + 1)
    u, yy, ss = x[lo:hi] - x[k], y[lo:hi], s[lo:hi]
    positive = ss[ss > 0]
    sigma = np.where(ss > 0, ss, float(np.median(positive)) if positive.size else 1.0)
    (a, b, c), covariance, _ = _weighted_quadratic(u, yy, sigma)
    if not a < 0:
        return grid
    vertex = -b / (2.0 * a)
    if not u[0] <= vertex <= u[-1]:
        return grid
    gradient = np.array([b / (2.0 * a * a), -1.0 / (2.0 * a)])
    variance = float(gradient @ covariance[:2, :2] @ gradient)
    curve_u = np.linspace(u[0], u[-1], 200)
    return dict(peak_us=float(x[k] + vertex), peak_v=float(a * vertex ** 2 + b * vertex + c),
                peak_sem_us=float(math.sqrt(max(variance, 0.0))), method="parabola", at_edge=False,
                window_us=[float(x[lo]), float(x[hi - 1])],
                curve=dict(width_us=(x[k] + curve_u).tolist(),
                           abs_v=(a * curve_u ** 2 + b * curve_u + c).tolist()))


def locate_null(widths, c_minus1, peak_index):
    """峰之后 c−1 最接近零的位置；实部、虚部分别二次拟合后在窗口内取模量最小点。

    最小点须离峰至少两个网格点、且不在扫描末端，否则返回 (None, 原因)。
    """
    x = np.asarray(widths, dtype=float)
    z = np.asarray(c_minus1, dtype=complex)
    j = peak_index + int(np.argmin(np.abs(z[peak_index:])))
    if j - peak_index < 2:
        return None, "峰之后 |c−1| 没有下降到明显的极小，回零点不在扫描范围内"
    if j == x.size - 1:
        return None, "|c−1| 在扫描末端仍在下降，回零点在扫描范围外"
    lo, hi = max(peak_index, j - PEAK_HALF_WINDOW), min(x.size, j + PEAK_HALF_WINDOW + 1)
    u = x[lo:hi] - x[j]
    real = np.polyfit(u, z[lo:hi].real, 2)
    imag = np.polyfit(u, z[lo:hi].imag, 2)
    fine = np.linspace(u[0], u[-1], 4001)
    magnitude = np.hypot(np.polyval(real, fine), np.polyval(imag, fine))
    m = int(np.argmin(magnitude))
    return dict(null_us=float(x[j] + fine[m]), null_v=float(magnitude[m]),
                window_us=[float(x[lo]), float(x[hi - 1])]), ""


def harmonic_report(root, summary, rows, phases, params):
    """多相位 B01：分解、定 c−1 峰与回零点、判质量并作图。"""
    points, incomplete = _summarize_harmonics(rows, phases)
    summary.update(mode="harmonic", rf_phases_deg=phases, phase_source="scan_phases",
                   model="Z(phi) = c0 + c+1*exp(i*phi) + c-1*exp(-i*phi); tau_pi = argmax |c-1|",
                   points=points, rows=len(rows), widths=len(points), incomplete_widths_us=incomplete)
    if len(points) < MIN_WIDTH_POINTS:
        return _write_unavailable(summary, root,
                                  f"相位齐全的宽度点 {len(points)} 个，少于定峰所需的 {MIN_WIDTH_POINTS} 个",
                                  plot_message=f"Fewer than {MIN_WIDTH_POINTS} complete widths", write_points=True)
    widths = [item["width_us"] for item in points]
    magnitudes = [item["c_minus1"]["abs_v"] for item in points]
    sems = np.array([item["c_minus1"]["sem_v"] for item in points], dtype=float)
    peak = locate_peak(widths, magnitudes, sems)
    peak_index = int(np.argmax(magnitudes))
    c_minus1 = [complex(item["c_minus1"]["re_v"], item["c_minus1"]["im_v"]) for item in points]
    null, null_reason = locate_null(widths, c_minus1, peak_index)
    edge = params.z_pulse_edge_us
    tau_pi = peak["peak_us"]
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    phase_sems = np.array([s for item in points for s in item["phase_sem_v"]], dtype=float)
    phase_sem_median = float(np.median(phase_sems[phase_sems > 0])) if np.any(phase_sems > 0) else 0.0
    residual_median = float(np.median([item["residual_rms_v"] for item in points]))
    fit_quality, fit_reason = "good", ""
    if peak["at_edge"]:
        fit_quality = "poor"
        fit_reason = "|c−1| 的峰在扫描端点，τπ 只是网格端点，需扩大 Z_PULSE_WIDTH_START/STOP_US"
    elif len(phases) > MIN_PHASES and phase_sem_median > 0 and residual_median > POOR_FIT_SIGMA * phase_sem_median:
        fit_quality = "poor"
        fit_reason = (f"分解残差中位数 {residual_median:.3g} V 超过单相位标准误中位数 {phase_sem_median:.3g} V 的 "
                      f"{POOR_FIT_SIGMA:g} 倍：响应含高次谐波或相位间漂移")
    elif sem_median <= 0:
        fit_reason = "N_REPEAT=1 或重复读数相同，c−1 没有标准误，峰位不确定度无法估计"
    summary.update(
        tau_pi_us=tau_pi, tau_pi_sem_us=peak["peak_sem_us"], tau_pi_method=f"c_minus1_peak_{peak['method']}",
        c_minus1_peak_v=peak["peak_v"], peak=dict((k, v) for k, v in peak.items() if k != "curve"),
        tau_2pi_us=null["null_us"] if null else None, c_minus1_null_v=null["null_v"] if null else None,
        tau_pi_from_null_us=(null["null_us"] - edge) / 2.0 + edge if null else None,
        null_reason=null_reason, decomposition_residual_median_v=residual_median,
        c_minus1_sem_median_v=sem_median,
        ratio_to_electrical=tau_pi / params.reference_pi_width_us,
        ratio_to_predicted=tau_pi / params.predicted_width_us(),
        fit_quality=fit_quality, fit_quality_reason=fit_reason, fit=None,
        keep=fit_quality == "good", reason="")
    if sem_median > 0 and peak["peak_v"] < LOW_SIGNAL_MULTIPLE * sem_median:
        summary["signal_low"] = True
        summary["signal_low_reason"] = (f"|c−1| 峰值 {peak['peak_v']:.3g} V 小于其标准误中位数 {sem_median:.3g} V 的 "
                                        f"{LOW_SIGNAL_MULTIPLE:g} 倍：脉冲产生的分量可忽略，峰位不可信")
        summary["keep"] = False
    output_path(root, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_harmonics(root, summary, points, peak, null)
    return summary


def _scan_phases(rows):
    """scan.csv 中出现的射频相位（升序）；旧格式无该列时为空。"""
    values = {float(row["rf_phase_deg"]) for row in rows if str(row.get("rf_phase_deg") or "").strip()}
    return sorted(values)


def _read_scan(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summarize_points(rows):
    """按宽度汇总：R 均值与标准误、θ 圆周均值、温度均值。"""
    widths = sorted({float(row["width_us"]) for row in rows})
    points = []
    for width in widths:
        group = [row for row in rows if math.isclose(float(row["width_us"]), width, rel_tol=0, abs_tol=1e-9)]
        values = np.array([float(row["R_v"]) for row in group], dtype=float)
        finite = np.isfinite(values)
        if not np.any(finite):
            continue
        theta = np.radians(np.array([float(row["theta_deg"]) for row in group], dtype=float)[finite])
        temperatures = [float(row["temp_c"]) for row in group if str(row.get("temp_c", "")).strip()]
        points.append(dict(
            width_us=float(width), repeats=int(values.size),
            r_mean_v=float(np.mean(values[finite])),
            r_sem_v=float(np.std(values[finite], ddof=1) / math.sqrt(np.count_nonzero(finite)))
            if np.count_nonzero(finite) > 1 else 0.0,
            theta_mean_deg=math.degrees(math.atan2(float(np.mean(np.sin(theta))), float(np.mean(np.cos(theta))))),
            temperature_c=float(np.mean(temperatures)) if temperatures else None))
    return points


def _run_state(run_dir):
    """读运行目录的 experiment_config.yaml；缺文件返回空映射。"""
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    return config if isinstance(config, dict) else {}


def _phase_record(config, params):
    """本轮实际生效的射频相位、来源与相位粗扫记录；配置缺键时回落到参数值。"""
    record = config.get("phase_check")
    if isinstance(record, dict) and record.get("enabled"):
        chosen, refined = record.get("chosen_deg"), record.get("refined_deg")
        for value in (refined, chosen):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value), dict(record), "stage"
    elif isinstance(record, dict):
        return float(params.y_rf_phase_deg), dict(record), "parameter"
    # 没有记录（A03 未运行）时以运行配置里最后一次写入的相位为准，再退回参数。
    phase = config.get("rf_phase_deg")
    source = config.get("phase_source")
    phase = float(phase) if isinstance(phase, (int, float)) and not isinstance(phase, bool) else float(params.y_rf_phase_deg)
    return phase, dict(enabled=False), source if source in {"stage", "parameter"} else "parameter"


def _demod_phase_deg(config):
    """MFLI 解调相位回读值；本实验只用 R，不依赖它。"""
    lockin = config.get("lockin")
    value = lockin.get("phase_deg") if isinstance(lockin, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def scan_report(run_dir, params):
    """B01：R(宽度) 加权拟合、τπ 及其 1σ、残差判据与电学参考比值。"""
    root = Path(run_dir)
    output_path(root, "results").mkdir(parents=True, exist_ok=True)
    config = _run_state(root)
    rf_phase_deg, phase_check, phase_source = _phase_record(config, params)
    summary = dict(experiment_id=run_format.EXPERIMENT_ID,
                   rf_frequency_hz=params.y_rf_freq_hz, rf_vpp=params.y_rf_vpp,
                   pulse_period_us=params.pulse_period_us, peak_current_ma=params.z_peak_current_ma,
                   edge_us=params.z_pulse_edge_us, n_repeat=params.n_repeat,
                   point_duration_s=params.scan_point_duration_s,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   target_angle_pi=params.target_angle_pi,
                   predicted_width_us=params.predicted_width_us(),
                   rf_phase_deg=rf_phase_deg,
                   rf_phase_offset_us=params.rf_phase_offset_us(rf_phase_deg),
                   phase_source=phase_source,
                   phase_check=phase_check,
                   demod0_phase_deg=_demod_phase_deg(config),
                   electrical_pi_width_us=params.reference_pi_width_us,
                   demod0_rate_sa_s=params.demod0_rate_sa_s, demod0_tc_s=params.demod0_tc_s,
                   model="R(tau) = R_max * |sin(pi*(tau - tau0)/(2*tau_pi))|",
                   drift_correction="none")
    path = output_path(root, "raw/scan.csv")
    if not path.is_file():
        return _write_unavailable(summary, root, "缺少 raw/scan.csv（未运行扫描）",
                                  plot_message="No R data: raw/scan.csv missing")
    rows = _read_scan(path)
    if not rows:
        return _write_unavailable(summary, root, "scan.csv 无有效行",
                                  plot_message="No valid scan rows")
    check_cancelled()
    phases = _scan_phases(rows)
    if params.multi_phase or len(phases) >= MIN_PHASES:
        if len(phases) < MIN_PHASES:
            summary.update(mode="harmonic", rf_phases_deg=phases, rows=len(rows))
            return _write_unavailable(summary, root, f"scan.csv 只有 {len(phases)} 个射频相位，分解至少需要 {MIN_PHASES} 个",
                                      plot_message=f"Fewer than {MIN_PHASES} RF phases recorded")
        return harmonic_report(root, summary, rows, phases, params)
    summary["mode"] = "single_phase"
    points = _summarize_points(rows)
    summary["points"] = points
    summary["rows"] = len(rows)
    summary["widths"] = len(points)
    if len(points) < MIN_WIDTH_POINTS:
        return _write_unavailable(summary, root,
                                  f"有效宽度点 {len(points)} 个，少于拟合所需的 {MIN_WIDTH_POINTS} 个",
                                  plot_message=f"Fewer than {MIN_WIDTH_POINTS} widths: cannot fit 3 parameters",
                                  write_points=True)
    fit = fit_r_vs_width([item["width_us"] for item in points], [item["r_mean_v"] for item in points],
                         [item["r_sem_v"] for item in points])
    sems = np.array([item["r_sem_v"] for item in points], dtype=float)
    positive = sems[sems > 0]
    sem_median = float(np.median(positive)) if positive.size else 0.0
    for item, model_value, residual_value in zip(points, fit["model_v"], fit["residual_v"]):
        reference = item["r_sem_v"] if item["r_sem_v"] > 0 else sem_median
        # 没有任何标准误时（重复读数相同或 N_REPEAT=1）无法做残差比较，不据此剔除点。
        normalized = abs(residual_value) / reference if reference > 0 else None
        item.update(model_v=float(model_value), residual_v=float(residual_value),
                    normalized_residual=float(normalized) if normalized is not None else None,
                    keep=bool(normalized is None or normalized <= KEEP_SIGMA))
    fit_quality, fit_reason = "good", ""
    if sem_median > 0 and fit["residual_rms_v"] > POOR_FIT_SIGMA * sem_median:
        fit_quality = "poor"
        fit_reason = (f"残差 RMS {fit['residual_rms_v']:.3g} V 超过单点标准误中位数 {sem_median:.3g} V 的 "
                      f"{POOR_FIT_SIGMA:g} 倍，判据模型与数据不自洽")
    elif sem_median <= 0:
        fit_reason = "单点标准误中位数为零（重复读数相同或 N_REPEAT=1），无法用残差与标准误比较，只记录原因"
    summary.update(
        fit=dict(r_max_v=fit["r_max_v"], r_max_sem_v=fit["r_max_sem_v"],
                 tau0_us=fit["tau0_us"], tau0_sem_us=fit["tau0_sem_us"],
                 tau_pi_us=fit["tau_pi_us"], tau_pi_sem_us=fit["tau_pi_sem_us"],
                 residual_rms_v=fit["residual_rms_v"], sem_median_v=sem_median,
                 reduced_chi2=fit["reduced_chi2"], dof=fit["dof"]),
        tau_pi_us=fit["tau_pi_us"], tau_pi_sem_us=fit["tau_pi_sem_us"],
        ratio_to_electrical=fit["tau_pi_us"] / params.reference_pi_width_us,
        ratio_to_predicted=fit["tau_pi_us"] / params.predicted_width_us(),
        fit_quality=fit_quality, fit_quality_reason=fit_reason,
        dropped_points=sum(1 for item in points if not item["keep"]),
        keep=fit_quality == "good" and all(item["keep"] for item in points),
        reason="")
    if sem_median > 0 and fit["r_max_v"] < LOW_SIGNAL_MULTIPLE * sem_median:
        summary["signal_low"] = True
        summary["signal_low_reason"] = (
            f"拟合幅度 R_max {fit['r_max_v']:.3g} V 小于单点标准误中位数 {sem_median:.3g} V 的 "
            f"{LOW_SIGNAL_MULTIPLE:g} 倍：信号可忽略，可能是射频相位落在零点附近；"
            f"不改变拟合结果，先核对相位自检记录")
    output_path(root, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_scan(root, summary, points, fit)
    return summary


def _write_unavailable(summary, run_dir, reason, *, plot_message, write_points=False):
    summary.update(rows=summary.get("rows", 0), reason=reason, fit_quality="unavailable",
                   fit_quality_reason=reason, keep=False, fit=None)
    if not write_points:
        summary.pop("points", None)
    output_path(run_dir, "results/scan_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_unavailable(run_dir, plot_message)
    return summary


def _plot_scan(run_dir, summary, points, fit):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    widths = np.array([item["width_us"] for item in points], dtype=float)
    means = np.array([item["r_mean_v"] for item in points], dtype=float)
    sems = np.array([item["r_sem_v"] for item in points], dtype=float)
    span = float(widths.max() - widths.min())
    curve_x = np.linspace(widths.min() - 0.1 * span, widths.max() + 0.1 * span, 600)
    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.errorbar(widths, means, yerr=sems, fmt="o", ms=4, capsize=2, color=COLOR_OPTIMAL,
                      label="MFLI R (Demod0)")
        axis.plot(curve_x, model_r(curve_x, fit["r_max_v"], fit["tau0_us"], fit["tau_pi_us"]), "-", lw=1,
                  color=COLOR_TRAD, label="weighted fit")
        axis.axvline(fit["tau_pi_us"], color="0.3", ls=":", lw=0.9,
                     label=fr"$\tau_\pi$ = {fit['tau_pi_us']:.3f} us")
        axis.axvline(summary["predicted_width_us"], color="0.6", ls="--", lw=0.8,
                     label=f"predicted {summary['predicted_width_us']:.3f} us")
        format_axis(axis, xlabel="Pulse width (us)", ylabel="R (V)")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/r_vs_width.png"))


def _plot_harmonics(run_dir, summary, points, peak, null):
    """多相位结果写进 r_vs_width.png：上图各相位的 R，下图 |c0|、|c+1|、|c−1| 与定峰结果。"""
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    widths = np.array([item["width_us"] for item in points], dtype=float)
    phases = summary["rf_phases_deg"]
    with plot_style_context("paper"):
        figure, (top, bottom) = new_figure(nrows=2, sharex=True)
        for index, phase in enumerate(phases):
            top.errorbar(widths, [item["phase_r_mean_v"][index] for item in points],
                         yerr=[item["phase_sem_v"][index] for item in points], fmt="o-", ms=3, lw=0.8,
                         capsize=1.5, label=f"RF phase {phase:g} deg")
        format_axis(top, ylabel="R (V)")
        top.legend(loc="best", fontsize=6)
        styles = (("c0", "|c0| (RF-independent)", "0.55", "s"),
                  ("c_plus1", "|c+1| (co-rotating)", COLOR_TRAD, "^"),
                  ("c_minus1", "|c-1| (pulse-only)", COLOR_OPTIMAL, "o"))
        for key, label, color, marker in styles:
            bottom.errorbar(widths, [item[key]["abs_v"] for item in points],
                            yerr=[item[key]["sem_v"] for item in points], fmt=marker + "-", ms=3, lw=0.8,
                            capsize=1.5, color=color, label=label)
        if peak.get("curve"):
            bottom.plot(peak["curve"]["width_us"], peak["curve"]["abs_v"], "-", lw=1.4, color="k",
                        label="parabola near peak")
        for axis in (top, bottom):
            axis.axvline(summary["tau_pi_us"], color="k", ls=":", lw=0.9)
            if null:
                axis.axvline(null["null_us"], color="0.4", ls="-.", lw=0.8)
            axis.axvline(summary["predicted_width_us"], color="0.7", ls="--", lw=0.8)
        bottom.plot([], [], "k:", label=fr"$\tau_\pi$ = {summary['tau_pi_us']:.3f} us (|c-1| peak)")
        if null:
            bottom.plot([], [], "-.", color="0.4", label=fr"$\tau_{{2\pi}}$ = {null['null_us']:.3f} us (c-1 null)")
        bottom.plot([], [], "--", color="0.7", label=f"predicted {summary['predicted_width_us']:.3f} us")
        format_axis(bottom, xlabel="Pulse width (us)", ylabel="Harmonic amplitude (V)")
        bottom.legend(loc="best", fontsize=6)
        save_figure(figure, output_path(run_dir, "results/r_vs_width.png"))


def _plot_unavailable(run_dir, message):
    import matplotlib
    matplotlib.use("Agg")
    from ...plotting import new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes, fontsize=8,
                  wrap=True)
        axis.set_axis_off()
        save_figure(figure, output_path(run_dir, "results/r_vs_width.png"))
