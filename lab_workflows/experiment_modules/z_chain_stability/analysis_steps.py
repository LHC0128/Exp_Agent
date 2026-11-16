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


def _mode_file(run_dir, mode):
    return output_path(run_dir, f"raw/zero_{mode}.csv")


def zero_report(run_dir, params):
    """B01：各零点段的均值、噪声、漂移、Allan，以及逐帧窗口内噪声；缺段留空。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    b_nt_per_ma = params.coil_constant_hz_per_ma / GYROMAGNETIC_RATIO_HZ_PER_NT
    segments = {}
    for mode in ("dg0", "baseline"):
        path = _mode_file(run_dir, mode)
        if not path.is_file():
            continue
        check_cancelled()
        data = _read_csv(path)
        if not data or data["current_a"].size < 2:
            continue
        dt = float(np.median(np.diff(data["elapsed_s"]))) if data["elapsed_s"].size > 1 else 0.0
        stats = _stats(data["current_a"], data["elapsed_s"], dt)
        segments[mode] = {**stats,
                          "mean_nt": stats["mean"] * 1e3 * b_nt_per_ma,
                          "std_nt": stats["std"] * 1e3 * b_nt_per_ma,
                          "drift_nt_per_day": stats["drift_per_s"] * 86400.0 * 1e3 * b_nt_per_ma}
    windows = _window_report(run_dir, params, b_nt_per_ma)
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, b_nt_per_ma=b_nt_per_ma,
                   coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   sense_resistor_ohm=params.sense_resistor_ohm, segments=segments,
                   windows=windows["summary"])
    output_path(run_dir, "results/zero_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if segments:
        _plot_zero(run_dir, segments)
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


def _plot_zero(run_dir, segments):
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
            axis.plot(data["elapsed_s"], data["current_a"] * 1e3, ".", ms=2, color=color, label=mode)
        format_axis(axis, xlabel="Elapsed (s)", ylabel="Current (mA)")
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="best", fontsize=7)
        save_figure(figure, output_path(run_dir, "results/zero_timeseries.png"))

        figure, axis = new_figure()
        for mode, color in (("dg0", COLOR_OPTIMAL), ("baseline", COLOR_TRAD)):
            if mode not in segments or not segments[mode]["allan_tau_s"]:
                continue
            axis.loglog(segments[mode]["allan_tau_s"], segments[mode]["allan_dev"], "o-", ms=3, color=color, label=mode)
        format_axis(axis, xlabel=r"$\tau$ (s)", ylabel=r"Allan deviation (mA)")
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
    """缺脉冲数据：写空摘要与原因，并补上声明的图片产物。"""
    summary.update(rows=0, reason=reason)
    output_path(run_dir, "results/pulse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_unavailable(run_dir, "pulse_timeseries.png", "No pulse data")
    _plot_unavailable(run_dir, "pulse_hist.png", "No pulse data")
    return summary


def pulse_report(run_dir, params):
    """B02：+π/−π 的 Q/θ 分布、漂移、Allan 与不对称；缺数据写空摘要与原因。"""
    (output_path(run_dir, "results")).mkdir(parents=True, exist_ok=True)
    path = output_path(run_dir, "raw/pulse_angles.csv")
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, coil_constant_hz_per_ma=params.coil_constant_hz_per_ma,
                   pulse_width_us=params.pulse_width_us, pulse_period_us=params.pulse_period_us,
                   source="pulse_angles.csv")
    if not path.is_file():
        return _empty_pulse(run_dir, summary, "缺少 raw/pulse_angles.csv（未运行 A05）")
    data = _read_csv(path)
    if not data or data["theta_pi"] is None or data["theta_pi"].size == 0:
        return _empty_pulse(run_dir, summary, "pulse_angles.csv 无有效行")
    sign, theta, elapsed = data["sign"], data["theta_pi"], data["elapsed_s"]
    positive, negative = sign == "+", sign == "-"
    dt = float(np.median(np.diff(elapsed))) if elapsed.size > 1 else 0.0
    stats = {}
    for name, mask in (("plus", positive), ("minus", negative)):
        if np.count_nonzero(mask) >= 2:
            stats[name] = _stats(theta[mask], elapsed[mask], dt)
    summary.update(rows=int(theta.size), plus=stats.get("plus"), minus=stats.get("minus"),
                   asymmetry_pi=(stats["plus"]["mean"] + stats["minus"]["mean"])
                   if "plus" in stats and "minus" in stats else None)
    output_path(run_dir, "results/pulse_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_pulse(run_dir, data, positive, negative)
    return summary


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

        figure, axis = new_figure()
        bins = max(10, min(60, int(np.sqrt(max(1, data["theta_pi"].size)))))
        axis.hist(data["theta_pi"], bins=bins, color=COLOR_OPTIMAL, alpha=0.75)
        format_axis(axis, xlabel=r"Pulse angle $\theta/\pi$", ylabel="Count")
        save_figure(figure, output_path(run_dir, "results/pulse_hist.png"))
