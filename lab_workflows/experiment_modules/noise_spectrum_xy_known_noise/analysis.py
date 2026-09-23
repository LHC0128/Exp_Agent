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
from ..noise_spectrum_xy.processing import fit_local_spectra, locate_ridge
from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from .dispersion import resolve_dispersion
from .generation import calculate_noise_preview, load_z_calibration, load_z_coil_transfer
from .models import NoiseSpectrumXYKnownNoiseParams

ANALYSIS_VERSION = "known-noise-full-range-v3"


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
    ax.plot((ridge["k"]*voltage+ridge["b"])/1000, voltage, "--", color=COLOR_CYAN, lw=1.1,
            label="Calibrated ridge")
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
    ax.plot(frequency[good]/1000, a_diff[good], ".", ms=2.5, color=COLOR_OPTIMAL,
                label="A(ON) - A(OFF)")
    finite = np.abs(a_diff[good & np.isfinite(a_diff)])
    ax.set_yscale("symlog", linthresh=max(float(np.percentile(finite, 10)), 1e-30) if finite.size else 1e-30)
    ax.axhline(0, color=COLOR_GRAY, lw=.6)
    format_axis(ax, xlabel="Frequency (kHz)", ylabel="Differential response coefficient A (V²·Hz)")
    ax.set_title("Differential response coefficient")
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
                     intercept_hz):
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
        ax.semilogy(axis, lorentzian_vs_control(axis, p_on[0], p_on[1], p_on[2], p_on[3],
                                                frequency[j]),
                    "-", color=COLOR_OPTIMAL, lw=1.0, label="ON fit")
        if np.isfinite(popt_off[j]).all():
            p_off = popt_off[j]
            ax.semilogy(axis, lorentzian_vs_control(axis, p_off[0], p_off[1], p_off[2],
                                                    p_off[3], frequency[j]),
                        "--", color=COLOR_GRAY, lw=1.0, label="OFF fit")
        ax.axvline(frequency[j], color="k", ls=":", lw=0.8)
        format_axis(ax, xlabel="Control frequency (Hz)", ylabel=r"PSD (V$^2$/Hz)")
        ax.set_title(f"f = {frequency[j]/1000:.2f} kHz", fontsize=8)
        # 验收结论放在图内，避免与顶轴标签抢空间。
        ax.text(0.03, 0.04,
                f"ON  {reasons_on[j]}\nOFF {reasons_off[j]}\n"
                f"$\\gamma$ = {p_on[0]:.0f} Hz",
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
                                 controlled_mask, uncontrolled_mask):
    """同一全范围模型分出的磁响应与背景；背景需额外通过远端可辨识性验收。"""
    x_khz = np.asarray(frequency, float)/1000
    controlled_mask = np.asarray(controlled_mask, bool)
    uncontrolled_mask = np.asarray(uncontrolled_mask, bool)
    fig, axes = new_figure(kind="wide", height_mm=65, ncols=2, constrained_layout=True)
    axes[0].semilogy(x_khz[controlled_mask], controlled_off[controlled_mask], ".",
                   ms=2.5, color=COLOR_GRAY,
                   label="Injection OFF")
    axes[0].semilogy(x_khz[controlled_mask], controlled_on[controlled_mask], ".",
                   ms=2.5, color=COLOR_OPTIMAL, label="Injection ON")
    truth = np.asarray(s_truth, float)
    truth_valid = controlled_mask & (truth > 0)
    axes[0].semilogy(x_khz[truth_valid], truth[truth_valid], "-", lw=1.1, color=COLOR_TRAD,
                   label="Injection truth")
    format_axis(axes[0], xlabel="Frequency (kHz)", ylabel=r"$S_\beta$ (Hz$^2$/Hz)")
    axes[0].set_title("Controlled noise: injection OFF vs ON")
    axes[0].grid(True, which="major", alpha=0.22)
    style_legend(axes[0], loc="lower left")
    axes[1].semilogy(x_khz[uncontrolled_mask], uncontrolled_off[uncontrolled_mask], ".",
                     ms=2.5, color=COLOR_GRAY, label="Injection OFF")
    axes[1].semilogy(x_khz[uncontrolled_mask], uncontrolled_on[uncontrolled_mask], ".",
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
    axes[0].plot(f[good], s_truth[good], color=COLOR_TRAD, lw=1.1,
                   label="Truth (K_Z x |H| chain)")
    axes[0].plot(f[good], s_meas[good], ".", ms=2, color=COLOR_OPTIMAL,
                   label="Measured (dispersion gain)")
    axes[0].set_yscale("symlog", linthresh=max(float(np.median(s_truth[criteria_mask]))*.01, 1e-30))
    format_axis(axes[0], xlabel="Frequency (kHz)", ylabel=r"$S_\beta$ (Hz$^2$/Hz)")
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


def plot_dispersion(results, offsets, y_means, curve=None, fit_mask=None,
                    slope=None, intercept=None, r_squared=None):
    """画色散线形与拟合结果；旧运行目录只有中心段线性拟合结果时退化绘制。"""
    fig, ax = new_figure(kind="wide", height_mm=65)
    ax.plot(offsets*1000, y_means, "o", ms=3.5, color=COLOR_GRAY, label="Demod Y vs Z bias")
    if curve is not None:
        used = np.ones(offsets.size, bool) if fit_mask is None else np.asarray(fit_mask, bool)
        axis = np.linspace(offsets[used].min(), offsets[used].max(), 200)
        ax.plot(axis*1000, curve(axis), "-", color=COLOR_OPTIMAL, lw=1.3,
                label="Dispersion line fit")
        if not used.all():
            ax.plot(offsets[~used]*1000, y_means[~used], "x", ms=4, color=COLOR_TRAD,
                    label="Excluded edge points")
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


def analyze(run_dir: Path):
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir/"experiment_config.yaml").read_text(encoding="utf-8"))
    if config.get("experiment_id") != "noise-spectrum-xy-known-noise":
        raise ValueError("运行目录不是 XY 控制已知噪声注入实验")
    defaults = NoiseSpectrumXYKnownNoiseParams()
    saved = config.get("parameters", {})
    options = {key: float(saved.get("ANALYSIS_"+key.upper(), getattr(defaults, "analysis_"+key)))
               for key in ("bin_width_hz", "frequency_min_hz", "frequency_max_hz",
                           "fit_half_width_hz")}
    pass_low = float(saved.get("RATIO_PASS_LOW", 0.5))
    pass_high = float(saved.get("RATIO_PASS_HIGH", 2.0))
    if any(not np.isfinite(v) or v < 0 for v in options.values()) or options["bin_width_hz"] == 0 \
            or options["fit_half_width_hz"] == 0 \
            or options["frequency_min_hz"] >= options["frequency_max_hz"]:
        raise ValueError("分析参数范围无效")
    known = config.get("known_noise") or {}
    truth_cfg = config.get("truth_chain") or {}
    dispersion = config.get("dispersion") or {}
    if not known or not truth_cfg or not dispersion:
        raise ValueError("运行配置缺少 known_noise/truth_chain/dispersion 段，无法定量比较")
    # 增益基准从 raw/ 重拟合（色散线形零交叉点斜率），不使用采集阶段写下的值。
    dispersion_fit = resolve_dispersion(run_dir, config)
    slope_v_per_hz = float(dispersion_fit["slope_v_per_hz"])
    if not np.isfinite(slope_v_per_hz) or slope_v_per_hz == 0:
        raise ValueError("色散斜率无效，无法进行增益换算")

    results = run_dir/"results"
    results.mkdir(exist_ok=True)
    existing = [p for p in results.iterdir() if p.is_file()]
    backup = None
    if existing:
        backup = results/("before_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in existing:
            shutil.move(str(path), str(backup/path.name))
    summary = dict(analysis_version=ANALYSIS_VERSION, status="running", options=options,
                   plotting=dict(profile="paper", figure_kind="wide",
                                 standard_height_mm=65, diagnostic_height_mm=110,
                                 paired_exports=True, dpi=300),
                   backup_directory=backup.name if backup else None, warnings=[])
    write_json(results/"analysis_summary.json", summary)
    set_plot_style("paper")
    try:
        psd_on, frequency, voltage, rows_on, psd_info = compute_psd_matrix(
            run_dir, config, options, "on")
        psd_off, _, _, rows_off, _ = compute_psd_matrix(run_dir, config, options, "off")
        save_known_noise_preview(results, run_dir, known, options["bin_width_hz"])
        rows = np.intersect1d(rows_on, rows_off)
        if rows.size < 20:
            raise ValueError(f"off/on 成对有效控制点不足 20 个（{rows.size}）")
        np.savez(results/"psd_matrices.npz", psd_on=psd_on, psd_off=psd_off,
                 freq_axis=frequency, amplitudes=voltage, paired_rows=rows,
                 actual_rate=psd_info["actual_rate_sa_s"], nperseg=psd_info["nperseg"])
        summary["psd"] = {**psd_info, "paired_points": int(rows.size)}
        differential = psd_on - psd_off
        average = 0.5*(psd_on + psd_off)
        print(f"Welch: {rows.size}/{voltage.size} 点成对, nperseg={psd_info['nperseg']}, "
              f"df={psd_info['bin_width_hz']:.3f} Hz", flush=True)

        band = (frequency >= options["frequency_min_hz"]) & (frequency <= options["frequency_max_hz"])
        f, v = frequency[band], voltage[rows]
        # 脊线定位用 OFF（本底）矩阵：注入是带限的，ON 侧脊线在带边饱和变形。
        data_off = psd_off[rows][:, band]
        data_on = psd_on[rows][:, band]
        cal_band = calibration_band(frequency, options, saved)
        ridge = locate_ridge(psd_off[rows][:, cal_band], frequency[cal_band], v)
        fit_spur = ridge["spur_mask"][band[cal_band]]
        np.savez(results/"calibration.npz", **ridge, amplitudes=v, freq_axis=frequency[cal_band])
        plot_two_dimensional(results, average[rows][:, band], f, v, ridge,
                             "noise_spectrum_2d.png", "Average ON/OFF PSD",
                             r"$\log_{10}$ PSD (V$^2$/Hz)")
        plot_two_dimensional(results, differential[rows][:, band], f, v, ridge,
                             "differential_psd.png", "Differential PSD (ON - OFF)",
                             r"Differential PSD (V$^2$/Hz)", diverging=True)
        summary["calibration"] = {key: float(ridge[key]) for key in ("k", "b", "residual_std_hz", "coverage")}
        summary["calibration"]["support_points"] = int(len(ridge["V_cal"]))
        summary["calibration"]["search_frequency_hz"] = [float(frequency[cal_band][0]), float(frequency[cal_band][-1])]
        support = ridge["k"]*ridge["V_cal"]+ridge["b"]
        summary["calibration"]["support_frequency_hz"] = [float(support.min()), float(support.max())]
        print("移动峰标定:", summary["calibration"], flush=True)

        fit_on = fit_local_spectra(data_on, f, ridge["omega_ctrl"], fit_spur,
                                   options["fit_half_width_hz"], (support.min(), support.max()),
                                   full_range=True)
        fit_off = fit_local_spectra(data_off, f, ridge["omega_ctrl"], fit_spur,
                                    options["fit_half_width_hz"], (support.min(), support.max()),
                                    full_range=True)
        # 同一模型给出全部参数；背景有独立可辨识性掩码，不强制 ON/OFF 相等。
        background_good = fit_on["background_fit_mask"] & fit_off["background_fit_mask"]
        good = fit_on["fit_mask"] & fit_off["fit_mask"]
        a_diff = fit_on["S_beta"] - fit_off["S_beta"]
        gamma = fit_on["popt"][:, 0]
        summary["fit_quality"] = dict(
            on=_reason_counts(fit_on["rejection_reason"]),
            off=_reason_counts(fit_off["rejection_reason"]),
            fit_half_width_hz=float(options["fit_half_width_hz"]),
            method="full_control_range",
            background_on=_reason_counts(fit_on["background_rejection_reason"]),
            background_off=_reason_counts(fit_off["background_rejection_reason"]),
            background_both_accepted=int(background_good.sum()),
            control_step_hz=float(np.median(np.diff(ridge["omega_ctrl"]))),
            both_accepted=int(good.sum()),
            either_rejected=int((~good).sum()),
        )
        plot_line_shapes(
            results, f, ridge["omega_ctrl"], data_on, data_off,
            fit_on["popt"], fit_off["popt"],
            fit_on["rejection_reason"], fit_off["rejection_reason"],
            select_line_shape_columns(f, fit_on["rejection_reason"] != "outside_ridge_support", 8),
            options["fit_half_width_hz"], ridge["k"], ridge["b"],
        )
        # 增益换算：静态极限连接 A/(gamma^2*sigma^2) 把 V^2 Hz 响应系数换算到 Hz^2/Hz。
        s_meas = a_diff / (gamma**2 * slope_v_per_hz**2)
        # 同一换算分别作用在 ON/OFF 总谱上，得到注入前后的可控噪声谱；
        # N_S1 是不可控噪声（含电子噪声）基线，不参与色散增益换算。
        controlled_on = fit_on["S_beta"] / (gamma**2 * slope_v_per_hz**2)
        controlled_off = fit_off["S_beta"] / (fit_off["popt"][:, 0]**2 * slope_v_per_hz**2)
        uncontrolled_on, uncontrolled_off = fit_on["N_S1"], fit_off["N_S1"]
        negative = good & (a_diff <= 0)
        # 负差仍是有效估计，不能以符号筛选造成向上偏差。
        np.savez(results/"popt_fit.npz",
                 popt_on=fit_on["popt"], perr_on=fit_on["perr"],
                 popt_off=fit_off["popt"], perr_off=fit_off["perr"],
                 rejection_on=fit_on["rejection_reason"], rejection_off=fit_off["rejection_reason"],
                 background_rejection_on=fit_on["background_rejection_reason"],
                 background_rejection_off=fit_off["background_rejection_reason"],
                 holdout_on=fit_on["holdout_relative_error"], holdout_off=fit_off["holdout_relative_error"],
                 tail_holdout_on=fit_on["tail_holdout_relative_error"],
                 tail_holdout_off=fit_off["tail_holdout_relative_error"],
                 background_anchor_points_on=fit_on["background_anchor_points"],
                 background_anchor_points_off=fit_off["background_anchor_points"],
                 freq_axis=f, param_names=["gamma", "Amp", "D", "dw"])
        plot_differential_response(results, f, a_diff, good)

        # 真值链：波形数值 PSD × (K_Z × H_norm)^2。
        project_root = find_project_root()
        waveform_path = (run_dir / str(known.get("waveform_file", "raw/known_noise_waveform.npy"))).resolve()
        if run_dir.resolve() not in waveform_path.parents:
            raise ValueError("噪声波形路径超出运行目录")
        normalized = np.load(waveform_path, allow_pickle=False)
        amplitude_vpp = float(known["amplitude_vpp"])
        sample_rate = float(known["sample_rate_sa_s"])
        voltage_wave = normalized * (amplitude_vpp / 2.0)
        wf_nperseg = max(2, int(np.ceil(sample_rate / options["bin_width_hz"])))
        wf_nperseg = min(wf_nperseg, voltage_wave.size)
        wf_freq, wf_psd = welch(voltage_wave, fs=sample_rate, nperseg=wf_nperseg,
                                noverlap=wf_nperseg//2, window="hann",
                                detrend="constant", scaling="density")
        # 去掉直流 bin 后做对数插值，避免 log(0)。
        usable = wf_freq > 0
        wf_freq, wf_psd = wf_freq[usable], wf_psd[usable]
        calibration = load_z_calibration(project_root, str(truth_cfg["z_calibration_run"]))
        transfer = load_z_coil_transfer(project_root, str(truth_cfg["z_tf_run"]))
        gain_chain = (calibration.k_hz_per_v * transfer.normalized_gain(wf_freq))**2
        s_truth_lines = wf_psd * gain_chain
        s_truth = np.exp(np.interp(np.log(f), np.log(wf_freq), np.log(s_truth_lines)))
        summary["truth_chain"] = dict(
            z_calibration_run=calibration.source_run,
            K_Z_Hz_per_V=calibration.k_hz_per_v,
            K_Z_uncertainty_Hz_per_V=calibration.uncertainty_hz_per_v,
            z_tf_run=transfer.source_run,
            tf_reference_hz=transfer.reference_hz,
            dispersion_slope_v_per_hz=slope_v_per_hz,
            gain_formula="S_meas = (A_on - A_off) / (gamma_on^2 * sigma^2); "
                         "S_truth = PSD(waveform*Vpp/2) * (K_Z*|H|/|H_ref|)^2",
        )

        ratio = np.full(f.size, np.nan)
        ratio[good] = s_meas[good]/s_truth[good]
        # 判据只在注入谱段内评估：带外真值只是波形数值 PSD 的本底，
        # 逐 bin 比值在那里没有物理意义。
        injection_mask = _injection_band_mask(f, known.get("bands"))
        band_valid = good & (s_truth > 0) & injection_mask
        median_ratio = float(np.median(ratio[band_valid])) if band_valid.any() else float("nan")
        coverage = float(band_valid.sum()/injection_mask.sum())
        criteria_pass = bool(coverage >= .5 and pass_low <= median_ratio <= pass_high)
        summary["comparison"] = dict(
            valid_points=int(good.sum()), negative_differential_points=int(negative.sum()),
            total_points=len(f), median_ratio=median_ratio,
            pass_low=pass_low, pass_high=pass_high, criteria_pass=criteria_pass,
            in_band_valid_points=int(band_valid.sum()),
            in_band_total_points=int(injection_mask.sum()), in_band_coverage=coverage,
            minimum_criteria_coverage=.5, signed_differences_retained=True,
            out_of_band_valid_points=int((good & ~injection_mask).sum()),
            injection_band_frequency_hz=[
                [float(band["start_hz"]), float(band["stop_hz"])]
                for band in (known.get("bands") or ())
            ],
            ratio_quartiles=[float(x) for x in np.percentile(ratio[band_valid], [25, 75])]
            if band_valid.any() else None,
            controlled_on_off_median_ratio=_median_ratio(controlled_on, controlled_off, good),
            uncontrolled_on_off_median_ratio=_median_ratio(
                uncontrolled_on, uncontrolled_off, background_good),
        )
        for label, selected_band in (("in_band", injection_mask), ("out_of_band", ~injection_mask)):
            bg = background_good & selected_band
            summary["comparison"][label+"_background"] = dict(
                resolved_points=int(bg.sum()), total_points=int(selected_band.sum()),
                median_ratio=_median_ratio(uncontrolled_on, uncontrolled_off, bg),
                median_tail_holdout_error_on=float(np.median(fit_on["tail_holdout_relative_error"][bg]))
                    if bg.any() else float("nan"),
            )
        if (background_good & injection_mask).sum() < .5*injection_mask.sum():
            summary["warnings"].append("注入带内不足一半频点能独立约束背景；磁响应尾部可能仍高于本底，不能判定不可控噪声是否变化。")
        if coverage < .5:
            summary["warnings"].append("注入带内有效覆盖不足 50%，不以少数通过点宣布谱验证通过。")
        summary["warnings"].append("全范围拟合不强制两态背景相等；背景需要远端锚点和整块尾部留出验证。参数误差为局部模型近似，不包括漂移和增益系统误差。")
        plot_controlled_uncontrolled(results, f, controlled_on, controlled_off, s_truth,
                                     uncontrolled_on, uncontrolled_off, good, background_good)
        np.savez(results/"noise_spectra.npz",
                 freq_axis=f, S_measured_hz2_per_hz=s_meas, S_truth_hz2_per_hz=s_truth,
                 ratio=ratio, A_on=fit_on["S_beta"], A_off=fit_off["S_beta"],
                 A_diff=a_diff, gamma_on=gamma, gamma_off=fit_off["popt"][:, 0],
                 S_beta_controlled_on_hz2_per_hz=controlled_on,
                 S_beta_controlled_off_hz2_per_hz=controlled_off,
                 N_S1_on=uncontrolled_on, N_S1_off=uncontrolled_off,
                 background_fit_mask=background_good,
                 background_fit_mask_on=fit_on["background_fit_mask"],
                 background_fit_mask_off=fit_off["background_fit_mask"],
                 fit_mask=good,
                 negative_differential_mask=negative,
                 quantitative_valid_mask=good,
                 criteria_band_mask=band_valid,
                 K_Z_hz_per_v=calibration.k_hz_per_v,
                 dispersion_slope_v_per_hz=slope_v_per_hz)
        np.savetxt(results/"noise_spectra.csv",
                   np.column_stack((f, s_meas, s_truth, ratio, good.astype(int),
                                    uncontrolled_on, uncontrolled_off, background_good.astype(int))),
                   delimiter=",",
                   header="frequency_Hz,S_measured_Hz2_per_Hz,S_truth_Hz2_per_Hz,ratio,valid,N_on_V2_per_Hz,N_off_V2_per_Hz,background_valid",
                   comments="")
        if band_valid.any():
            plot_comparison(results, f, s_meas, s_truth, band_valid, good, pass_low, pass_high)
        np.savez(results/"fit_diagnostics.npz", frequency_hz=f,
                 control_hz=ridge["omega_ctrl"], differential=differential[rows][:, band],
                 average=average[rows][:, band], psd_on=data_on, psd_off=data_off,
                 spur_mask=fit_spur)
        summary["dispersion"] = {
            key: dispersion_fit[key]
            for key in ("source", "method", "k_z_hz_per_v", "slope_v_per_hz",
                        "slope_v_per_v", "gamma_v", "gamma_hz", "center_v", "center_hz",
                        "r_squared", "rmse_v", "n_points", "is_valid",
                        "skip_start_points", "skip_end_points",
                        "recorded_slope_v_per_hz", "recorded_relative_difference",
                        "fallback_reason")
            if key in dispersion_fit
        }
        if dispersion_fit.get("rejection_reasons"):
            summary["dispersion"]["rejection_reasons"] = dispersion_fit["rejection_reasons"]
        offsets = dispersion_fit.get("offsets_v")
        if offsets is not None:
            # 有原始数据：画出离线重拟合的色散线形与被剔除端点。
            popt = np.asarray(dispersion_fit["popt"], float)
            plot_dispersion(
                results, offsets, np.asarray(dispersion_fit["y_mean"], float),
                curve=lambda axis, popt=popt: _dispersive(axis, *popt),
                fit_mask=np.asarray(dispersion_fit["fit_mask"], bool),
                slope=float(dispersion_fit["slope_v_per_v"]),
                r_squared=float(dispersion_fit["r_squared"]),
            )
        else:
            # 原始数据缺失：无法重画线形与拟合曲线。
            summary["warnings"].append(
                "原始色散数据不可用（" + str(dispersion_fit.get("fallback_reason"))
                + "），增益换算沿用采集记录的斜率 " + f"{slope_v_per_hz:.6g} V/Hz。"
            )
        if dispersion_fit.get("source") == "raw_refit" and not dispersion_fit.get("is_valid", True):
            summary["warnings"].append(
                "离线色散线形拟合质量未通过（"
                + "；".join(str(item) for item in dispersion_fit.get("rejection_reasons") or [])
                + "）；斜率仍用于增益换算，绝对值定量结论需先复核色散扫描。"
            )
        difference = dispersion_fit.get("recorded_relative_difference")
        if isinstance(difference, float) and np.isfinite(difference) and difference > 0.1:
            summary["warnings"].append(
                "离线色散线形拟合斜率与采集记录的中心段线性拟合相差 "
                f"{difference:.1%}（记录 {dispersion_fit['recorded_slope_v_per_hz']:.6g} V/Hz），"
                "绝对定量结果以本次重拟合的线形斜率为准。"
            )

        summary["warnings"].append(
            "增益换算 S_meas = (A_on - A_off) / (gamma_on^2*sigma^2) 的滤波函数形式与"
            " paper/optimal_control.tex 式(Sx_static)逐步核对一致；但色散斜率幅度"
            " sigma = 2*G*S2*P0/Gamma 的实验定义（dY/df）与理论量的幅度等价性仍属假设，"
            "建议首轮运行用已知幅度单频正弦注入自校准确认。"
        )
        summary["warnings"].append(
            "谱内 NaN 为未通过拟合/背景可辨识性验收；有效负差保留，禁止外推填补；"
            "truth 谱含线圈频响形状，带外与杂散保护带不可比。"
        )
        summary["status"] = "completed" if good.any() else "quality_failed"
        if not good.any():
            summary["error"] = "没有频率点通过全范围拟合质量验收"
        print("分析完成:", summary["comparison"], flush=True)
    except WorkflowCancelled as exc:
        summary.update(status="cancelled", error=str(exc))
        raise
    except Exception as exc:
        summary.update(status="quality_failed" if isinstance(exc, ValueError) else "failed", error=str(exc))
        raise
    finally:
        write_json(results/"analysis_summary.json", summary)
    return summary


def main():
    summary = analyze(runtime_run_dir())
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
