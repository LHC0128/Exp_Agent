"""离线输入资格、零点/脉冲 CSV 读取与两段表征；不连接仪器，不改写 raw。"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import processing, run_format
from .stages import ANALYSIS_KEYS

GYROMAGNETIC_RATIO_HZ_PER_NT = 7.0048
WINDOW_FRAME_LIMIT = 5000
WINDOW_PSD_FRAMES = 300
WINDOW_PSD_POINTS = 2048


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
    root = Path(run_dir)
    raw = output_path(root, "raw")
    paths = [output_path(root, "experiment_config.yaml"), *sorted(raw.glob("*.csv")), *sorted(raw.glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def _read_csv(path: Path) -> dict[str, np.ndarray]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    if not rows:
        return {}
    def column(name, cast=float):
        return np.asarray([cast(row[name]) for row in rows])

    def optional(name):
        return column(name) if name in rows[0] else None
    return dict(elapsed_s=column("elapsed_s"), current_a=optional("current_a"),
                mean_v=optional("mean_v"), std_v=optional("std_v"),
                sign=np.asarray([row.get("sign", "") for row in rows]),
                theta_pi=optional("theta_pi"), q_a_s=optional("q_a_s"),
                i_base_a=optional("i_base_a"), peak_a=optional("peak_a"))


def _stats(values, elapsed, dt_s):
    values = np.asarray(values, float)
    elapsed = np.asarray(elapsed, float)
    taus, devs = processing.allan_deviation(values, dt_s)
    return dict(rows=int(values.size), mean=float(np.mean(values)), std=float(np.std(values)),
                min=float(np.min(values)), max=float(np.max(values)),
                drift_per_s=processing.drift_rate(values, elapsed),
                allan_dt_s=float(dt_s), allan_tau_s=taus, allan_dev=devs)


def _drift_se(values, elapsed):
    """线性拟合斜率的标准误；按白噪声残差估，未计自相关，偏乐观。"""
    x, y = np.asarray(elapsed, float), np.asarray(values, float)
    if x.size < 3:
        return None
    sxx = float(np.sum((x - x.mean()) ** 2))
    if sxx <= 0:
        return None
    resid = y - np.polyval(np.polyfit(x, y, 1), x)
    dof = x.size - 2
    return float(np.sqrt((float(np.sum(resid ** 2)) / dof) / sxx)) if dof > 0 else None


def _decile_change(values):
    """首尾各 10% 的均值差及其标准误；不依赖线性模型。"""
    values = np.asarray(values, float)
    n = max(1, values.size // 10)
    head, tail = values[:n], values[-n:]
    if head.size < 2 or tail.size < 2:
        return None, None
    change = float(tail.mean() - head.mean())
    se = float(np.sqrt(head.var(ddof=1) / head.size + tail.var(ddof=1) / tail.size))
    return change, se


def _sampling(elapsed, nominal_dt_s):
    """采样完整性：间隔中位数、最大间隔与超长间隔个数。"""
    dt = np.diff(np.asarray(elapsed, float))
    if dt.size == 0:
        return dict(rows=1, nominal_dt_s=nominal_dt_s, dt_median_s=None, max_gap_s=None, long_gaps=0)
    median = float(np.median(dt))
    return dict(rows=int(dt.size + 1), nominal_dt_s=nominal_dt_s, dt_median_s=median,
                max_gap_s=float(np.max(dt)), long_gaps=int(np.count_nonzero(dt > 1.5 * median)))


def _mode_file(run_dir, mode):
    return output_path(run_dir, f"raw/zero_{mode}.csv")


def zero_report(run_dir, params):
    """B01：各零点段的均值、噪声、漂移、Allan 与窗口内噪声；缺段留空。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    b_nt_per_ma = params.coil_constant_hz_per_ma / GYROMAGNETIC_RATIO_HZ_PER_NT
    windows = _window_report(run_dir, params, b_nt_per_ma)
    segments = {}
    for mode in ("dg0", "baseline"):
        path = _mode_file(run_dir, mode)
        if not path.is_file():
            continue
        check_cancelled()
        data = _read_csv(path)
        if not data or data["current_a"].size < 2:
            continue
        elapsed = np.asarray(data["elapsed_s"], float)
        values = np.asarray(data["current_a"], float) * 1e3 * b_nt_per_ma
        keep = elapsed >= params.settle_exclude_s
        excluded = int(np.count_nonzero(~keep))
        elapsed, values = elapsed[keep], values[keep]
        if values.size < 2:
            continue
        dt = float(np.median(np.diff(elapsed))) if elapsed.size > 1 else 0.0
        stats = _stats(values, elapsed, dt)
        change, change_se = _decile_change(values)
        drift_se = _drift_se(values, elapsed)
        window = windows["summary"].get(mode)
        floor = window["within_std_median_nt"] / np.sqrt(window["points"]) if window else None
        segments[mode] = dict(
            unit="nT", rows=stats["rows"], excluded_rows=excluded,
            mean_nt=stats["mean"], std_nt=stats["std"], min_nt=stats["min"], max_nt=stats["max"],
            drift_nt_per_day=stats["drift_per_s"] * 86400.0,
            drift_se_nt_per_day=None if drift_se is None else drift_se * 86400.0,
            end_minus_start_nt=change,
            end_minus_start_se_nt=change_se,
            white_noise_floor_nt=floor,
            frames_vs_floor=(stats["std"] / floor) if floor else None,
            allan_dt_s=stats["allan_dt_s"], allan_tau_s=stats["allan_tau_s"],
            allan_dev_nt=stats["allan_dev"],
            sampling=_sampling(elapsed, params.zero_log_interval_s))
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, b_nt_per_ma=b_nt_per_ma,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   sense_resistor_ohm=params.sense_resistor_ohm,
                   settle_exclude_s=params.settle_exclude_s,
                   segments=segments, windows=windows["summary"])
    output_path(run_dir, "results/zero_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if segments:
        _plot_zero(run_dir, segments, b_nt_per_ma)
    else:
        _plot_unavailable(run_dir, "zero_timeseries.png", "No zero log data")
        _plot_unavailable(run_dir, "zero_allan.png", "No zero log data")
    if windows["series"]:
        _plot_window_noise(run_dir, windows["series"])
    else:
        _plot_unavailable(run_dir, "zero_window_noise.png", "No per-frame NPZ data")
    if windows["psd"]:
        _plot_window_psd(run_dir, windows["psd"])
    else:
        _plot_unavailable(run_dir, "zero_window_psd.png", "No per-frame NPZ data")
    return summary


def _window_frames(run_dir, mode):
    return sorted(output_path(run_dir, "raw").glob(f"zero_{mode}_[0-9]*.npz"))


def _load_window(path):
    with np.load(path, allow_pickle=False) as data:
        volts = np.asarray(data["sense_v"], dtype=float)
        meta = json.loads(str(data["meta"]))
    return volts, meta


def _window_report(run_dir, params, b_nt_per_ma):
    """逐帧读原始 NPZ：窗口内噪声、窗口均值时间序列与中位功率谱；缺帧时返回空。"""
    summary, series, psd = {}, {}, {}
    for mode in ("dg0", "baseline"):
        paths = _window_frames(run_dir, mode)
        if not paths:
            continue
        check_cancelled()
        step = max(1, math.ceil(len(paths) / WINDOW_FRAME_LIMIT))
        elapsed, mean_nt, std_nt, points, interval = [], [], [], [], None
        centred: list[np.ndarray] = []
        for index, path in enumerate(paths[::step]):
            if index % 200 == 0:
                check_cancelled()
            volts, meta = _load_window(path)
            if volts.size < 8:
                continue
            interval = float((meta.get("preamble") or {}).get("xincrement", 0.0)) or interval
            current = volts / params.sense_resistor_ohm
            elapsed.append(float(meta.get("elapsed_s", len(elapsed))))
            mean_nt.append(float(np.mean(current)) * 1e3 * b_nt_per_ma)
            std_nt.append(float(np.std(current)) * 1e3 * b_nt_per_ma)
            points.append(int(volts.size))
            if interval and len(centred) < WINDOW_PSD_FRAMES:
                centred.append(current - float(np.mean(current)))
        if not elapsed:
            continue
        order = np.argsort(elapsed)
        elapsed = np.asarray(elapsed, float)[order]
        mean_nt = np.asarray(mean_nt, float)[order]
        std_nt = np.asarray(std_nt, float)[order]
        summary[mode] = dict(
            frames=len(paths), frames_analyzed=int(elapsed.size), points=int(np.median(points)),
            sample_interval_s=interval, sample_rate_sa_s=(1.0 / interval) if interval else None,
            within_std_median_nt=float(np.median(std_nt)),
            within_std_drift_nt_per_day=processing.drift_rate(std_nt, elapsed) * 86400.0,
            mean_nt_min=float(np.min(mean_nt)), mean_nt_max=float(np.max(mean_nt)),
            start_elapsed_s=float(elapsed[0]), end_elapsed_s=float(elapsed[-1]))
        series[mode] = dict(elapsed_s=elapsed, std_nt=std_nt)
        if interval and centred:
            psd[mode] = _median_psd(centred, interval, b_nt_per_ma)
    return dict(summary=summary, series=series, psd=psd)


def _median_psd(frames, interval_s, b_nt_per_ma):
    """一帧一个 Welch 谱，取各频点中位数；频率上限为采样率的奈奎斯特频率。"""
    from scipy.signal import welch
    spectra = []
    for values in frames:
        nperseg = min(WINDOW_PSD_POINTS, values.size)
        freq, power = welch(values * 1e3 * b_nt_per_ma, fs=1.0 / interval_s, nperseg=nperseg,
                            detrend="constant", scaling="density")
        spectra.append(power)
    return dict(freq_hz=freq, psd_nt2_per_hz=np.median(np.asarray(spectra), axis=0),
                frames=len(spectra))


def _plot_unavailable(run_dir, name, message):
    """缺数据时仍产出声明的图片产物，图上写明原因而不是静默缺文件。"""
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes, fontsize=8)
        axis.set_axis_off()
        save_figure(figure, output_path(run_dir, f"results/{name}"))


