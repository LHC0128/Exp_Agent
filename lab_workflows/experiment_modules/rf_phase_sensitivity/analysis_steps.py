"""从封存的 XY 帧测量基带增益和噪声；示波器与锁相数据只比较幅值。"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

import numpy as np
from scipy.signal import correlate, welch

from ...analysis_execution import file_signature as signature
from ...experiment_runtime import check_cancelled
from ...workflow_state import write_state
from . import run_format

ANALYSIS_VERSION = "rf-phase-sensitivity-v1"
SUMMARY = run_format.SUMMARY
TRANSFER_CSV = run_format.TRANSFER_TABLE
NOISE_CSV = run_format.NOISE_TABLE
TRANSFER_PNG = run_format.TRANSFER_PLOT
NOISE_PNG = run_format.NOISE_PLOT
ARTIFACTS = (SUMMARY, TRANSFER_CSV, NOISE_CSV, TRANSFER_PNG, NOISE_PNG)

MIN_TONE_SNR = 5.0
MIN_TONE_CYCLES = 9.8
MIN_NOISE_HZ = 1.0
MIN_WELCH_SEGMENTS = 2
CLOSURE_RANGE = (0.8, 1.2)
REPEAT_FLOOR = 0.15
REPEAT_CAP = 0.30
REPEAT_SIGMA = 2.0
STEP_RTOL = 5e-4
RATE_RTOL = 1e-3
DAQ_STREAM_RTOL = 0.003
GRID_ATOL_SAMPLES = 0.02
LOCKIN_PHASE_ATOL_DEG = 1e-3
LOCKIN_RTOL = 1e-6

FRAME_ERRORS = (OSError, ValueError, TypeError, KeyError, EOFError, BadZipFile, np.linalg.LinAlgError)
CALIBRATION_KEYS = ("k0_v_per_rad", "k0_se_v_per_rad", "burst_command_deg", "carrier_center_deg",
                    "slope_x_v_per_rad", "lockin_phase_deg")
LOCKIN_KEYS = ("phase_deg", "order", "time_constant_s", "stream_rate_sa_s", "oscillator_hz")
TRANSFER_COLUMNS = (
    "attempt", "frequency_hz", "repeat", "scope_label", "xy_label", "electrical_accepted", "electrical_reason",
    "phase_pk_rad", "phase_scatter_rad", "am_fraction_pk", "center_error_deg", "rate_sa_s", "samples", "cycles",
    "y_pk_v", "y_pk_se_v", "y_snr", "x_pk_v", "x_2f_pk_v", "y_2f_pk_v",
    "h_v_per_rad", "h_fit_se_v_per_rad", "normalized_gain", "normalized_gain_se", "accepted", "reason",
)
NOISE_COLUMNS = ("frequency_hz", "detector_y_psd_v2_per_hz", "detector_y_asd_v_per_sqrt_hz",
                 "transfer_band", "h_v_per_rad", "phase_asd_rad_per_sqrt_hz")


def output_path(run_dir, name):
    """拒绝越界路径以及原始或派生文件途经的目录联接。"""
    root = Path(run_dir).resolve()
    relative = Path(name)
    path = root / relative
    if relative.anchor or ".." in relative.parts or path.resolve() != path:
        raise ValueError(f"路径越出运行目录或经过联接：{name}")
    return path


def load_config(run_dir):
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def resolve_options(base, options):
    """分析使用实验保存的参数快照，不支持覆盖。"""
    if options:
        raise ValueError("离线分析不支持覆盖参数：" + "、".join(sorted(options)))
    return base


def fingerprint(run_dir):
    """按语义绑定采集配置并逐帧检查内容；忽略适配器回写的分析状态。"""
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if not isinstance(config, dict):
        raise ValueError("运行配置无法读取为 YAML 映射")
    source = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    config_sha = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    raw = output_path(run_dir, "raw")
    return dict(analysis_version=ANALYSIS_VERSION, config_sha256=config_sha,
                raw_files={f"raw/{path.name}": signature(output_path(raw, path.name))
                           for path in sorted(raw.glob("*.npz"))})


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _verify_attestations(metadata, **expected):
    if any(key in metadata and metadata[key] != value for key, value in expected.items()):
        raise ValueError("帧的频率、激励模式或示波器标签与采集清单不符")


def sample_grid(time_s, rate_sa_s):
    """用端点间隔重算实际率，逐点验证均匀性、连续性以及回读采样率。"""
    reported = _number(rate_sa_s)
    t = np.asarray(time_s, dtype=float)
    if reported is None or reported <= 0 or t.ndim != 1 or t.size < 8 or not np.isfinite(t).all():
        raise ValueError("采样率或时间轴无效，单段至少需要 8 个样本")
    t = t - t[0]
    step = t[-1] / (t.size - 1)
    steps = np.diff(t)
    if (not math.isfinite(step) or step <= 0 or np.any(steps <= 0)
            or not np.allclose(steps, step, rtol=STEP_RTOL, atol=0)
            or np.any(np.abs(t / step - np.arange(t.size)) > GRID_ATOL_SAMPLES)):
        raise ValueError("时间轴不均匀、不连续或有缺样")
    rate = 1 / float(step)
    if not math.isclose(rate, reported, rel_tol=RATE_RTOL):
        raise ValueError("时间轴采样率与帧回读不一致")
    return t, rate


def _waveform(values, time_s):
    y = np.asarray(values, dtype=float)
    if y.shape != time_s.shape or not np.isfinite(y).all():
        raise ValueError("XY 波形非有限或长度与时间轴不一致")
    return y


def _lockin(metadata):
    state = metadata.get("lockin")
    if not isinstance(state, dict):
        raise ValueError("缺少锁相回读状态")
    attestation = {key: _number(state.get(key)) for key in LOCKIN_KEYS}
    if (any(value is None for value in attestation.values())
            or any(attestation[key] <= 0 for key in LOCKIN_KEYS[1:])
            or not 1 <= attestation["order"] <= 8 or not attestation["order"].is_integer()):
        raise ValueError("锁相回读状态不完整或数值无效")
    if not math.isclose(metadata["rate_sa_s"], attestation["stream_rate_sa_s"], rel_tol=DAQ_STREAM_RTOL):
        raise ValueError("XY 帧实际率与锁相传输率回读不一致")
    attestation["order"] = int(attestation["order"])
    return attestation


def _same_phase(a, b):
    return abs(math.remainder(a - b, 360.0)) <= LOCKIN_PHASE_ATOL_DEG


def _same_lockin(a, b):
    return (_same_phase(a["phase_deg"], b["phase_deg"]) and a["order"] == b["order"]
            and all(math.isclose(a[key], b[key], rel_tol=LOCKIN_RTOL) for key in LOCKIN_KEYS[2:]))


def fit_tone(time_s, x_v, y_v, rate_sa_s, frequency_hz, *, correlation_s=0.0):
    """直接同时拟合正余弦、常量、漂移；只发布幅值和拟合标准误。"""
    t, rate = sample_grid(time_s, rate_sa_s)
    frequency = _number(frequency_hz)
    nyquist = min(rate, rate_sa_s) / 2
    if frequency is None or not 0 < frequency < nyquist:
        raise ValueError("基带音调不在帧的 Nyquist 范围内")
    cycles = frequency * float(t[-1])
    if cycles < MIN_TONE_CYCLES:
        raise ValueError(f"基带音调帧不足 {MIN_TONE_CYCLES:g} 个周期")
    x, y = _waveform(x_v, t), _waveform(y_v, t)
    theta = (2 * np.pi * frequency) * t
    columns = [np.sin(theta), np.cos(theta), np.ones_like(t), t / t[-1] - 0.5]
    include_2f = 2 * frequency + 1 / t[-1] < nyquist
    if include_2f:
        columns.extend((np.sin(2 * theta), np.cos(2 * theta)))
    design = np.column_stack(columns)
    coeff, _, rank, spectrum = np.linalg.lstsq(design, np.column_stack((x, y)), rcond=None)
    if rank != design.shape[1] or spectrum[-1] <= spectrum[0] * 1e-8:
        raise ValueError("基带音调拟合的设计矩阵病态")
    x_pk, y_pk = map(float, np.hypot(*coeff[:2]))
    residual = y - design @ coeff[:, 1]
    dof = len(t) - rank
    inverse_gram = np.linalg.inv(design.T @ design)
    direction = coeff[:2, 1] / y_pk if y_pk > 0 else np.array([1.0, 0.0])
    variance = (residual @ residual / dof) * (direction @ inverse_gram[:2, :2] @ direction)
    # Bartlett 加权的相关残差方差只提高、不会压低独立残差给出的标准误。
    lag = math.ceil(min(dof // 4, 5 * correlation_s * rate))
    if lag >= 1:
        score = residual * (design @ (inverse_gram[:, :2] @ direction))
        ac = correlate(score, score, mode="full", method="fft")[len(t) - 1:len(t) + lag]
        tapered = ac[0] + 2 * (ac[1:] @ (1 - np.arange(1, lag + 1) / (lag + 1)))
        variance = max(variance, tapered * len(t) / dof)
    y_se = math.sqrt(max(0.0, variance))
    # 抑制常数或纯漂移的舍入误差伪装成高 SNR；不设物理电压阈值。
    floor = max(np.finfo(float).tiny, 32 * np.finfo(float).eps * np.max(np.abs(y)))
    y_snr = y_pk / max(y_se, floor)
    result = dict(rate_sa_s=rate, samples=int(t.size), cycles=cycles, x_pk_v=x_pk, y_pk_v=y_pk,
                  y_pk_se_v=y_se, y_snr=float(y_snr))
    if include_2f:
        result.update(x_2f_pk_v=float(np.hypot(*coeff[4:6, 0])), y_2f_pk_v=float(np.hypot(*coeff[4:6, 1])))
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("基带音调拟合产生了非有限值")
    return result


def _transfer_rows(run_dir, attempts, configured, calibration):
    k0, k0_se = (calibration.get(key) for key in CALIBRATION_KEYS[:2])
    counts = {key: Counter(value for item in attempts if (value := _text(item.get(key))) is not None)
              for key in ("scope_label", "xy_label")}
    primary = Counter(_number(item.get("frequency_hz")) for item in attempts if item.get("repeat") is False)
    reference, rows = None, []
    for index, item in enumerate(attempts, 1):
        check_cancelled()
        f = _number(item.get("frequency_hz"))
        pm = _number(item.get("phase_pk_rad"))
        repeat, electrical = item.get("repeat"), item.get("accepted")
        scope, xy = _text(item.get("scope_label")), _text(item.get("xy_label"))
        row = dict(attempt=index, frequency_hz=f, repeat=repeat if isinstance(repeat, bool) else None,
                   scope_label=scope, xy_label=xy, phase_pk_rad=pm, phase_scatter_rad=_number(item.get("phase_scatter_rad")),
                   am_fraction_pk=_number(item.get("am_fraction_pk")), center_error_deg=_number(item.get("center_error_deg")),
                   electrical_accepted=electrical if isinstance(electrical, bool) else None,
                   electrical_reason=_text(item.get("reason")))
        reasons = []
        if f not in configured or not isinstance(repeat, bool):
            reasons.append("频点或复测标志与历史扫描定义不符")
        elif repeat is False and primary[f] != 1:
            reasons.append("同一频点有重复的主测记录")
        if any(counts[key][row[key]] > 1 for key in counts):
            reasons.append("原始帧标签被多个频响尝试使用")
        if electrical is not True:
            reasons.append(row["electrical_reason"] or "未通过电学资格")
        else:
            if pm is None or pm <= 0:
                reasons.append("缺少有效的示波器实测 PM 峰值")
            try:
                metadata, _ = run_format.read_frame(run_dir, scope, "scope")
                _verify_attestations(metadata, modulation_analysis_hz=f)
            except FRAME_ERRORS as exc:
                reasons.append(f"示波器原始帧不可读或与清单不符：{exc}")
        if xy is not None:
            try:
                metadata, arrays = run_format.read_frame(run_dir, xy, "xy")
                _verify_attestations(metadata, frequency_hz=f, scope_label=scope)
                state = _lockin(metadata)
                row.update(fit_tone(arrays["time_s"], arrays["x_v"], arrays["y_v"], metadata["rate_sa_s"], f,
                                    correlation_s=state["order"] * state["time_constant_s"]))
                if row["y_snr"] < MIN_TONE_SNR:
                    reasons.append(f"Y 音调信噪比低于 {MIN_TONE_SNR:g}")
                if (phase := calibration.get("lockin_phase_deg")) is not None and not _same_phase(state["phase_deg"], phase):
                    reasons.append("频响帧锁相相位与静态标定不一致")
                if reference is not None and not _same_lockin(state, reference):
                    reasons.append("频响帧锁相状态与首个合格频点不一致")
            except FRAME_ERRORS as exc:
                reasons.append(f"XY 帧或音调拟合无效：{exc}")
        else:
            reasons.append("缺少 XY 帧")
        if not reasons:
            h, h_se = row["y_pk_v"] / pm, row["y_pk_se_v"] / pm
            if not math.isfinite(h) or h <= 0 or not math.isfinite(h_se):
                reasons.append("实测相位换算的增益无效")
            else:
                row.update(h_v_per_rad=h, h_fit_se_v_per_rad=h_se)
                if k0:
                    gain = _number(h / abs(k0))
                    if gain is not None:
                        row["normalized_gain"] = gain
                        if k0_se is not None and k0_se >= 0:
                            row["normalized_gain_se"] = _number(math.hypot(h_se / abs(k0), gain * (k0_se / abs(k0))))
                if reference is None:
                    reference = state
        row.update(accepted=not reasons, reason="；".join(reasons))
        rows.append(row)
    return rows, reference


def _gates(rows, accepted, lowest, anchor):
    closure = dict(frequency_hz=lowest, accepted_range=list(CLOSURE_RANGE), passed=False)
    if (gain := accepted.get(lowest, {}).get("normalized_gain")) is None:
        closure["reason"] = "最低配置频点缺少合格增益或有效静态 K0"
    else:
        closure.update(normalized_gain=gain, passed=CLOSURE_RANGE[0] <= gain <= CLOSURE_RANGE[1])
        if not closure["passed"]:
            closure["reason"] = "最低频点 H/|K0| 超出 0.8–1.2；未以锚点强制归一"

    repeat = dict(frequency_hz=anchor, passed=False)
    if (len(rows) < 2 or rows[0]["frequency_hz"] != anchor or rows[0]["repeat"] is not False
            or rows[-1]["frequency_hz"] != anchor or rows[-1]["repeat"] is not True
            or sum(row["repeat"] is True for row in rows) != 1):
        repeat["reason"] = "缺少到达顺序首末的独立锚点和唯一复测"
    elif not all(row["accepted"] for row in (rows[0], rows[-1])):
        repeat["reason"] = "首末锚点至少一帧不合格"
    else:
        first, last = rows[0], rows[-1]
        h0, h1 = first["h_v_per_rad"], last["h_v_per_rad"]
        ratio = h1 / h0
        scale = max(h0, h1)
        a, b = h0 / scale, h1 / scale
        difference = 2 * abs(b - a) / (a + b)
        se = (4 * a * b / (a + b)**2) * math.hypot(first["h_fit_se_v_per_rad"] / h0, last["h_fit_se_v_per_rad"] / h1)
        limit = min(REPEAT_CAP, max(REPEAT_FLOOR, REPEAT_SIGMA * se))
        repeat.update(first_h_v_per_rad=h0, repeat_h_v_per_rad=h1, ratio=_number(ratio),
                      symmetric_fractional_difference=difference, difference_fit_se=se,
                      fractional_limit=limit, passed=difference <= limit)
        if not repeat["passed"]:
            repeat["reason"] = "锚点首末增益的对称相对差超过允许漂移"
    return closure, repeat


def _bands(configured, accepted):
    """只连接配置表中的相邻合格频点，不用复测替换主测。"""
    bands, band = [], []
    for f in configured:
        if f in accepted:
            band.append(f)
        else:
            if len(band) >= 2:
                bands.append(band)
            band = []
    if len(band) >= 2:
        bands.append(band)
    return bands


def _welch(time_s, y_v, rate_sa_s, segment_s):
    t, rate = sample_grid(time_s, rate_sa_s)
    y = _waveform(y_v, t)
    # 请求分辨率为 1 / segment_s，短帧只提供如实标明分辨率的探测器诊断谱。
    requested = max(8, math.ceil(rate * segment_s))
    nperseg = min(requested, y.size)
    noverlap = nperseg // 2
    nsegments = 1 + (y.size - nperseg) // (nperseg - noverlap)
    frequency, psd = welch(y, fs=rate, window="hann", nperseg=nperseg, noverlap=noverlap,
                           detrend="linear", return_onesided=True, scaling="density")
    keep = frequency >= MIN_NOISE_HZ
    if not keep.any() or not np.isfinite(psd).all() or (psd < 0).any():
        raise ValueError("没有有效的 1 Hz 以上噪声谱")
    details = dict(rate_sa_s=rate, samples=int(y.size), nperseg=nperseg, noverlap=noverlap,
                   overlap_fraction=noverlap / nperseg, segments=nsegments, window="hann", detrend="linear",
                   one_sided=True, segment_s=nperseg / rate, requested_segment_s=segment_s,
                   resolution_hz=rate / nperseg, resolution_met=nperseg == requested)
    return frequency[keep], psd[keep], details


def _noise(run_dir, noise, rows, reference, segment_s):
    empty = np.array([], dtype=float)
    frequency, psd = empty, empty
    xy = _text(noise.get("xy_label"))
    monitor = noise.get("scope_labels")
    electrical = noise.get("accepted") is True
    details = dict(xy_label=xy, electrical_accepted=electrical, electrical_reason=_text(noise.get("reason")))
    reasons = []
    if not electrical:
        reasons.append(details["electrical_reason"] or "噪声未通过电学资格")
    elif (not isinstance(monitor, list) or len(monitor) != 2 or not all(_text(label) for label in monitor)
          or monitor[0] == monitor[1] or any(label in {r["scope_label"] for r in rows} for label in monitor)):
        reasons.append("噪声缺少独立的前后示波器监测帧")
    else:
        details["scope_labels"] = monitor
        for label in monitor:
            check_cancelled()
            try:
                run_format.read_frame(run_dir, label, "scope")
            except FRAME_ERRORS as exc:
                reasons.append(f"噪声监测帧不可读：{exc}")
    try:
        metadata, arrays = run_format.read_frame(run_dir, xy, "xy")
        frequency, psd, welch_details = _welch(arrays["time_s"], arrays["y_v"], metadata["rate_sa_s"], segment_s)
        details["welch"] = welch_details
        if not welch_details["resolution_met"] or welch_details["segments"] < MIN_WELCH_SEGMENTS:
            reasons.append("噪声帧长度不足以达到请求的 Welch 分辨率和最少分段数")
        if any(xy == row["xy_label"] for row in rows):
            reasons.append("噪声帧与频响帧共用标签")
        _verify_attestations(metadata, source_mode="unmodulated_carrier")
        state = _lockin(metadata)
        details["lockin"] = state
        if reference is None or not _same_lockin(state, reference):
            reasons.append("噪声帧锁相状态与合格频响不一致或无法比对")
    except FRAME_ERRORS as exc:
        reasons.append(f"噪声帧或回读无效：{exc}")
    details.update(detector_spectrum_available=bool(frequency.size), qualified=not reasons, reasons=reasons)
    return frequency, psd, details


def _refer_noise(frequency, asd, bands, accepted):
    h = np.full(frequency.shape, np.nan)
    phase = np.full(frequency.shape, np.nan)
    band_ids = np.zeros(frequency.shape, dtype=int)
    metrics = []
    for band_id, band in enumerate(bands, 1):
        indices = np.flatnonzero((frequency >= band[0]) & (frequency <= band[-1]))
        if not indices.size:
            continue
        h_band = np.exp(np.interp(np.log(frequency[indices]), np.log(band),
                                   np.log([accepted[f]["h_v_per_rad"] for f in band])))
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            values = asd[indices] / h_band
            keep = np.isfinite(values) & np.isfinite(values * 1e6) & (h_band > 0)
        indices, values = indices[keep], values[keep]
        if not indices.size:
            continue
        h[indices], phase[indices], band_ids[indices] = h_band[keep], values, band_id
        median = float(np.median(values))
        middle = int(np.argmin(abs(frequency[indices] - (band[0] + band[-1]) / 2)))
        metrics.append(dict(transfer_band=band_id, bins=int(indices.size), frequency_min_hz=float(frequency[indices[0]]),
                            frequency_max_hz=float(frequency[indices[-1]]), midpoint_bin_hz=float(frequency[indices[middle]]),
                            midpoint_rad_per_sqrt_hz=float(values[middle]), midpoint_urad_per_sqrt_hz=float(values[middle] * 1e6),
                            median_rad_per_sqrt_hz=median, median_urad_per_sqrt_hz=median * 1e6))
    return h, phase, band_ids, metrics


def _save_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def _plots(directory, rows, accepted, summary, frequency, asd, phase, band_ids):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from ...plotting import figure_size, plot_style_context

    def diagnostic(ax, text):
        ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)

    def figure_pair():
        figure = Figure(figsize=figure_size("wide", height_mm=112), layout="constrained")
        FigureCanvasAgg(figure)
        return figure, figure.subplots(2, 1, sharex=True)

    with plot_style_context("paper"):
        fig, axes = figure_pair()
        for ax, key, error, ylabel in zip(axes, ("h_v_per_rad", "normalized_gain"),
                                         ("h_fit_se_v_per_rad", "normalized_gain_se"),
                                         ("Magnitude |H| (V/rad)", "Normalized gain |H| / |K0|")):
            primary = [row for row in accepted.values() if row.get(key, 0) > 0]
            repeated = [row for row in rows if row["repeat"] and row["accepted"] and row.get(key, 0) > 0]
            for selection, marker, label in ((primary, "o", "Primary"), (repeated, "x", "Repeat")):
                if selection:
                    ax.plot([r["frequency_hz"] for r in selection], [r[key] for r in selection],
                            linestyle="none", marker=marker, label=label)
                    bars = [r for r in selection if r.get(error) is not None and r[error] < r[key]]
                    if bars:
                        ax.errorbar([r["frequency_hz"] for r in bars], [r[key] for r in bars],
                                    yerr=[r[error] for r in bars], fmt="none", capsize=2)
            ax.set(xscale="log", ylabel=ylabel)
            if primary or repeated:
                ax.set_yscale("log")
                ax.legend()
            else:
                diagnostic(ax, "No qualified magnitude estimates" if ax is axes[0] else "No usable K0-normalized estimates")
        n = len(summary["configured_frequencies_hz"])
        closure = "pass" if summary["k0_closure"]["passed"] else "not passed"
        repeat = "pass" if summary["anchor_repeat"]["passed"] else "not passed"
        fig.suptitle(f"Transfer: {len(accepted)}/{n} primary points | K0 closure: {closure} | Repeat: {repeat}")
        axes[-1].set_xlabel("Modulation frequency (Hz)")
        fig.savefig(directory / TRANSFER_PNG, dpi=240)
        check_cancelled()

        fig, (detector, referred) = figure_pair()
        detector.set(xscale="log", ylabel=r"Detector Y ASD (V/$\sqrt{\mathrm{Hz}}$)")
        referred.set(xscale="log", xlabel="Frequency (Hz)", ylabel=r"Equivalent phase ASD (rad/$\sqrt{\mathrm{Hz}}$)")
        if (asd > 0).any():
            detector.loglog(frequency, np.where(asd > 0, asd, np.nan))
        else:
            diagnostic(detector, "Detector Y ASD is zero" if frequency.size else "No valid detector Y spectrum")
        if summary["phase_referral"]["available"]:
            for band in summary["phase_referral"]["bands"]:
                bins = band_ids == band["transfer_band"]
                if (phase[bins] > 0).any():
                    referred.loglog(frequency[bins], np.where(phase[bins] > 0, phase[bins], np.nan),
                                     marker="." if bins.sum() == 1 else None)
            if not (phase > 0).any():
                diagnostic(referred, "Qualified phase ASD is zero")
        else:
            diagnostic(referred, "Phase referral withheld; see summary")
        fig.savefig(directory / NOISE_PNG, dpi=240)


def analyze_frames(run_dir, config, params, expected_fingerprint):
    """生成可辨识的部分结果；输入改变时拒绝发布候选产物。"""
    configured = sorted(params.pm_frequencies_hz)
    config_calibration = _mapping(config.get("calibration"))
    calibration = {key: value for key in CALIBRATION_KEYS if (value := _number(config_calibration.get(key))) is not None}
    if config_calibration.get("accepted") is not True:
        calibration.pop("k0_v_per_rad", None)
        calibration.pop("k0_se_v_per_rad", None)
    if reason := _text(config_calibration.get("reason")):
        calibration["reason"] = reason
    attempts = config.get("transfer")
    rows, reference = _transfer_rows(run_dir, [_mapping(item) for item in attempts] if isinstance(attempts, list) else [],
                                    configured, calibration)
    accepted = {row["frequency_hz"]: row for row in rows if row["accepted"] and not row["repeat"]}
    closure, repeat = _gates(rows, accepted, configured[0], params.pm_anchor_hz)
    bands = _bands(configured, accepted)
    frequency, psd, noise = _noise(run_dir, _mapping(config.get("noise")), rows, reference, params.welch_segment_s)
    asd = np.sqrt(psd)
    reasons = [gate["reason"] for gate in (closure, repeat) if not gate["passed"]] + noise["reasons"]
    if not bands:
        reasons.append("相邻合格主测频点不足，不能在任何频带插值")
    metrics = []
    if not reasons:
        h, phase, band_ids, metrics = _refer_noise(frequency, asd, bands, accepted)
        if not metrics:
            reasons.append("合格频带内没有可折算的 Welch 频点")
    else:
        h = phase = np.full(frequency.shape, np.nan)
        band_ids = np.zeros(frequency.shape, dtype=int)
    status = _text(config.get("completion_status"))
    complete = bool(metrics) and len(accepted) == len(configured) and status == "completed"
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                   analysis_version=ANALYSIS_VERSION, input_fingerprint=expected_fingerprint,
                   acquisition_status=status, acquisition_failure_reason=_text(config.get("failure_reason")),
                   result_status="complete" if complete else "partial" if rows or frequency.size else "no_usable_data",
                   quality_passed=bool(metrics), calibration=calibration, lockin_reference=reference,
                   configured_frequencies_hz=configured, transfer_attempts=len(rows), accepted_primary_points=len(accepted),
                   k0_closure=closure, anchor_repeat=repeat, transfer_bands_hz=[[b[0], b[-1]] for b in bands],
                   minimum_y_tone_snr=MIN_TONE_SNR, minimum_tone_cycles=MIN_TONE_CYCLES, noise=noise,
                   phase_referral=dict(available=bool(metrics), reasons=reasons, bands=metrics, minimum_frequency_hz=MIN_NOISE_HZ,
                                       interpolation="log_frequency_log_magnitude_within_adjacent_accepted_points"),
                   method_notes=["实测示波器 PM 峰值与锁相 Y 峰值只比较幅值，不计算复频响。",
                                 "H 与复测的标准误只传播 Y 拟合误差；归一增益另外传播可用的 K0 标准误，未含 PM 测量不确定度。"])
    invalid_sections = [key for key, kind in (("calibration", dict), ("transfer", list), ("noise", dict))
                        if config.get(key) is not None and not isinstance(config[key], kind)]
    if invalid_sections:
        summary["invalid_metadata_sections"] = invalid_sections

    def noise_rows():
        for f, p, a, band, gain, value in zip(frequency, psd, asd, band_ids, h, phase):
            yield f, p, a, int(band) or None, gain if band else None, value if band else None

    results = output_path(run_dir, "results")
    with TemporaryDirectory(dir=results, prefix=".phase-analysis-") as temporary:
        temp = Path(temporary)
        write_state(temp / SUMMARY, summary)
        _save_csv(temp / TRANSFER_CSV, TRANSFER_COLUMNS, ([row.get(key) for key in TRANSFER_COLUMNS] for row in rows))
        _save_csv(temp / NOISE_CSV, NOISE_COLUMNS, noise_rows())
        _plots(temp, rows, accepted, summary, frequency, asd, phase, band_ids)
        check_cancelled()
        if fingerprint(run_dir) != expected_fingerprint:
            raise ValueError("离线分析期间输入发生变化，请重新执行 B01")
        for name in ARTIFACTS:
            (temp / name).replace(output_path(run_dir, f"results/{name}"))
    return {key: value for key, value in summary.items() if key != "input_fingerprint"}
