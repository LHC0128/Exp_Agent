"""XY 噪声谱离线分析；不连接硬件，质量失败仍保存诊断。"""
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

from ...experiment_runtime import check_cancelled, runtime_run_dir
from ...common import WorkflowCancelled
from ...plotting import (
    COLOR_CYAN,
    COLOR_GRAY,
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from ..mx_main_field_scope_noise_spectrum.global_analysis import (
    fit_global_noise_separation, project_global_noise_separation,
)
from .models import NoiseSpectrumXYParams, welch_settings
from .processing import locate_ridge, fit_local_spectra

ANALYSIS_VERSION = "xy-ridge-local-v3"


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def compute_psd(run_dir, config, options):
    """按波形编号对齐控制轴；不读取可能过期的 PSD 缓存。"""
    daq = config["hf2_daq"]
    rate = float(daq.get("actual_rate_Sa_s", float("nan")))
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError("缺少有效的硬件实际采样率，不能从请求值猜测频率轴")
    scan = config["scan_params"]
    if "xy_envelope_voltage_V" in scan:
        voltage = np.asarray(scan["xy_envelope_voltage_V"], float)
    else:
        voltage = np.linspace(scan["XY_AMP_START_V"], scan["XY_AMP_STOP_V"], scan["XY_AMP_POINTS"])
    if not np.isfinite(voltage).all() or np.any(np.diff(voltage) <= 0):
        raise ValueError("控制电压轴必须有限且严格递增")
    legacy = int(config.get("schema_version", 1)) < 3
    settings = welch_settings(rate, float(daq["DAQ_duration_s"]), options["bin_width_hz"],
                              minimum_segment=int(daq["nperseg"]) if legacy else 2)
    nperseg = settings["nperseg"]
    frequency = np.fft.rfftfreq(nperseg, 1/rate)
    matrix = np.full((voltage.size, frequency.size), np.nan)
    valid_rows, invalid_rows, raw_files, segment_counts = [], [], [], []
    for i in range(voltage.size):
        check_cancelled()
        path = run_dir/"raw"/f"waveform_C{i:04d}.npy"
        if not path.exists():
            invalid_rows.append({"index": i, "reason": "missing_waveform"})
            continue
        wave = np.load(path, allow_pickle=False)
        raw_files.append({"name": path.name, "bytes": path.stat().st_size,
                          "mtime_ns": path.stat().st_mtime_ns, "samples": int(wave.size)})
        if wave.ndim != 1 or wave.size < nperseg or not np.isfinite(wave).all() or np.std(wave) == 0:
            invalid_rows.append({"index": i, "reason": "invalid_or_short_waveform"})
            continue
        _, psd = welch(wave, fs=rate, nperseg=nperseg, noverlap=nperseg//2,
                       window="hann", detrend="constant", scaling="density")
        if np.any(psd <= 0):
            invalid_rows.append({"index": i, "reason": "nonpositive_psd"})
            continue
        matrix[i] = psd
        valid_rows.append(i)
        segments = 1 + (wave.size - nperseg) // (nperseg - nperseg//2)
        raw_files[-1]["welch_segments"] = int(segments)
        segment_counts.append(int(segments))
    return matrix, frequency, voltage, np.asarray(valid_rows, int), dict(
        actual_rate_sa_s=rate, nperseg=nperseg, bin_width_hz=rate/nperseg,
        window="hann", noverlap=nperseg//2, invalid_rows=invalid_rows, raw_files=raw_files,
        segment_rule="legacy_max" if legacy else "frequency_bin_width",
        segments_per_point_range=[min(segment_counts), max(segment_counts)] if segment_counts else None,
    )


def save_plot(fig, path):
    # 统一绘图默认已使用 constrained layout，不重复切换布局引擎。
    # save_figure 默认成对导出并关闭画布，无需再调用 pyplot。
    save_figure(fig, path)


def _interpolate_for_plot(x, y, valid):
    """仅为图形连接内部缺口，不改变保存的定量数组。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    valid = np.asarray(valid, bool) & np.isfinite(x) & np.isfinite(y) & (y > 0)
    plotted = np.full_like(y, np.nan, dtype=float)
    if valid.sum() >= 2:
        valid_indices = np.flatnonzero(valid)
        plotted[valid] = y[valid]
        missing = (~valid
                   & (np.arange(y.size) > valid_indices[0])
                   & (np.arange(y.size) < valid_indices[-1]))
        # 在 log-y 图上做对数线性插值，避免跨数量级时产生不自然的折线。
        plotted[missing] = np.exp(np.interp(x[missing], x[valid], np.log(y[valid])))
    return plotted, valid


def plot_ridge(results, matrix, frequency, voltage, ridge):
    fig, axes = new_figure(kind="wide", height_mm=65, ncols=2)
    for ax, data in zip(axes, [np.log10(matrix), ridge["contrast"]]):
        lo, hi = np.percentile(data, [5, 98])
        ax.pcolormesh(frequency / 1000, voltage, data, shading="auto", cmap="inferno",
                      vmin=lo, vmax=hi, rasterized=True)
        format_axis(ax, xlabel="Frequency (kHz)", ylabel="Sine peak voltage (V)")
        ax.plot((ridge["k"] * voltage + ridge["b"]) / 1000, voltage,
                "--", color=COLOR_CYAN, lw=1.1, label="Calibrated ridge")
        style_legend(ax, loc="upper left")
    axes[1].scatter(ridge["f_cal"] / 1000, ridge["V_cal"], s=5, color=COLOR_GRAY,
                    label="Accepted peaks")
    style_legend(axes[1], loc="upper left")
    save_plot(fig, results/"calibration.png")
    fig, ax = new_figure(kind="wide", height_mm=65)
    data = np.log10(matrix)
    ax.pcolormesh(frequency / 1000, ridge["omega_ctrl"] / 1000, data, shading="auto",
                  cmap="inferno", vmin=np.percentile(data, 5),
                  vmax=np.percentile(data, 98), rasterized=True)
    ax.plot(frequency / 1000, frequency / 1000, "--", color=COLOR_CYAN,
            lw=1.1, label=r"Reference: $f = \Omega_{\mathrm{Ctrl}}$")
    format_axis(ax, xlabel="Frequency (kHz)", ylabel="Calibrated control frequency (kHz)")
    ax.set_ylim(ridge["omega_ctrl"][0] / 1000, ridge["omega_ctrl"][-1] / 1000)
    style_legend(ax, loc="upper left")
    save_plot(fig, results/"noise_spectrum_2d.png")


def plot_fit(results, matrix, frequency, control, fit):
    x_khz = np.asarray(frequency, float) / 1000
    good = np.asarray(fit["fit_mask"], bool)
    controlled, controlled_valid = _interpolate_for_plot(x_khz, fit["S_beta"], good)
    background, background_valid = _interpolate_for_plot(x_khz, fit["N_S1"], good)
    fig, ax_control = new_figure(kind="wide", height_mm=65)
    ax_background = ax_control.twinx()
    control_color, background_color = COLOR_OPTIMAL, COLOR_TRAD
    ax_control.semilogy(x_khz, controlled, color=control_color, lw=1.3,
                        label="Controlled response A (interpolated)")
    ax_control.semilogy(x_khz[controlled_valid], fit["S_beta"][controlled_valid],
                        "o", ms=3.5, color=control_color, label="Controlled accepted points")
    ax_background.semilogy(x_khz, background, color=background_color, lw=1.3,
                           label="Uncontrolled/background PSD (interpolated)")
    ax_background.semilogy(x_khz[background_valid], fit["N_S1"][background_valid],
                           "s", ms=3.5, color=background_color, label="Uncontrolled accepted points")
    format_axis(ax_control, xlabel="Frequency (kHz)", ylabel="Controlled response coefficient A (V²·Hz)")
    ax_background.set_ylabel("Uncontrolled/background PSD N_S1 (V²/Hz)")
    ax_control.grid(True, which="major", alpha=0.22)
    handles, labels = ax_control.get_legend_handles_labels()
    right_handles, right_labels = ax_background.get_legend_handles_labels()
    ax_control.legend(handles + right_handles, labels + right_labels, loc="best", frameon=False)
    save_plot(fig, results/"noise_spectra_extracted.png")
    fig, axes = new_figure(kind="wide", height_mm=110, nrows=2, ncols=2)
    good = np.flatnonzero(fit["fit_mask"])
    if good.size:
        indices = good[np.linspace(0,good.size-1,4).astype(int)]
        for ax,j in zip(axes.flat,indices):
            ax.semilogy(control/1000,matrix[:,j],".",label="Measured")
            ax.semilogy(control/1000,fit["model_matrix"][:,j],label="Local fit")
            format_axis(ax, xlabel="Control frequency (kHz)", ylabel="PSD (V²/Hz)")
            ax.text(.03, .95, f"f = {frequency[j] / 1000:.2f} kHz",
                    transform=ax.transAxes, va="top")
            style_legend(ax, loc="best")
    save_plot(fig, results/"psd_column_diagnostics.png")
    fig, axes = new_figure(kind="wide", height_mm=110, nrows=2, ncols=2)
    frac = (matrix-fit["model_matrix"])/fit["model_matrix"]
    axes[0,0].pcolormesh(frequency/1000,control/1000,np.ma.masked_invalid(frac),
                         shading="auto",cmap="RdBu_r",vmin=-1,vmax=1,rasterized=True)
    format_axis(axes[0, 0], xlabel="Frequency (kHz)", ylabel="Control frequency (kHz)")
    axes[0,1].plot(frequency/1000,fit["median_relative_residual"],label="Median residual")
    axes[0,1].plot(frequency/1000,fit["holdout_relative_error"],label="Holdout error")
    format_axis(axes[0, 1], xlabel="Frequency (kHz)", ylabel="Relative error")
    axes[0, 1].set_ylim(0, 1)
    style_legend(axes[0, 1], loc="best")
    for ax,i,label in [(axes[1,0],0,"Linewidth (Hz)"),(axes[1,1],3,"Frequency offset (Hz)")]:
        ax.plot(frequency[good]/1000,fit["popt"][good,i],".",ms=2)
        format_axis(ax, xlabel="Frequency (kHz)", ylabel=label)
    save_plot(fig,results/"fit_quality.png")


def analyze(run_dir: Path):
    run_dir = Path(run_dir).resolve()
    config = yaml.safe_load((run_dir/"experiment_config.yaml").read_text(encoding="utf-8"))
    defaults = NoiseSpectrumXYParams()
    saved = config.get("parameters",{})
    options = {key: float(saved.get("ANALYSIS_"+key.upper(),getattr(defaults,"analysis_"+key)))
               for key in ("bin_width_hz","frequency_min_hz","frequency_max_hz","fit_half_width_hz")}
    if any(not np.isfinite(v) or v < 0 for v in options.values()) or options["bin_width_hz"] == 0 or options["fit_half_width_hz"] == 0 or options["frequency_min_hz"] >= options["frequency_max_hz"]:
        raise ValueError("分析参数范围无效")
    results = run_dir/"results"
    results.mkdir(exist_ok=True)
    # 只归档当前 results 顶层产物；原始数据、配置和已有诊断子目录不移动。
    existing = [p for p in results.iterdir() if p.is_file()]
    backup = None
    if existing:
        backup = results/("before_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        for path in existing:
            shutil.move(str(path),str(backup/path.name))
    summary = dict(analysis_version=ANALYSIS_VERSION, status="running", options=options,
                   plotting=dict(profile="paper", figure_kind="wide",
                                  standard_height_mm=65, diagnostic_height_mm=110,
                                  paired_exports=True, dpi=300),
                   backup_directory=backup.name if backup else None, warnings=[])
    write_json(results/"analysis_summary.json",summary)
    set_plot_style("paper")
    try:
        matrix, frequency, voltage, rows, psd_info = compute_psd(run_dir,config,options)
        summary["psd"] = psd_info
        np.savez(results/"psd_matrix.npz",psd_matrix=matrix,freq_axis=frequency,amplitudes=voltage,
                 actual_rate=psd_info["actual_rate_sa_s"],nperseg=psd_info["nperseg"],valid_rows=rows)
        band = (frequency>=options["frequency_min_hz"]) & (frequency<=options["frequency_max_hz"])
        f, v, data = frequency[band], voltage[rows],matrix[rows][:,band]
        print(f"Welch: {len(rows)}/{len(voltage)} 点, nperseg={psd_info['nperseg']}, df={psd_info['bin_width_hz']:.3f} Hz",flush=True)
        ridge = locate_ridge(data,f,v)
        np.savez(results/"calibration.npz",**ridge,amplitudes=v,freq_axis=f)
        plot_ridge(results,data,f,v,ridge)
        summary["calibration"] = {key: float(ridge[key]) for key in ("k","b","residual_std_hz","coverage")}
        summary["calibration"]["support_points"] = len(ridge["V_cal"])
        summary["calibration"]["half_slopes"] = ridge["half_slopes"].tolist()
        support = ridge["k"]*ridge["V_cal"]+ridge["b"]
        summary["calibration"]["support_frequency_hz"] = [float(support.min()),float(support.max())]
        print("移动峰标定:",summary["calibration"],flush=True)
        fit = fit_local_spectra(data,f,ridge["omega_ctrl"],ridge["spur_mask"],
                                options["fit_half_width_hz"],(support.min(),support.max()))
        good = fit["fit_mask"]
        np.savez(results/"popt_fit.npz",**{k: val for k,val in fit.items() if k not in ("model_matrix","S_beta","N_S1")},
                 freq_axis=f,param_names=["gamma","Amp","D","dw"])
        np.savez(results/"noise_spectra.npz",S_beta=fit["S_beta"],N_S1=fit["N_S1"],freq_axis=f,
                 amplitudes=v,omega_ctrl=ridge["omega_ctrl"],fit_mask=good,quantitative_valid_mask=good,
                 interpolated_mask=fit["interpolated_mask"],rejection_reason=fit["rejection_reason"],
                 coefficient_semantics="A = C*S_beta; uncalibrated response coefficient, V^2 Hz")
        np.savetxt(results/"noise_spectra.csv",np.column_stack((f,fit["S_beta"],fit["N_S1"],good.astype(int))),
                   delimiter=",",header="frequency_Hz,response_coefficient_A_V2_Hz,N_S1_V2_per_Hz,valid",comments="")
        np.savez(results/"fit_diagnostics.npz",frequency_hz=f,control_hz=ridge["omega_ctrl"],
                 measured=data,model=fit["model_matrix"],spur_mask=ridge["spur_mask"])
        plot_fit(results,data,f,ridge["omega_ctrl"],fit)
        reasons,counts=np.unique(fit["rejection_reason"],return_counts=True)
        summary["fit"] = dict(valid_points=int(good.sum()),total_points=len(f),
                              rejection_counts=dict(zip(reasons.tolist(),counts.tolist())),
                              valid_frequency_extent_hz=[float(f[good].min()),float(f[good].max())] if good.any() else None,
                              median_holdout_error=float(np.median(fit["holdout_relative_error"][good])) if good.any() else None)
        # Mx 二维共同线宽模型作为对照，不能覆盖局部质量掩码或填充失败点。
        core = (~ridge["spur_mask"]) & (f>=support.min()+options["fit_half_width_hz"]) & (f<=support.max()-options["fit_half_width_hz"])
        indices = np.flatnonzero(core)
        if indices.size >= 40:
            print("计算 Mx 二维模型对照（不替换局部验收结果）...",flush=True)
            sampled = indices[::max(1,int(np.ceil(indices.size/350)))]
            check_cancelled()
            global_fit = fit_global_noise_separation(data[:,sampled],f[sampled],ridge["omega_ctrl"],
                       background_margin_hz=options["fit_half_width_hz"],cv_folds=3,alternating_iterations=2)
            check_cancelled()
            comparison = project_global_noise_separation(data[:,indices],f[indices],ridge["omega_ctrl"],global_fit,cv_folds=3)
            np.savez(results/"global_comparison.npz",frequency_hz=f[indices],model=comparison.model_matrix,
                     background_profile=comparison.background_profile,gamma_hz=comparison.gamma_hz,
                     residual=comparison.residual_matrix,conditional_valid_mask=comparison.quantitative_valid_mask)
            summary["global_comparison"] = dict(gamma_hz=float(comparison.gamma_hz),
                optimizer_success=bool(global_fit.optimizer_success),
                median_relative_residual=float(np.median(comparison.median_absolute_fractional_residual)),
                note="条件交叉验证仅检验谱系数；共同线宽与背景由全数据估计，不作为独立定量验收")
        summary["warnings"].append("S_beta 历史键保存的是未去除增益因子的响应系数 A，非绝对磁噪声；谱内 NaN 为未通过质量验收，禁止外推填补。")
        summary["status"] = "completed" if good.any() else "quality_failed"
        if not good.any():
            summary["error"] = "没有频率点通过局部拟合质量验收"
        print("分析完成:",summary["fit"],flush=True)
    except WorkflowCancelled as exc:
        summary.update(status="cancelled",error=str(exc))
        raise
    except Exception as exc:
        summary.update(status="quality_failed" if isinstance(exc,ValueError) else "failed",error=str(exc))
        raise
    finally:
        write_json(results/"analysis_summary.json",summary)
    return summary


def main():
    summary=analyze(runtime_run_dir())
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