def _plot_zero(run_dir, segments, b_nt_per_ma):
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            path = _mode_file(run_dir, mode)
            if not path.is_file():
                continue
            data = _read_csv(path)
            axis.plot(data["elapsed_s"], data["current_a"] * 1e3 * b_nt_per_ma, ".", ms=2, color=color, label=mode)
        format_axis(axis, xlabel="Elapsed (s)", ylabel="Field equivalent (nT)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_timeseries.png"))

        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            if mode not in segments or not segments[mode]["allan_tau_s"]:
                continue
            segment = segments[mode]
            taus = np.asarray(segment["allan_tau_s"], float)
            devs = np.asarray(segment["allan_dev_nt"], float)
            # 独立平均段数 n/(2m)；少于此数时长 τ 的点只有统计意义
            pairs = segment["rows"] / (2.0 * np.maximum(1.0, taus / max(segment["allan_dt_s"], 1e-12)))
            errors = devs / np.sqrt(np.maximum(pairs, 1.0))
            axis.set_xscale("log")
            axis.set_yscale("log")
            axis.errorbar(taus, devs, yerr=errors, fmt="o-", ms=3, lw=1, capsize=2, color=color, label=mode)
        format_axis(axis, xlabel=r"$\tau$ (s)", ylabel=r"Allan deviation (nT)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_allan.png"))


