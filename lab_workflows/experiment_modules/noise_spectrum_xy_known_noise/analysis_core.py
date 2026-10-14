"""XY 已知噪声注入离线分析；off/on 参数层差分 + 真值链逐 bin 比较，不连接硬件。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import yaml
from scipy.signal import welch

from sensitivity_analysis.fitting import _dispersive

from ...experiment_runtime import check_cancelled, runtime_run_dir
from ...common import WorkflowCancelled, find_project_root
from ...plotting import (
    COLOR_CYAN,
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from ..noise_spectrum_xy.models import welch_settings
from ..noise_spectrum_xy.processing import SEPARATION_METHOD, fit_local_spectra, locate_ridge
from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from .dispersion import resolve_dispersion
from .generation import calculate_noise_preview, load_z_calibration
from .models import NoiseSpectrumXYKnownNoiseParams

ANALYSIS_VERSION = "known-noise-staged-static-gain-v6"


def calibration_band(frequency, options, saved):
    """标定覆盖已保存的扫描目标；输出频带不应截断远端控制点的移动峰。"""
    upper = max(options["frequency_max_hz"],
                float(saved.get("TARGET_NOISE_FREQ_STOP_HZ", options["frequency_max_hz"])))
    if not np.isfinite(upper):
        raise ValueError("脊线标定频率上限必须有限")
    # 使用实际采样率得到的频率轴，自然受 Nyquist 限制；目标只定搜索范围，不替代标定。
    return (frequency >= options["frequency_min_hz"]) & (frequency <= upper)


def _json_safe(value):
    """NaN/Inf 转 null；拟合失败产生的 NaN 不能中断诊断保存。"""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    return value


def _median_ratio(numerator, denominator, mask):
    """带内中位比值；掩码外或分母非正时返回 NaN，不外推。"""
    usable = np.asarray(mask, bool) & np.isfinite(numerator) & np.isfinite(denominator) & (denominator > 0)
    if not usable.any():
        return float("nan")
    return float(np.median(np.asarray(numerator, float)[usable] / np.asarray(denominator, float)[usable]))


def _injection_band_mask(frequency, bands) -> np.ndarray:
    """注入谱段在分析频率轴上的掩码；判据只允许在带内生效。"""
    frequency = np.asarray(frequency, float)
    mask = np.zeros(frequency.size, bool)
    for band in bands or ():
        start = float(band["start_hz"])
        stop = float(band["stop_hz"])
        mask |= (frequency >= start) & (frequency <= stop)
    if not mask.any():
        raise ValueError("已知噪声注入频段与本次分析频带无交集，判据无法评估")
    return mask


def _reason_counts(reasons) -> dict[str, int]:
    """逐频率列的拟合验收结论计数，便于在摘要里定位拒绝原因。"""
    counts: dict[str, int] = {}
    for reason in np.asarray(reasons).ravel():
        key = str(reason)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def write_json(path, payload):
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def compute_psd_matrix(run_dir: Path, config: dict, options: dict, suffix: str):
    """按波形编号对齐控制轴计算单侧（off 或 on）PSD 矩阵。"""
    daq = config["hf2_daq"]
    rate = float(daq.get("actual_rate_Sa_s", float("nan")))
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError("缺少有效的硬件实际采样率，不能从请求值猜测频率轴")
    scan = config["scan_params"]
    voltage = np.asarray(scan["xy_envelope_voltage_V"], float)
    if not np.isfinite(voltage).all() or np.any(np.diff(voltage) <= 0):
        raise ValueError("控制电压轴必须有限且严格递增")
    from sensitivity_analysis.acquisition import load_acquisition_rate
    files = sorted((run_dir / "raw").glob(f"waveform_C*_{suffix}.npy"))
    if files:
        rate = load_acquisition_rate(files[0], rate)
    settings = welch_settings(rate, float(daq["DAQ_duration_s"]), options["bin_width_hz"])
    nperseg = settings["nperseg"]
    frequency = np.fft.rfftfreq(nperseg, 1/rate)
    matrix = np.full((voltage.size, frequency.size), np.nan)
    valid_rows = []
    for i in range(voltage.size):
        check_cancelled()
        path = run_dir/"raw"/f"waveform_C{i:04d}_{suffix}.npy"
        if not path.exists():
            continue
        wave = np.load(path, allow_pickle=False)
        if not np.isclose(load_acquisition_rate(path, rate), rate, rtol=1e-6):
            raise ValueError("配对波形的实际采样率不一致，无法合并 PSD 频率轴")
        if wave.ndim != 1 or wave.size < nperseg or not np.isfinite(wave).all() or np.std(wave) == 0:
            continue
        _, psd = welch(wave, fs=rate, nperseg=nperseg, noverlap=nperseg//2,
                       window="hann", detrend="constant", scaling="density")
        matrix[i] = psd
        valid_rows.append(i)
    info = dict(settings)
    info["actual_rate_sa_s"] = rate
    return matrix, frequency, voltage, np.asarray(valid_rows, int), info


def save_plot(fig, path):
    # save_figure 默认关闭画布，分析器不直接使用 pyplot。
    save_figure(fig, path)


def plot_two_dimensional(results, matrix, frequency, voltage, ridge, name, title,
                         colorbar_label, diverging=False):
    """控制幅度 × 频率的 PSD 伪彩图；附色条，差分图用零中心的发散色标。

    差分矩阵（ON − OFF）的符号有意义，负值不能掩掉或取绝对值，因此用 symlog
    发散色标以 0 为中心；平均 PSD 恒为正，用常规对数色标。
    """
    fig, ax = new_figure(kind="wide", height_mm=65, constrained_layout=True)
    data = np.ma.masked_invalid(np.asarray(matrix, float))
    if diverging:
        finite = np.abs(data.compressed())
        positive = finite[finite > 0]
        # 线性阈值取本底量级（低分位），带宽内的对数结构才不被压平；
        # 色条上下限取正值的极高分位，避免单个尖峰吃掉全部动态范围。
        linthresh = float(np.percentile(positive, 50)) if positive.size else 1.0
        limit = float(np.percentile(positive, 99.5)) if positive.size else None
        mesh = ax.pcolormesh(frequency/1000, voltage, data, shading="auto",
                             cmap="RdBu_r", rasterized=True,
                             norm=matplotlib.colors.SymLogNorm(
                                 linthresh=linthresh, vmin=-limit, vmax=limit, base=10,
                                 linscale=0.4))
    else:
        data = np.ma.masked_less_equal(data, 0)
        log_data = np.ma.log10(data)
        lo, hi = np.percentile(log_data.compressed(), [5, 98]) if log_data.count() else (None, None)
        mesh = ax.pcolormesh(frequency/1000, voltage, log_data, shading="auto",
                             cmap="inferno", vmin=lo, vmax=hi, rasterized=True)
    ridge_label = "Provisional peak positions from K/B" if ridge.get("provisional") else "Calibrated ridge"
    ax.plot((ridge["k"]*voltage+ridge["b"])/1000, voltage, "--", color=COLOR_CYAN, lw=1.1,
            label=ridge_label)
    ax.set_xlim(frequency[0]/1000, frequency[-1]/1000)
    format_axis(ax, xlabel="Frequency (kHz)", ylabel="Sine peak voltage (V)")
    ax.set_title(title)
    style_legend(ax, loc="upper left")
    bar = fig.colorbar(mesh, ax=ax)
    bar.set_label(colorbar_label)
    if diverging:
        # SymLog 的默认定位器会在 0 附近堆叠刻度，改为按数量级显示。
        from matplotlib.ticker import LogLocator, NullFormatter
        bar.ax.yaxis.set_major_locator(LogLocator(base=10, numticks=7))
        bar.ax.yaxis.set_minor_formatter(NullFormatter())
        bar.ax.yaxis.set_major_formatter(matplotlib.ticker.LogFormatterMathtext(base=10))
    save_plot(fig, results/name)


def plot_differential_response(results, frequency, a_diff, good):
    """只画参数层差分响应系数；ON/OFF 总谱归入可控/不可控对比图。"""
    fig, ax = new_figure(kind="wide", height_mm=65)
    magnitude = np.abs(np.asarray(a_diff, float))
    visible = np.asarray(good, bool) & np.isfinite(magnitude) & (magnitude > 0)
    ax.plot(frequency[visible]/1000, magnitude[visible], ".", ms=2.5, color=COLOR_OPTIMAL,
            label="|A(ON) - A(OFF)|")
    ax.set_yscale("log")
    format_axis(ax, xlabel="Frequency (kHz)", ylabel="|Differential response coefficient A| (V²·Hz)")
    ax.set_title("Absolute differential response coefficient")
    ax.grid(True, which="major", alpha=0.22)
    style_legend(ax, loc="best")
    save_plot(fig, results/"noise_spectra_extracted.png")


def select_line_shape_columns(frequency, selectable, count):
    """在可选频率中均匀抽取若干列，用于展示洛伦兹线形。"""
    indices = np.flatnonzero(np.asarray(selectable, bool))
    if indices.size == 0 or count <= 0:
        return np.array([], int)
    picks = np.linspace(0, indices.size - 1, min(count, indices.size)).astype(int)
    return indices[np.unique(picks)]


def plot_line_shapes(results, frequency, control, psd_on, psd_off, popt_on, popt_off,
                     reasons_on, reasons_off, columns, half_width_hz, k_hz_per_v,
                     intercept_hz, *, background_on=None, background_off=None):
    """若干分析频率下 PSD 随控制场幅值的洛伦兹线形，并叠加拟合曲线与验收结论。

    展示完整控制轴及远端尾部；灰色区域仅标记峰定位窗口。
    """
    columns = np.asarray(columns, int)
    if columns.size == 0:
        return
    ncols = min(4, columns.size)
    nrows = int(np.ceil(columns.size / ncols))
    fig, axes = new_figure(kind="wide", height_mm=68 * nrows, nrows=nrows, ncols=ncols,
                           constrained_layout=True, squeeze=False)
    span = half_width_hz
    for ax, j in zip(axes.ravel(), columns):
        window = np.ones(control.size, bool)
        axis = control[window]
        ax.axvspan(frequency[j]-span, frequency[j]+span, color=COLOR_GRAY, alpha=.10)
        ax.semilogy(axis, psd_on[window, j], "o", ms=2.5, color=COLOR_OPTIMAL,
                    label="Injection ON")
        ax.semilogy(axis, psd_off[window, j], "s", ms=2.5, color=COLOR_GRAY,
                    label="Injection OFF")
        p_on = popt_on[j]
        if reasons_on[j] == "accepted":
            ax.semilogy(axis, lorentzian_vs_control(axis, p_on[0], p_on[1], p_on[2], p_on[3],
                                                frequency[j]),
                    "-", color=COLOR_OPTIMAL, lw=1.0, label="ON fit")
        if reasons_off[j] == "accepted" and np.isfinite(popt_off[j]).all():
            p_off = popt_off[j]
            ax.semilogy(axis, lorentzian_vs_control(axis, p_off[0], p_off[1], p_off[2],
                                                    p_off[3], frequency[j]),
                        "--", color=COLOR_GRAY, lw=1.0, label="OFF fit")
        for background, color, label in ((background_on, COLOR_OPTIMAL, "ON background"),
                                          (background_off, COLOR_GRAY, "OFF background")):
            if background is not None and np.isfinite(background[j]):
                ax.axhline(background[j], color=color, ls=":", lw=1, label=label)
        ax.axvline(frequency[j], color="k", ls=":", lw=0.8)
        format_axis(ax, xlabel="Control frequency (Hz)", ylabel=r"PSD (V$^2$/Hz)")
        ax.set_title(f"f = {frequency[j]/1000:.2f} kHz", fontsize=8)
        # 验收结论放在图内，避免与顶轴标签抢空间。
        annotations = [f"{state} peak: {'accepted' if reason == 'accepted' else 'unresolved'}"
                       for state, reason in (("ON", reasons_on[j]), ("OFF", reasons_off[j]))]
        for state, background in (("ON", background_on), ("OFF", background_off)):
            if background is not None:
                annotations.append(f"{state} background: {'resolved' if np.isfinite(background[j]) else 'unresolved'}")
        ax.text(0.03, 0.04, "\n".join(annotations),
                transform=ax.transAxes, va="bottom", ha="left", fontsize=6,
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "white",
                      "alpha": 0.85, "edgecolor": "0.75"})
        if k_hz_per_v:
            top = ax.secondary_xaxis(
                "top",
                functions=(lambda hz: (hz - intercept_hz) / k_hz_per_v,
                           lambda volt: volt * k_hz_per_v + intercept_hz),
            )
            top.set_xlabel("Control amplitude (V)", fontsize=6.5)
            top.tick_params(labelsize=6.5)
        if j == columns[0]:
            style_legend(ax, loc="upper right", fontsize=6.5)
    for ax in axes.ravel()[columns.size:]:
        ax.set_visible(False)
    save_plot(fig, results/"lorentzian_line_shapes.png")


def plot_controlled_uncontrolled(results, frequency, controlled_on, controlled_off,
                                 s_truth, uncontrolled_on, uncontrolled_off,
                                 controlled_mask, uncontrolled_mask,
                                 controlled_ylabel=r"$S_\beta$ (Hz$^2$/Hz)"):
    """两态各自显示验收通过的谱；只有差谱和定量比值需要共同有效掩码。"""
    x_khz = np.asarray(frequency, float)/1000
    controlled_mask = np.asarray(controlled_mask, bool)
    uncontrolled_mask = np.asarray(uncontrolled_mask, bool)
    fig, axes = new_figure(kind="wide", height_mm=65, ncols=2, constrained_layout=True)
    axes[0].semilogy(x_khz, controlled_off, ".",
                   ms=2.5, color=COLOR_GRAY,
                   label="Injection OFF")
    axes[0].semilogy(x_khz, controlled_on, ".",
                   ms=2.5, color=COLOR_OPTIMAL, label="Injection ON")
    if s_truth is not None:
        truth = np.asarray(s_truth, float)
        truth_valid = controlled_mask & (truth > 0)
        axes[0].semilogy(x_khz[truth_valid], truth[truth_valid], "-", lw=1.1, color=COLOR_TRAD,
                       label="Injection truth")
    format_axis(axes[0], xlabel="Frequency (kHz)", ylabel=controlled_ylabel)
    axes[0].set_title("Controlled noise: injection OFF vs ON")
    axes[0].grid(True, which="major", alpha=0.22)
    style_legend(axes[0], loc="lower left")
    axes[1].semilogy(x_khz, uncontrolled_off, ".",
                     ms=2.5, color=COLOR_GRAY, label="Injection OFF")
    axes[1].semilogy(x_khz, uncontrolled_on, ".",
                     ms=2.5, color=COLOR_GREEN, label="Injection ON")
    format_axis(axes[1], xlabel="Frequency (kHz)", ylabel=r"$N_{S_1}$ (V$^2$/Hz)")
    axes[1].set_title("Resolved background: injection OFF vs ON")
    axes[1].grid(True, which="major", alpha=0.22)
    style_legend(axes[1], loc="lower left")
    save_plot(fig, results/"controlled_uncontrolled_comparison.png")


def plot_comparison(results, frequency, s_meas, s_truth, criteria_mask, good, pass_low, pass_high):
    """测得谱 vs 真值谱；比值面板区分参与判据的带内点与带外诊断点。"""
    fig, axes = new_figure(kind="wide", height_mm=65, ncols=2, constrained_layout=True)
    f = np.asarray(frequency, float)/1000
    good = np.asarray(good, bool)
    criteria_mask = np.asarray(criteria_mask, bool)
    measured_magnitude = np.abs(np.asarray(s_meas, float))
    visible = good & np.isfinite(measured_magnitude) & (measured_magnitude > 0)
    truth = np.asarray(s_truth, float)
    truth_visible = good & np.isfinite(truth) & (truth > 0)
    axes[0].plot(f[truth_visible], truth[truth_visible], color=COLOR_TRAD, lw=1.1,
                   label="Truth (K_Z chain)")
    axes[0].plot(f[visible], measured_magnitude[visible], ".", ms=2, color=COLOR_OPTIMAL,
                   label=r"Measured $|S_\beta|$ (dispersion gain)")
    axes[0].set_yscale("log")
    format_axis(axes[0], xlabel="Frequency (kHz)", ylabel=r"$|S_\beta|$ (Hz$^2$/Hz)")
    axes[0].set_title("Measured vs truth spectrum")
    axes[0].grid(True, which="major", alpha=0.22)
    style_legend(axes[0], loc="lower left")
    # 带外真值趋近零，比值无意义；仅展示带内有符号比值。
    ratio = s_meas[criteria_mask]/s_truth[criteria_mask]
    axes[1].plot(f[criteria_mask], ratio, ".", ms=2.5, color=COLOR_OPTIMAL,
                     label="Inside injection band (criteria)")
    axes[1].axhline(1.0, color="k", lw=0.8)
    axes[1].axhspan(pass_low, pass_high, color=COLOR_GREEN, alpha=0.15, lw=0)
    median = float(np.median(ratio))
    axes[1].axhline(median, color=COLOR_TRAD, ls="--", lw=1,
                    label=f"median = {median:.3f}")
    format_axis(axes[1], xlabel="Frequency (kHz)", ylabel="Measured / Truth")
    axes[1].set_title("Ratio and acceptance band")
    axes[1].axhline(0, color=COLOR_GRAY, lw=.6)
    axes[1].grid(True, which="major", alpha=0.22)
    style_legend(axes[1], loc="best")
    save_plot(fig, results/"measured_vs_truth.png")


def plot_dispersion(results, offsets, y_means, curve=None,
                    slope=None, intercept=None, r_squared=None):
    """画色散线形与拟合结果；旧运行目录只有中心段线性拟合结果时退化绘制。"""
    fig, ax = new_figure(kind="wide", height_mm=65)
    ax.plot(offsets*1000, y_means, "o", ms=3.5, color=COLOR_GRAY, label="Demod Y vs Z bias")
    if curve is not None:
        axis = np.linspace(offsets.min(), offsets.max(), 200)
        ax.plot(axis*1000, curve(axis), "-", color=COLOR_OPTIMAL, lw=1.3,
                label="Dispersion line fit")
        if slope is not None and r_squared is not None and np.isfinite(r_squared):
            ax.text(0.03, 0.95,
                    f"dY/dV = {slope:.4g} V/V\nR² = {r_squared:.4f}",
                    transform=ax.transAxes, va="top", ha="left", fontsize=8,
                    bbox={"boxstyle": "round,pad=0.3", "facecolor": "white",
                          "alpha": 0.8, "edgecolor": "0.7"})
    elif slope is not None:
        # 兼容旧运行目录：只有中心段线性拟合的斜率与截距。
        center = np.abs(offsets) <= (np.max(np.abs(offsets)) / 2.0)
        axis = np.linspace(offsets[center].min(), offsets[center].max(), 50)
        ax.plot(axis*1000, slope*axis + intercept, "--", color=COLOR_TRAD,
                label="Center linear fit (legacy)")
    format_axis(ax, xlabel="Z bias offset (mV)", ylabel="Demodulator Y (V)")
    style_legend(ax, loc="best")
    save_plot(fig, results/"dispersion_scan.png")


def save_known_noise_preview(results: Path, run_dir: Path, known: dict, bin_width_hz: float) -> None:
    """保存实际任意波、目标谱和由该波形计算的 Welch 谱。"""
    waveform_path = (run_dir / str(known.get("waveform_file", "raw/known_noise_waveform.npy"))).resolve()
    if run_dir.resolve() not in waveform_path.parents:
        raise ValueError("噪声波形路径超出运行目录")
    normalized = np.load(waveform_path, allow_pickle=False)
    if normalized.ndim != 1 or not np.isfinite(normalized).all():
        raise ValueError("保存的噪声波形无效")
    sample_rate = float(known["sample_rate_sa_s"])
    repeat_freq = float(known.get("repeat_frequency_hz", sample_rate / normalized.size))
    line_frequencies = np.fft.rfftfreq(normalized.size, 1.0 / sample_rate)
    design_psd = np.zeros(line_frequencies.size)
    bands = [
        (float(b["start_hz"]), float(b["stop_hz"]), float(b["design_psd_v2_per_hz"]))
        for b in known.get("bands", [])
    ]
    for start, stop, level in bands:
        design_psd[(line_frequencies >= start) & (line_frequencies <= stop)] = level
    from .generation import KnownNoiseWaveform
    waveform = KnownNoiseWaveform(
        normalized=normalized, design_psd_v2_per_hz=design_psd,
        line_frequencies_hz=line_frequencies, sample_rate_sa_s=sample_rate,
        line_spacing_hz=repeat_freq, design_peak_v=float(known["design_peak_v"]),
        rms_v=float(known["rms_v"]), seed=int(known.get("seed", 0)),
    )
    preview = calculate_noise_preview(
        waveform, amplitude_vpp=float(known["amplitude_vpp"]), bands=bands,
        bin_width_hz=bin_width_hz,
    )
    np.savez_compressed(
        results / "known_noise_preview.npz",
        time_s=preview["time_s"], voltage_v=preview["voltage_v"],
        frequency_hz=preview["frequency_hz"],
        target_psd_v2_per_hz=preview["target_psd_v2_per_hz"],
        calculated_psd_v2_per_hz=preview["calculated_psd_v2_per_hz"],
        nperseg=preview["nperseg"], realized_psd_scale=preview["realized_psd_scale"],
    )
    fig, axes = new_figure(kind="wide", height_mm=110, nrows=2)
    axes[0].plot(preview["time_s"] * 1000, preview["voltage_v"], color="tab:blue", lw=0.7)
    format_axis(axes[0], xlabel="Time (ms)", ylabel="Voltage (V)")
    axes[0].set_title("Known noise arbitrary waveform")
    positive = preview["frequency_hz"] > 0
    target_positive = positive & (preview["target_psd_v2_per_hz"] > 0)
    calculated_positive = positive & (preview["calculated_psd_v2_per_hz"] > 0)
    axes[1].loglog(preview["frequency_hz"][target_positive],
                   preview["target_psd_v2_per_hz"][target_positive],
                   color=COLOR_TRAD, lw=1.1, label="Target PSD")
    axes[1].loglog(preview["frequency_hz"][calculated_positive],
                   preview["calculated_psd_v2_per_hz"][calculated_positive],
                   color=COLOR_OPTIMAL, lw=1.1, label="Welch PSD from waveform")
    format_axis(axes[1], xlabel="Frequency (Hz)", ylabel=r"PSD (V$^2$/Hz)")
    axes[1].set_title("Target and calculated noise spectrum")
    axes[1].grid(True, which="major", alpha=0.22)
    style_legend(axes[1], loc="best")
    save_plot(fig, results / "known_noise_preview.png")


