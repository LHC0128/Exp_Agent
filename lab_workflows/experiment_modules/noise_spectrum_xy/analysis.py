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
from .processing import SEPARATION_METHOD, locate_ridge, fit_local_spectra

ANALYSIS_VERSION = "xy-independent-background-v8"


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
    from sensitivity_analysis.acquisition import load_acquisition_rate
    files = sorted((run_dir / "raw").glob("waveform_C*.npy"))
    if files:
        rate = load_acquisition_rate(files[0], rate)
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
        if not np.isclose(load_acquisition_rate(path, rate), rate, rtol=1e-6):
            raise ValueError("同次扫描的实际采样率不一致，无法合并 PSD 频率轴")
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


def plot_noise_spectrum_2d(results, matrix, frequency, control_axis, *, calibrated=False):
    """先按扫描电压保存实测谱；只有标定通过才改用控制频率轴。"""
    data = np.ma.log10(np.ma.masked_less_equal(np.ma.masked_invalid(matrix), 0))
    if not frequency.size or not data.count():
        raise ValueError("所选分析频带没有有效 PSD，无法生成二维噪声谱")
    lo, hi = np.percentile(data.compressed(), [5, 98])
    axis = control_axis / 1000 if calibrated else control_axis
    fig, ax = new_figure(kind="wide", height_mm=65)
    mesh = ax.pcolormesh(frequency / 1000, axis, data, shading="auto",
                         cmap="inferno", vmin=lo, vmax=hi, rasterized=True)
    if calibrated:
        ax.plot(frequency / 1000, frequency / 1000, "--", color=COLOR_CYAN,
                lw=1.1, label=r"Reference: $f = \Omega_{\mathrm{Ctrl}}$")
        style_legend(ax, loc="upper left")
    else:
        ax.set_title("Measured PSD (uncalibrated control axis)")
    format_axis(ax, xlabel="Frequency (kHz)", ylabel=(
        "Calibrated control frequency (kHz)" if calibrated else "Sine peak voltage (V)"))
    ax.set_ylim(axis[0], axis[-1])
    fig.colorbar(mesh, ax=ax).set_label(r"$\log_{10}$ PSD (V$^2$/Hz)")
    save_plot(fig, results / "noise_spectrum_2d.png")


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
    plot_noise_spectrum_2d(results, matrix, frequency, ridge["omega_ctrl"], calibrated=True)


def plot_fit(results, matrix, frequency, control, fit):
    x_khz = np.asarray(frequency, float) / 1000
    good = np.asarray(fit["fit_mask"], bool)
    controlled, controlled_valid = _interpolate_for_plot(x_khz, fit["S_beta"], good)
    background, background_valid = _interpolate_for_plot(x_khz, fit["N_S1"], fit["background_fit_mask"])
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


def plot_background_diagnostic(results, frequency, fit):
    """区分模型背景与独立远端平台，不额外标记低频边界点。"""
    fig, ax = new_figure(kind="wide", height_mm=65)
    for methods, label, marker, color in (
        (("lorentzian_fit", "edge_fixed_shape"), "Lorentzian background", ".", COLOR_TRAD),
        (("off_resonance_plateau", "off_resonance_edge_plateau"),
         "Independent off-resonance background", "s", COLOR_OPTIMAL),
    ):
        mask = fit["background_fit_mask"] & np.isin(fit["background_method"], methods)
        ax.semilogy(frequency[mask] / 1000, fit["N_S1"][mask], marker,
                    ms=3, color=color, label=label)
    format_axis(ax, xlabel="Frequency (kHz)", ylabel="Background PSD N_S1 (V²/Hz)")
    style_legend(ax, loc="best")
    save_plot(fig, results / "background_identifiability.png")