def _plot_window_noise(run_dir, series):
    """逐帧窗口内标准差随时间变化；它反映 20 ms 尺度的噪声本身是否稳定。"""
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            if mode not in series:
                continue
            axis.semilogy(series[mode]["elapsed_s"], series[mode]["std_nt"], ".", ms=2, color=color, label=mode)
        format_axis(axis, xlabel="Elapsed (s)", ylabel="Within-window std (nT)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_window_noise.png"))


def _plot_window_psd(run_dir, psd):
    """各帧 Welch 谱的中位数；频率上限由单帧采样率决定。"""
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            if mode not in psd:
                continue
            axis.loglog(psd[mode]["freq_hz"][1:], psd[mode]["psd_nt2_per_hz"][1:], "-", lw=1, color=color, label=mode)
        format_axis(axis, xlabel="Frequency (Hz)", ylabel=r"PSD (nT$^2$/Hz)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_window_psd.png"))


def _empty_pulse(run_dir, summary, reason):
    """缺脉冲数据：写空摘要、空 CSV 与原因，并补上声明的图片产物。"""
    summary.update(rows=0, reason=reason)
    output_path(run_dir, "results/pulse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    path = output_path(run_dir, "results/pulse_angles.csv")
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=PULSE_FIELDS).writeheader()
    _plot_unavailable(run_dir, "pulse_timeseries.png", "No pulse data")
    _plot_unavailable(run_dir, "pulse_hist_plus.png", "No pulse data")
    _plot_unavailable(run_dir, "pulse_hist_minus.png", "No pulse data")
    _plot_unavailable(run_dir, "pulse_base.png", "No pulse data")
    return summary


PULSE_FIELDS = ["timestamp", "elapsed_s", "sign", "q_a_s", "theta_pi", "i_base_a", "peak_a"]


def _pulse_rows_from_frames(run_dir, params):
    """从每次捕获的原始帧重算逐发积分与角度；没有原始帧时返回 None。

    顺便从首帧测出示波器的电压码宽，供基线台阶与 θ 量化标注。
    """
    paths = sorted(output_path(run_dir, "raw").glob("pulse_[0-9]*.npz"))
    if not paths:
        return None, 0, None
    rows, adc_step = [], None
    for path in paths:
        check_cancelled()
        with np.load(path, allow_pickle=False) as data:
            volts = np.asarray(data["sense_v"], dtype=float)
            time_s = np.asarray(data["time"], dtype=float)
            meta = json.loads(str(data["meta"]))
        if volts.size < 8:
            continue
        if adc_step is None:
            steps = np.diff(np.unique(np.round(volts, 9)))
            positive = steps[steps > 0]
            if positive.size:
                adc_step = float(np.min(positive))
        pulses = processing.segment_pulses(time_s, volts / params.sense_resistor_ohm,
                                           period_s=params.pulse_period_us * 1e-6,
                                           width_s=params.pulse_width_us * 1e-6,
                                           tail_s=params.pulse_tail_window_us * 1e-6)
        for pulse in pulses:
            theta = processing.pulse_angle_pi(pulse["q_a_s"], params.coil_constant_hz_per_ma)
            rows.append({"timestamp": str(meta.get("timestamp", "")),
                         "elapsed_s": f"{float(meta.get('elapsed_s', 0.0)):.6f}",
                         "sign": "+" if theta >= 0 else "-",
                         "q_a_s": f"{pulse['q_a_s']:.9g}", "theta_pi": f"{theta:.9g}",
                         "i_base_a": f"{pulse['i_base_a']:.9g}", "peak_a": f"{pulse['peak_a']:.9g}"})
    return rows, len(paths), adc_step