class AnalysisWorkflow:
    """持久化五个分析阶段；前置产物必须属于同一输入和参数修订。"""

    def __init__(self, run_dir, analysis_parameters=None, *, use_saved_options=True):
        import hashlib
        from .stages import ANALYSIS, initial_states, invalidate
        from .state_io import read_state
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        if self.config.get("completion_status") == "running":
            raise ValueError("本轮扫描仍在写入数据，请等待扫描结束")
        defaults = NoiseSpectrumXYParams()
        saved = self.config.get("parameters", {})
        self.options = {key: float(saved.get("ANALYSIS_" + key.upper(), getattr(defaults, "analysis_" + key)))
            for key in ("bin_width_hz", "frequency_min_hz", "frequency_max_hz", "fit_half_width_hz")}
        previous = read_state(self.state_path) or {}
        if use_saved_options:
            self.options.update(previous.get("options", {}))
        self.states = previous.get("steps") or initial_states(ANALYSIS)
        self.revision = previous.get("revision", 0)
        self.summary = previous.get("summary") or {}
        raw = [{"name": path.name, "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()]
        # 适配器会写分析状态；它不是采集输入，不能让刚完成的阶段立即失效。
        acquisition_config = {key: value for key, value in self.config.items() if key not in ("analysis_status", "analysis_error")}
        self.fingerprint = dict(config_sha256=hashlib.sha256(json.dumps(acquisition_config,
            sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest(),
            raw_files=raw, analysis_version=ANALYSIS_VERSION)
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running":
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
            if record["status"] == "completed" and any(self._file_signature(self.results / name) != signature
                    for name, signature in record.get("files", {}).items()):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if analysis_parameters:
            changed = set()
            for name, value in analysis_parameters.items():
                key = name.removeprefix("ANALYSIS_").lower()
                if name != "ANALYSIS_" + key.upper() or key not in self.options:
                    raise ValueError("不是分析参数：" + name)
                value = float(value)
                if value != self.options[key]:
                    changed.add(name)
                    self.options[key] = value
            for stage in ANALYSIS:
                if changed.intersection(stage.parameters):
                    invalidate(ANALYSIS, self.states, stage.id)
                    break
        values = list(self.options.values())
        if any(not np.isfinite(value) or value < 0 for value in values) or self.options["bin_width_hz"] == 0 or self.options["fit_half_width_hz"] == 0 or self.options["frequency_min_hz"] >= self.options["frequency_max_hz"]:
            raise ValueError("分析参数范围无效")

    @staticmethod
    def _file_signature(path):
        return [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options,
            steps=self.states, fingerprint=self.fingerprint, summary=self.summary)

    def persist(self):
        from .state_io import write_state
        from ...experiment_runtime import report_runtime_progress
        write_state(self.state_path, self.snapshot())
        if self.states["B01"]["status"] != "running":
            write_json(self.results / "analysis_summary.json", self.summary)
        report_runtime_progress("analysis", f"分析状态：{self.summary.get('status', 'not_started')}",
            data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        from .stages import ANALYSIS
        index = next(i for i, stage in enumerate(ANALYSIS) if stage.id == item)
        names = {name for stage in ANALYSIS[index:] for name in stage.artifacts}
        if item == "B03" and (self.results / "calibration.npz").exists():
            names.update(("noise_spectrum_2d.png", "noise_spectrum_2d.pdf"))
        files = [path for path in self.results.glob("*") if path.is_file() and path.name in names]
        if files:
            summary_path = self.results / "analysis_summary.json"
            if summary_path.is_file():
                files.append(summary_path)
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                shutil.move(str(path), str(backup / path.name))
            self.summary["backup_directory"] = backup.name

    def _data(self):
        with np.load(self.results / "psd_matrix.npz", allow_pickle=False) as cached:
            matrix, frequency, voltage, rows = (cached[key] for key in ("psd_matrix", "freq_axis", "amplitudes", "valid_rows"))
        band = (frequency > 0) & (frequency >= self.options["frequency_min_hz"]) & (frequency <= self.options["frequency_max_hz"])
        return matrix, frequency, voltage, rows, band, frequency[band], voltage[rows], matrix[rows][:, band]

    def _ridge(self):
        with np.load(self.results / "calibration.npz", allow_pickle=False) as cached:
            return dict(cached)

    def execute(self, selected, expected_revision=None):
        from .stages import ANALYSIS, invalidate, validate_selection
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析状态已改变，请刷新后重试")
        selected = validate_selection(ANALYSIS, self.states, selected)
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        set_plot_style("paper")
        for item in selected:
            check_cancelled()
            invalidate(ANALYSIS, self.states, item)
            summary_keys = {"B02": ("psd", "noise_spectrum_2d"), "B03": ("calibration",),
                "B04": ("fit", "background_diagnostic"), "B05": ("global_comparison",)}
            stage_index = next(i for i, stage in enumerate(ANALYSIS) if stage.id == item)
            for stage in ANALYSIS[stage_index:]:
                for key in summary_keys.get(stage.id, ()):
                    self.summary.pop(key, None)
            if item != "B01":
                self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.update(status="running", options=self.options)
            self.summary.pop("error", None)
            self.persist()
            try:
                getattr(self, "step_" + item)()
                self.states[item] = {"status": "completed", "error": "",
                    "files": {name: self._file_signature(self.results / name)
                        for stage in ANALYSIS if stage.id == item for name in stage.artifacts if name.endswith(".npz")}}
                if item != "B05":
                    self.summary["status"] = "partial"
            except BaseException as exc:
                cancelled = isinstance(exc, (WorkflowCancelled, KeyboardInterrupt))
                status = "cancelled" if cancelled else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary

    def step_B01(self):
        run_dir, results, config, options, summary = self.run_dir, self.results, self.config, self.options, self.summary
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
        self.summary = summary = dict(analysis_version=ANALYSIS_VERSION, status="running", options=options,
                       plotting=dict(profile="paper", figure_kind="wide",
                                      standard_height_mm=65, diagnostic_height_mm=110,
                                      paired_exports=True, dpi=300),
                       backup_directory=backup.name if backup else None, warnings=[])
        self.summary = summary
        set_plot_style("paper")

    def step_B02(self):
        run_dir, results, config, options, summary = self.run_dir, self.results, self.config, self.options, self.summary
        matrix, frequency, voltage, rows, psd_info = compute_psd(run_dir,config,options)
        summary["psd"] = psd_info
        np.savez(results/"psd_matrix.npz",psd_matrix=matrix,freq_axis=frequency,amplitudes=voltage,
                 actual_rate=psd_info["actual_rate_sa_s"],nperseg=psd_info["nperseg"],valid_rows=rows)
        # 完整 Welch 矩阵保留 DC；分离、导出和结果图只使用正频率。
        band = ((frequency > 0) & (frequency >= options["frequency_min_hz"])
                & (frequency <= options["frequency_max_hz"]))
        f, v, data = frequency[band], voltage[rows],matrix[rows][:,band]
        print(f"Welch: {len(rows)}/{len(voltage)} 点, nperseg={psd_info['nperseg']}, df={psd_info['bin_width_hz']:.3f} Hz",flush=True)
        # 标定或分离失败也保留原始二维谱；无效扫描行留空，不插值。
        plot_noise_spectrum_2d(results, matrix[:, band], f, voltage)
        summary["noise_spectrum_2d"] = dict(control_axis="sine_peak_voltage_v", calibrated=False)

    def step_B03(self):
        run_dir, results, config, options, summary = self.run_dir, self.results, self.config, self.options, self.summary
        matrix, frequency, voltage, rows, band, f, v, data = self._data()
        plot_noise_spectrum_2d(results, matrix[:, band], f, voltage)
        summary["noise_spectrum_2d"] = dict(control_axis="sine_peak_voltage_v", calibrated=False)
        ridge = locate_ridge(data,f,v)
        np.savez(results/"calibration.npz",**ridge,amplitudes=v,freq_axis=f)
        plot_ridge(results,data,f,v,ridge)
        summary["noise_spectrum_2d"] = dict(control_axis="calibrated_control_frequency_khz", calibrated=True)
        summary["calibration"] = {key: float(ridge[key]) for key in ("k","b","residual_std_hz","coverage")}
        summary["calibration"]["support_points"] = len(ridge["V_cal"])
        summary["calibration"]["half_slopes"] = ridge["half_slopes"].tolist()
        support = ridge["k"]*ridge["V_cal"]+ridge["b"]
        summary["calibration"]["support_frequency_hz"] = [float(support.min()),float(support.max())]
        print("移动峰标定:",summary["calibration"],flush=True)

    def step_B04(self):
        run_dir, results, config, options, summary = self.run_dir, self.results, self.config, self.options, self.summary
        _, _, _, _, _, f, v, data = self._data()
        ridge = self._ridge()
        support = ridge["k"] * ridge["V_cal"] + ridge["b"]
        fit = fit_local_spectra(
            data, f, ridge["omega_ctrl"], ridge["spur_mask"],
            options["fit_half_width_hz"], (support.min(), support.max()),
            full_range=True, allow_boundary_fits=True,
        )
        # 保留历史 full_range_* 输出键；三种实验现在统一以共享全轴分析为主结果。
        full_range_fit = fit
        good = fit["fit_mask"]
        np.savez(results/"popt_fit.npz",**{k: val for k,val in fit.items() if k not in ("model_matrix","S_beta","N_S1")},
                 **{f"full_range_{k}": val for k, val in full_range_fit.items()
                    if k not in ("model_matrix", "S_beta", "N_S1")},
                 freq_axis=f,param_names=["gamma","Amp","D","dw"])
        np.savez(results/"noise_spectra.npz",S_beta=fit["S_beta"],N_S1=fit["N_S1"],freq_axis=f,
                 amplitudes=v,omega_ctrl=ridge["omega_ctrl"],fit_mask=good,quantitative_valid_mask=good,
                 interpolated_mask=fit["interpolated_mask"],rejection_reason=fit["rejection_reason"],
                 background_fit_mask=fit["background_fit_mask"],
                 background_rejection_reason=fit["background_rejection_reason"],
                 background_method=fit["background_method"],
                 fit_mode=fit["fit_mode"], edge_fit_mask=fit["edge_fit_mask"],
                 S_beta_full_range=full_range_fit["S_beta"],
                 N_S1_full_range=full_range_fit["N_S1"],
                 fit_mask_full_range=full_range_fit["fit_mask"],
                 background_fit_mask_full_range=full_range_fit["background_fit_mask"],
                 background_rejection_reason_full_range=full_range_fit["background_rejection_reason"],
                 tail_holdout_relative_error_full_range=full_range_fit["tail_holdout_relative_error"],
                 coefficient_semantics="A = C*S_beta; uncalibrated response coefficient, V^2 Hz")
        np.savetxt(results/"noise_spectra.csv",np.column_stack((
            f, fit["S_beta"], fit["N_S1"], good.astype(int),
            fit["background_fit_mask"].astype(int), fit["edge_fit_mask"].astype(int),
        )), delimiter=",", header="frequency_Hz,S_beta_controllable_V2_per_Hz,N_S1_uncontrollable_V2_per_Hz,valid,background_valid,edge_fixed_shape", comments="")
        np.savez(results/"fit_diagnostics.npz",frequency_hz=f,control_hz=ridge["omega_ctrl"],
                 measured=data,model=fit["model_matrix"],spur_mask=ridge["spur_mask"])
        plot_fit(results,data,f,ridge["omega_ctrl"],fit)
        plot_background_diagnostic(results, f, fit)
        reasons,counts=np.unique(fit["rejection_reason"],return_counts=True)
        background_reasons, background_counts = np.unique(
            full_range_fit["background_rejection_reason"], return_counts=True
        )
        summary["fit"] = dict(method=SEPARATION_METHOD, valid_points=int(good.sum()),total_points=len(f),
                              background_valid_points=int(fit["background_fit_mask"].sum()),
                              edge_fixed_shape_points=int(fit["edge_fit_mask"].sum()),
                              edge_frequency_extent_hz=(
                                  [float(f[fit["edge_fit_mask"]].min()), float(f[fit["edge_fit_mask"]].max())]
                                  if fit["edge_fit_mask"].any() else None
                              ),
                              rejection_counts=dict(zip(reasons.tolist(),counts.tolist())),
                              valid_frequency_extent_hz=[float(f[good].min()),float(f[good].max())] if good.any() else None,
                              median_holdout_error=float(np.median(fit["holdout_relative_error"][good])) if good.any() else None)
        full_range_good = full_range_fit["fit_mask"]
        full_range_background_good = full_range_fit["background_fit_mask"]
        tail_errors = full_range_fit["tail_holdout_relative_error"][full_range_background_good]
        summary["background_diagnostic"] = dict(
            method=SEPARATION_METHOD,
            peak_fit_points=int(full_range_good.sum()),
            background_valid_points=int(full_range_background_good.sum()),
            total_points=len(f),
            background_rejection_counts=dict(zip(background_reasons.tolist(), background_counts.tolist())),
            median_tail_holdout_error=float(np.median(tail_errors)) if tail_errors.size else None,
            note="N_S1 按独立背景掩码输出；popt/perr 仅描述洛伦兹模型，背景来源见 background_method",
        )

    def step_B05(self):
        run_dir, results, config, options, summary = self.run_dir, self.results, self.config, self.options, self.summary
        _, _, _, _, _, f, v, data = self._data()
        ridge = self._ridge()
        support = ridge["k"] * ridge["V_cal"] + ridge["b"]
        with np.load(results / "noise_spectra.npz", allow_pickle=False) as cached:
            fit = dict(cached)
        good = fit["fit_mask"]
        summary["global_comparison"] = {"status": "skipped", "reason": "可用对照频点不足 40"}
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
        summary["warnings"].append("低频边界点使用扫描内部已验收线宽/偏移的固定形状拟合；edge_fixed_shape 点不是四参数独立线宽拟合，详见 fit_mode。")
        summary["warnings"].append("0 Hz 是去均值 Hann-Welch 的 DC bin，已从分离谱、CSV 和结果图排除；完整 psd_matrix.npz 仍保留该频点。")
        summary["warnings"].append("三个 XY 噪声实验共用全控制轴分离；可控响应与背景独立验收，峰失败时允许通过两侧平台一致性和分块留出验证提取 N_S1。")
        summary["warnings"] = list(dict.fromkeys(summary["warnings"]))
        summary["status"] = "completed" if good.any() or fit["background_fit_mask"].any() else "quality_failed"
        if summary["status"] == "quality_failed":
            summary["error"] = "没有频率点通过峰或背景质量验收"
        print("分析完成:",summary["fit"],flush=True)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    """完整分析和分阶段分析复用相同五个步骤。"""
    from .stages import ANALYSIS
    ctx = AnalysisWorkflow(run_dir, analysis_parameters, use_saved_options=step_ids is not None)
    return ctx.execute(step_ids or [stage.id for stage in ANALYSIS], expected_revision)


def main():
    import os
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    summary = analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0 if summary["status"] in ("completed", "partial") else 2


if __name__ == "__main__":
    raise SystemExit(main())