def pulse_report(run_dir, params):
    """B02：优先由逐帧原始波形重算 Q/θ，再给分布、漂移、Allan 与不对称。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   b_nt_per_ma=params.coil_constant_hz_per_ma / GYROMAGNETIC_RATIO_HZ_PER_NT,
                   pulse_width_us=params.pulse_width_us, pulse_period_us=params.pulse_period_us)
    rows, captures, adc_step = _pulse_rows_from_frames(run_dir, params)
    if rows is not None:
        path = output_path(run_dir, "results/pulse_angles.csv")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=PULSE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        summary.update(source="raw/pulse_*.npz", captures=captures,
                       adc_step_mv=(float(adc_step) * 1e3 if adc_step else None),
                       adc_step_ma=(float(adc_step) / params.sense_resistor_ohm * 1e3 if adc_step else None))
    else:
        path = output_path(run_dir, "raw/pulse_angles.csv")
        summary.update(source="raw/pulse_angles.csv", captures=None, adc_step_mv=None, adc_step_ma=None)
        if not path.is_file():
            return _empty_pulse(run_dir, summary, "缺少 raw/pulse_*.npz 与 raw/pulse_angles.csv（未运行 A05）")
    data = _read_csv(path)
    if not data or data["theta_pi"] is None or data["theta_pi"].size == 0:
        return _empty_pulse(run_dir, summary, "脉冲数据无有效行")
    sign, theta, elapsed = data["sign"], data["theta_pi"], data["elapsed_s"]
    positive, negative = sign == "+", sign == "-"
    dt = float(np.median(np.diff(elapsed))) if elapsed.size > 1 else 0.0
    stats = {}
    for name, mask in (("plus", positive), ("minus", negative)):
        if np.count_nonzero(mask) >= 2:
            stats[name] = _stats(theta[mask], elapsed[mask], dt)
    base_summary, base_series = _base_report(data, params, summary["b_nt_per_ma"], adc_step)
    summary.update(rows=int(theta.size), plus=stats.get("plus"), minus=stats.get("minus"),
                   asymmetry_pi=(stats["plus"]["mean"] + stats["minus"]["mean"])
                   if "plus" in stats and "minus" in stats else None,
                   base=base_summary)
    output_path(run_dir, "results/pulse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_pulse(run_dir, data, positive, negative)
    if base_series is None:
        _plot_unavailable(run_dir, "pulse_base.png", "No baseline data")
    else:
        _plot_base(run_dir, base_series,
                   adc_step / params.sense_resistor_ohm * 1e3 * summary["b_nt_per_ma"] if adc_step else None)
    return summary


def _base_report(data, params, b_nt_per_ma, adc_step=None):
    """逐次捕获的帧内基线：台阶、漂移以及它对 θ 的影响；缺列时返回 None。"""
    base = data.get("i_base_a")
    if base is None or base.size == 0:
        return None, None
    order = np.argsort(data["elapsed_s"])
    elapsed, first = np.unique(np.asarray(data["elapsed_s"], float)[order], return_index=True)
    values = np.asarray(base, float)[order][first]
    levels = np.unique(np.round(values, 12))
    step = float(np.median(np.diff(levels))) if levels.size > 1 else 0.0
    drift = processing.drift_rate(values, elapsed)
    # 基线整体偏 δ 时，两发面积都变化 δ·积分窗；这是 θ/π 对基线的敏感度
    theta_per_ma = 2.0 * params.coil_constant_hz_per_ma * 1000.0 * 1e-3 * params.pulse_tail_window_us * 1e-6
    code_ma = float(adc_step) / params.sense_resistor_ohm * 1e3 if adc_step else None
    summary = dict(captures=int(values.size), mean_ma=float(np.mean(values)) * 1e3,
                   std_ma=float(np.std(values)) * 1e3, min_ma=float(np.min(values)) * 1e3,
                   max_ma=float(np.max(values)) * 1e3, span_ma=float(np.max(values) - np.min(values)) * 1e3,
                   mean_nt=float(np.mean(values)) * 1e3 * b_nt_per_ma,
                   span_nt=float(np.max(values) - np.min(values)) * 1e3 * b_nt_per_ma,
                   levels=int(levels.size), step_ma=step * 1e3, step_nt=step * 1e3 * b_nt_per_ma,
                   steps_per_code=(step * 1e3 / code_ma) if code_ma else None,
                   drift_ma_per_day=float(drift) * 86400.0 * 1e3,
                   drift_nt_per_day=float(drift) * 86400.0 * 1e3 * b_nt_per_ma,
                   theta_per_ma=theta_per_ma,
                   theta_drift_bound_per_day=float(drift) * 86400.0 * 1e3 * theta_per_ma)
    return summary, dict(elapsed_s=elapsed, base_nt=values * 1e3 * b_nt_per_ma)


def _plot_base(run_dir, series, adc_step_nt=None):
    """帧内基线随时间；台阶状分布说明它被示波器垂直分辨率量化。"""
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.plot(series["elapsed_s"], series["base_nt"], ".", ms=2, color=COLOR_OPTIMAL)
        format_axis(axis, xlabel="Elapsed (s)", ylabel="Frame baseline (nT)")
        if adc_step_nt:
            # 右侧标出一码的高度，说明台阶来自 ADC 量化而不是真实漂移
            low = float(np.min(series["base_nt"]))
            bar_x = 0.95
            axis.annotate("", xy=(bar_x, low + adc_step_nt), xytext=(bar_x, low),
                          xycoords=axis.get_yaxis_transform(),
                          arrowprops=dict(arrowstyle="<->", lw=0.8, color="#555555"))
            axis.text(bar_x - 0.04, low + adc_step_nt / 2.0, f"1 ADC code\n{adc_step_nt:.1f} nT",
                      transform=axis.get_yaxis_transform(), ha="right", va="center", fontsize=7, color="#555555")
        save_figure(figure, output_path(run_dir, "results/pulse_base.png"))


def _plot_angle_histogram(run_dir, values, name, color):
    """单独画一侧的角度分布：只覆盖该侧 ±4σ，边上标均值与标准差。"""
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import format_axis, new_figure, plot_style_context, save_figure

    center, spread = float(np.mean(values)), float(np.std(values))
    half = max(4.0 * spread, 1e-6)
    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.hist(values, bins=np.linspace(center - half, center + half, 41), color=color, alpha=0.8)
        axis.axvline(center, color="black", lw=0.8, ls="--")
        axis.margins(y=0.22)
        axis.text(0.03, 0.98, f"mean {center:.4f}$\\pi$  sd {spread:.4f}$\\pi$  n {values.size}",
                  transform=axis.transAxes, va="top", fontsize=8)
        format_axis(axis, xlabel=r"Pulse angle $\theta/\pi$", ylabel="Count")
        save_figure(figure, output_path(run_dir, f"results/{name}"))


def _plot_pulse(run_dir, data, positive, negative):
    import matplotlib
    matplotlib.use("Agg")
    from lab_workflows.plotting import COLOR_OPTIMAL, COLOR_TRAD, format_axis, new_figure, plot_style_context, save_figure

    with plot_style_context("paper"):
        figure, axis = new_figure()
        axis.plot(data["elapsed_s"][positive], data["theta_pi"][positive], ".", ms=3, color=COLOR_OPTIMAL, label=r"$+\pi$")
        axis.plot(data["elapsed_s"][negative], data["theta_pi"][negative], ".", ms=3, color=COLOR_TRAD, label=r"$-\pi$")
        format_axis(axis, xlabel="Elapsed (s)", ylabel=r"Pulse angle $\theta/\pi$")
        axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/pulse_timeseries.png"))

    for mask, name, color in ((positive, "pulse_hist_plus.png", COLOR_OPTIMAL),
                              (negative, "pulse_hist_minus.png", COLOR_TRAD)):
        if np.count_nonzero(mask) >= 2:
            _plot_angle_histogram(run_dir, data["theta_pi"][mask], name, color)
