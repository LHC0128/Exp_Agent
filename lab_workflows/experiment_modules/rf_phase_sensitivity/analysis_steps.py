"""从封存的 XY 帧测量基带增益和噪声；兼容旧版含示波器监视帧的归档目录。"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

import numpy as np
from scipy.signal import correlate, welch

from ...analysis_execution import file_signature as signature
from ...experiment_runtime import check_cancelled
from ...workflow_state import write_state
from ..z_chain_common import phase_plot
from . import run_format, signal_fit

ANALYSIS_VERSION = "rf-phase-sensitivity-v1"
SUMMARY = run_format.SUMMARY
TRANSFER_CSV = run_format.TRANSFER_TABLE
NOISE_CSV = run_format.NOISE_TABLE
RESPONSE_PNG = run_format.RESPONSE_PLOT
LOCAL_PNG = run_format.LOCAL_PLOT
TRANSFER_PNG = run_format.TRANSFER_PLOT
NOISE_PNG = run_format.NOISE_PLOT
ARTIFACTS = (SUMMARY, TRANSFER_CSV, NOISE_CSV, RESPONSE_PNG, LOCAL_PNG, TRANSFER_PNG, NOISE_PNG)

MIN_TONE_SNR = 5.0
MIN_TONE_CYCLES = 9.8
MIN_NOISE_HZ = 1.0
MIN_WELCH_SEGMENTS = 2
MAINS_HARMONICS_HZ = (50.0, 100.0, 150.0, 200.0, 250.0)
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
    "h_v_per_rad", "h_fit_se_v_per_rad", "normalized_gain", "normalized_gain_se",
    "normalized_h0_gain", "normalized_h0_gain_se",
    "quality_passed", "warnings", "accepted", "reason",
)
NOISE_COLUMNS = (
    "frequency_hz", "detector_y_psd_v2_per_hz", "detector_y_asd_v_per_sqrt_hz",
    "transfer_band", "h_v_per_rad", "phase_asd_rad_per_sqrt_hz", "phase_asd_deg_per_sqrt_hz",
    "phase_asd_urad_per_sqrt_hz", "h_lorentzian_v_per_rad", "phase_asd_lorentzian_urad_per_sqrt_hz",
)


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
        raise ValueError("帧的频率、激励模式或监视标签与采集清单不符")


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
    """同时拟合基频、二次谐波、直流漂移及不碰撞的 50 Hz 工频谐波；发布幅值与拟合标准误。"""
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
    avoid = (frequency, *((2 * frequency,) if include_2f else ()))
    guard_hz = max(2.0 / float(t[-1]), 0.5)
    for mains_hz in MAINS_HARMONICS_HZ:
        if mains_hz + 1 / t[-1] >= nyquist:
            break
        if any(abs(mains_hz - target) < guard_hz for target in avoid) or len(t) <= len(columns) + 8:
            continue
        angle = (2 * np.pi * mains_hz) * t
        columns.extend((np.sin(angle), np.cos(angle)))
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
        repeat = item.get("repeat")
        electrical = item.get("electrical_accepted")
        if not isinstance(electrical, bool):
            electrical = item.get("accepted")
        scope, xy = _text(item.get("scope_label")), _text(item.get("xy_label"))
        row = dict(attempt=index, frequency_hz=f, repeat=repeat if isinstance(repeat, bool) else None,
                   scope_label=scope, xy_label=xy, phase_pk_rad=pm, phase_scatter_rad=_number(item.get("phase_scatter_rad")),
                   am_fraction_pk=_number(item.get("am_fraction_pk")), center_error_deg=_number(item.get("center_error_deg")),
                   electrical_accepted=electrical if isinstance(electrical, bool) else None,
                   electrical_reason=_text(item.get("reason")))
        hard_reasons, warnings = [], []
        if f not in configured or not isinstance(repeat, bool):
            hard_reasons.append("频点或复测标志与历史扫描定义不符")
        elif repeat is False and primary[f] != 1:
            hard_reasons.append("同一频点有重复的主测记录")
        if any(counts[key][row[key]] > 1 for key in counts):
            hard_reasons.append("原始帧标签被多个频响尝试使用")
        if electrical is not True:
            warnings.append(row["electrical_reason"] or "未通过参考电学指标")
        if pm is None or pm <= 0:
            hard_reasons.append("缺少有效的调相峰值（phase_pk_rad）")
        if scope is not None:
            try:
                metadata, _ = run_format.read_frame(run_dir, scope, "scope")
                _verify_attestations(metadata, modulation_analysis_hz=f)
            except FRAME_ERRORS as exc:
                hard_reasons.append(f"示波器原始帧不可读或与清单不符：{exc}")
        if xy is not None:
            try:
                metadata, arrays = run_format.read_frame(run_dir, xy, "xy")
                _verify_attestations(metadata, frequency_hz=f, scope_label=scope)
                state = _lockin(metadata)
                row.update(fit_tone(arrays["time_s"], arrays["x_v"], arrays["y_v"], metadata["rate_sa_s"], f,
                                    correlation_s=state["order"] * state["time_constant_s"]))
                if row["y_snr"] < MIN_TONE_SNR:
                    warnings.append(f"Y 音调信噪比低于 {MIN_TONE_SNR:g}")
                if (phase := calibration.get("lockin_phase_deg")) is not None and not _same_phase(state["phase_deg"], phase):
                    hard_reasons.append("频响帧锁相相位与静态标定不一致")
                if reference is not None and not _same_lockin(state, reference):
                    hard_reasons.append("频响帧锁相状态与首个有效频点不一致")
            except FRAME_ERRORS as exc:
                hard_reasons.append(f"XY 帧或音调拟合无效：{exc}")
        else:
            hard_reasons.append("缺少 XY 帧")
        if not hard_reasons:
            h, h_se = row["y_pk_v"] / pm, row["y_pk_se_v"] / pm
            if not math.isfinite(h) or h <= 0 or not math.isfinite(h_se):
                hard_reasons.append("实测相位换算的增益无效")
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
        row.update(accepted=not hard_reasons, reason="；".join(hard_reasons),
                   quality_passed=(not hard_reasons) and (not warnings), warnings="；".join(warnings))
        rows.append(row)
    return rows, reference


def _gates(rows, accepted, lowest, anchor, *, repeat_expected=None):
    closure = dict(frequency_hz=lowest, accepted_range=list(CLOSURE_RANGE), passed=False)
    if (gain := accepted.get(lowest, {}).get("normalized_gain")) is None:
        closure["reason"] = "最低配置频点缺少合格增益或有效静态 K0"
    else:
        closure.update(normalized_gain=gain, passed=CLOSURE_RANGE[0] <= gain <= CLOSURE_RANGE[1])
        if not closure["passed"]:
            closure["reason"] = "最低频点 H/|K0| 超出 0.8–1.2；未以锚点强制归一"

    has_repeat = any(row["repeat"] is True for row in rows)
    if repeat_expected is None:
        repeat_expected = has_repeat
    if not repeat_expected and not has_repeat:
        return closure, dict(frequency_hz=None, configured=False, passed=True, reason="")

    repeat = dict(frequency_hz=anchor, configured=True, passed=False)
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
    details = dict(rate_sa_s=rate, samples=int(y.size), duration_s=float(t[-1] + 1.0 / rate),
                   nperseg=nperseg, noverlap=noverlap,
                   overlap_fraction=noverlap / nperseg, segments=nsegments, window="hann", detrend="linear",
                   one_sided=True, segment_s=nperseg / rate, requested_segment_s=segment_s,
                   resolution_hz=rate / nperseg, resolution_met=nperseg == requested)
    return frequency[keep], psd[keep], details


def welch_segmented(frames, segment_s):
    """对多个独立温控窗口录得的连续段分别计算 Welch PSD，并按段数加权（等长即等权）平均。"""
    if not frames:
        raise ValueError("缺少可计算 Welch PSD 的噪声分段")
    freq_ref = None
    nperseg_ref = None
    weighted_psd = None
    first_details = None
    total_samples = 0
    total_segments = 0
    total_duration_s = 0.0
    per_frame_segments = []
    resolution_met = True
    for time_s, y_v, rate_sa_s in frames:
        freq, psd, details = _welch(time_s, y_v, rate_sa_s, segment_s)
        if freq_ref is None:
            freq_ref = freq
            nperseg_ref = details["nperseg"]
            weighted_psd = psd * details["segments"]
            first_details = details
        else:
            if details["nperseg"] != nperseg_ref or not np.allclose(freq, freq_ref, rtol=1e-9, atol=1e-9):
                raise ValueError("各噪声分段的实际采样率或 Welch 频率网格不一致，拒绝拼接平均")
            weighted_psd = weighted_psd + psd * details["segments"]
        total_samples += details["samples"]
        total_segments += details["segments"]
        total_duration_s += details["duration_s"]
        per_frame_segments.append(details["segments"])
        resolution_met = resolution_met and bool(details["resolution_met"])
    combined = dict(
        first_details,
        samples=int(total_samples),
        duration_s=float(total_duration_s),
        segment_duration_s=float(first_details["duration_s"]),
        segments=int(total_segments),
        acquisition_segments=len(frames),
        per_frame_segments=per_frame_segments,
        resolution_met=resolution_met,
    )
    return freq_ref, weighted_psd / total_segments, combined


def _noise_labels(noise):
    raw_list = noise.get("xy_labels")
    if isinstance(raw_list, list) and raw_list:
        labels = [_text(item) for item in raw_list]
        if any(label is None for label in labels) or len(set(labels)) != len(labels):
            return None, "噪声分段标签列表无效或存在重复"
        return labels, None
    single = _text(noise.get("xy_label"))
    return ([single] if single is not None else []), None


def _noise(run_dir, noise, rows, reference, segment_s, *, expected_segments=None):
    empty = np.array([], dtype=float)
    frequency, psd = empty, empty
    labels, label_error = _noise_labels(noise)
    xy = labels[0] if labels else _text(noise.get("xy_label"))
    monitor = noise.get("scope_labels")
    raw_electrical = noise.get("electrical_accepted")
    electrical = (raw_electrical if isinstance(raw_electrical, bool) else noise.get("accepted")) is True
    explicit_rejected = raw_electrical is False or (raw_electrical is None and noise.get("accepted") is False)
    details = dict(xy_label=xy, xy_labels=labels or [], electrical_accepted=electrical,
                   electrical_reason=_text(noise.get("reason")))
    hard_reasons, warnings = [], []
    if explicit_rejected or (bool(labels) and not electrical):
        warnings.append(details["electrical_reason"] or "噪声未通过参考电学指标")
    has_scope_frames = any(row.get("scope_label") is not None for row in rows)
    if isinstance(monitor, list) and monitor:
        if (len(monitor) != 2 or not all(_text(label) for label in monitor)
                or monitor[0] == monitor[1] or any(label in {r["scope_label"] for r in rows} for label in monitor)):
            warnings.append("噪声缺少独立的前后示波器监测帧")
        else:
            details["scope_labels"] = monitor
            for label in monitor:
                check_cancelled()
                try:
                    run_format.read_frame(run_dir, label, "scope")
                except FRAME_ERRORS as exc:
                    warnings.append(f"噪声监测帧不可读：{exc}")
    elif has_scope_frames or monitor not in (None, []):
        warnings.append("噪声缺少独立的前后示波器监测帧")
    if label_error is not None:
        hard_reasons.append(label_error)
    else:
        try:
            transfer_labels = {row["xy_label"] for row in rows if row.get("xy_label") is not None}
            target_labels = labels if labels else [None]
            loaded = []
            for label in target_labels:
                check_cancelled()
                if label in transfer_labels:
                    hard_reasons.append("噪声帧与频响帧共用标签")
                metadata, arrays = run_format.read_frame(run_dir, label, "xy")
                loaded.append((label, metadata, arrays))
            if len(loaded) == len(target_labels) and loaded:
                frequency, psd, welch_details = welch_segmented(
                    [(arr["time_s"], arr["y_v"], meta["rate_sa_s"]) for _, meta, arr in loaded],
                    segment_s,
                )
                details["welch"] = welch_details
                if welch_details["segments"] < MIN_WELCH_SEGMENTS:
                    hard_reasons.append("噪声帧长度不足最少 Welch 分段数")
                elif not welch_details["resolution_met"]:
                    warnings.append("噪声帧未达到请求的 Welch 频率分辨率")
                if expected_segments is not None and len(loaded) < int(expected_segments):
                    warnings.append(f"无调制噪声仅完成 {len(loaded)}/{int(expected_segments)} 个分段")
            first_state = None
            for _, metadata, _ in loaded:
                _verify_attestations(metadata, source_mode="unmodulated_carrier")
                state = _lockin(metadata)
                if first_state is None:
                    first_state = state
                    details["lockin"] = first_state
                elif not _same_lockin(state, first_state):
                    hard_reasons.append("分段噪声帧之间锁相状态不一致")
                if reference is None or not _same_lockin(state, reference):
                    hard_reasons.append("噪声帧锁相状态与有效频响不一致或无法比对")
        except FRAME_ERRORS as exc:
            hard_reasons.append(f"噪声帧或回读无效：{exc}")
    hard_reasons = list(dict.fromkeys(hard_reasons))
    if isinstance(noise.get("quality_warnings"), list):
        warnings = list(dict.fromkeys([*warnings, *(w for w in noise["quality_warnings"] if isinstance(w, str) and w)]))
    details.update(detector_spectrum_available=bool(frequency.size), qualified=not hard_reasons,
                   reasons=hard_reasons, quality_passed=(not hard_reasons) and (not warnings), warnings=warnings)
    return frequency, psd, details


def _fit_transfer_lorentzian(rows, accepted, k0):
    ordered = sorted(accepted.values(), key=lambda row: row["frequency_hz"])
    if len(ordered) < 2:
        return dict(available=False, reason="有效主测频点不足 2 个，无法拟合 √Lorentzian")
    clean = [row for row in ordered if not any(abs(row["frequency_hz"] - m) <= 0.5 for m in MAINS_HARMONICS_HZ)]
    fit_rows = clean if len(clean) >= 2 else ordered
    freqs = [row["frequency_hz"] for row in fit_rows]
    gains = [row["h_v_per_rad"] for row in fit_rows]
    ses = [row.get("h_fit_se_v_per_rad") for row in fit_rows]
    fit = signal_fit.fit_sqrt_lorentzian(freqs, gains, ses if all(s is not None for s in ses) else None)
    if not fit.get("available"):
        return fit
    h0 = fit["h0_v_per_rad"]
    h0_se = fit.get("h0_se_v_per_rad")
    valid_h0_se = h0_se is not None and math.isfinite(h0_se) and h0_se >= 0
    for row in rows:
        if (h := row.get("h_v_per_rad")) is not None and h > 0:
            gain = _number(h / h0)
            row["normalized_h0_gain"] = gain
            if gain is not None and (h_se := row.get("h_fit_se_v_per_rad")) is not None:
                rel_h = h_se / h0
                rel_h0 = (gain * (h0_se / h0)) if valid_h0_se else 0.0
                row["normalized_h0_gain_se"] = _number(math.hypot(rel_h, rel_h0))
    if k0 is not None and math.isfinite(k0) and abs(k0) > 0:
        fit["h0_to_k0_ratio"] = _number(h0 / abs(k0))
    return fit


def _refer_noise(frequency, asd, bands, accepted, lorentzian_fit=None):
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
                            midpoint_deg_per_sqrt_hz=float(math.degrees(values[middle])),
                            median_rad_per_sqrt_hz=median, median_urad_per_sqrt_hz=median * 1e6,
                            median_deg_per_sqrt_hz=math.degrees(median)))

    h_lor = np.full(frequency.shape, np.nan)
    phase_lor = np.full(frequency.shape, np.nan)
    lor_summary = dict(available=False, reason="缺少有效的 √Lorentzian 频响拟合")
    if metrics and lorentzian_fit and lorentzian_fit.get("available"):
        h0 = float(lorentzian_fit["h0_v_per_rad"])
        fc = float(lorentzian_fit["fc_hz"])
        if h0 > 0 and fc > 0 and frequency.size:
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                cand_h = h0 / np.sqrt(1.0 + (frequency / fc) ** 2)
                cand_p = asd / cand_h
                valid = np.isfinite(cand_p) & np.isfinite(cand_p * 1e6) & (cand_h > 0)
            if valid.any():
                h_lor[valid] = cand_h[valid]
                phase_lor[valid] = cand_p[valid]
                vals = cand_p[valid]
                med_lor = float(np.median(vals))
                lor_summary = dict(
                    available=True,
                    bins=int(valid.sum()),
                    frequency_min_hz=float(frequency[valid][0]),
                    frequency_max_hz=float(frequency[valid][-1]),
                    h0_v_per_rad=h0,
                    fc_hz=fc,
                    median_rad_per_sqrt_hz=med_lor,
                    median_urad_per_sqrt_hz=med_lor * 1e6,
                )
    return h, phase, band_ids, metrics, h_lor, phase_lor, lor_summary


def _coarse_plot_data(run_dir, config):
    """转调公共 A03 绘图数据实现；读帧与帧异常类型由本实验注入。"""
    return phase_plot.coarse_plot_data(run_dir, config, read_frame=run_format.read_frame,
                                       frame_errors=FRAME_ERRORS)


def _local_plot_data(run_dir, config, calibration):
    raw_points = _mapping(config.get("calibration")).get("points")
    if not isinstance(raw_points, list) or not raw_points:
        return None
    degs, zs, times = [], [], []
    for pt in raw_points:
        if not isinstance(pt, dict):
            continue
        deg = _number(pt.get("carrier_center_deg"))
        if deg is None:
            deg = _number(pt.get("carrier_command_deg"))
        if deg is None:
            deg = _number(pt.get("burst_command_deg"))
        if deg is None:
            continue
        x_val = y_val = None
        if xy_label := _text(pt.get("xy_label")):
            try:
                _, arr = run_format.read_frame(run_dir, xy_label, "xy")
                x_val, y_val = float(np.mean(arr["x_v"])), float(np.mean(arr["y_v"]))
            except FRAME_ERRORS:
                pass
        if x_val is None:
            x_val, y_val = _number(pt.get("x_mean_v")), _number(pt.get("y_mean_v"))
        t_val = _number(pt.get("elapsed_s"))
        if x_val is None or y_val is None or not (math.isfinite(x_val) and math.isfinite(y_val)):
            continue
        degs.append(deg)
        zs.append(complex(x_val, y_val))
        times.append(0.0 if t_val is None else t_val)
    if len(degs) < 3:
        return None
    deg_arr = np.asarray(degs, dtype=float)
    z_arr = np.asarray(zs, dtype=complex)
    t_arr = np.asarray(times, dtype=float)
    center_deg = _number(calibration.get("carrier_center_deg"))
    if center_deg is None:
        center_deg = _number(calibration.get("burst_command_deg"))
    if center_deg is None:
        center_deg = float(deg_arr[0])
    dphi_deg = ((deg_arr - center_deg + 180.0) % 360.0) - 180.0
    dphi_rad = np.deg2rad(dphi_deg)
    dphi_anchor = ((deg_arr[-1] - deg_arr[0] + 180.0) % 360.0) - 180.0
    if deg_arr.size >= 2 and t_arr[-1] > t_arr[0] and abs(dphi_anchor) < 1.0:
        drift = (z_arr[-1] - z_arr[0]) / (t_arr[-1] - t_arr[0])
        z_corr = z_arr - drift * (t_arr - np.mean(t_arr))
    else:
        z_corr = z_arr
    design = np.column_stack((np.ones_like(dphi_rad), dphi_rad))
    coef, _, rank, _ = np.linalg.lstsq(design, z_corr, rcond=None)
    if rank != 2:
        return None
    grid_deg = np.linspace(float(dphi_deg.min()), float(dphi_deg.max()), 300)
    grid_rad = np.deg2rad(grid_deg)
    x_poly = np.polyfit(dphi_rad, z_corr.real, 3 if dphi_rad.size >= 6 else 1)
    x_curve_v = np.polyval(x_poly, grid_rad)
    slope_x = float(coef[1].real)
    eq_fit = None
    if dphi_rad.size >= 6:
        try:
            span_rad = max(float(np.max(np.abs(dphi_rad))), 0.05)
            eq_fit = signal_fit.fit_eq222_local(dphi_rad, z_corr.imag, max_offset_rad=span_rad)
        except FRAME_ERRORS:
            eq_fit = None
    if eq_fit is not None:
        k0_val = float(eq_fit["k0"])
        k0_se = float(eq_fit["k0_se"])
        y0_val = float(eq_fit["y0"])
        x0_val = float(np.polyval(x_poly, eq_fit["phi_offset_rad"]))
        y_curve_v = signal_fit.eq222_y(grid_rad, eq_fit["y0"], eq_fit["k0"], eq_fit["b_r2"], eq_fit["phi_offset_rad"])
        rms_v = float(eq_fit["rms_v"])
        r_val = float(eq_fit["r"])
        mode = "eq_2_2_2"
    else:
        k0_val = float(coef[1].imag)
        k0_se = float("nan")
        y0_val = float(coef[0].imag)
        x0_val = float(coef[0].real)
        y_curve_v = y0_val + k0_val * grid_rad
        rms_v = float(np.sqrt(np.mean((z_corr.imag - (y0_val + k0_val * dphi_rad)) ** 2)))
        r_val = 0.0
        mode = "linear"
    return dict(center_deg=center_deg, dphi_deg=dphi_deg, z_corr=z_corr,
                x0=x0_val, y0=y0_val, k0=k0_val, k0_se=k0_se, slope_x=slope_x,
                grid_deg=grid_deg, x_curve_v=x_curve_v, y_curve_v=y_curve_v,
                rms_v=rms_v, r=r_val, mode=mode)


def coarse_plot_data(run_dir, config):
    """A03 整周粗扫的绘图数据；转调 ``z_chain_common.phase_plot`` 公共实现。"""
    return _coarse_plot_data(run_dir, config)


def local_plot_data(run_dir, config, calibration):
    """A04 局部标定的绘图数据；供复用 A04 图的其它实验调用。"""
    return _local_plot_data(run_dir, config, calibration)


def _save_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def _diagnostic(ax, text):
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes)


def _add_zoom_x_inset(parent, x_mv, y_mv, color_vals, cmap, *, line_x=None, line_y=None,
                      star_xy=None, title="X zoomed", bounds=(0.08, 0.14, 0.34, 0.72), s=12):
    ax_in = parent.inset_axes(list(bounds))
    if line_x is not None and line_y is not None:
        ax_in.plot(line_x, line_y, "-", color="#888888", lw=0.8, alpha=0.7)
    ax_in.scatter(x_mv, y_mv, c=color_vals, cmap=cmap, s=s, edgecolor="#222222", lw=0.25, zorder=3)
    if star_xy is not None:
        ax_in.plot(star_xy[0], star_xy[1], marker="*", markersize=7.5, color="#d55e00", linestyle="none", zorder=4)
    x_pad = max(0.2 * float(np.ptp(x_mv)), 1.0)
    y_pad = max(0.08 * float(np.ptp(y_mv)), 1.0)
    ax_in.set_xlim(float(x_mv.min()) - x_pad, float(x_mv.max()) + x_pad)
    ax_in.set_ylim(float(y_mv.min()) - y_pad, float(y_mv.max()) + y_pad)
    ax_in.tick_params(labelsize=6.5)
    ax_in.set_title(title, fontsize=7, pad=2)
    ax_in.grid(alpha=0.25, lw=0.4)


def coarse_figure(coarse_data):
    """构建 A03 整周相位响应图（相图 + X/Y 波形），即 ``_plots`` 的 Fig 1；转调公共实现。"""
    return phase_plot.coarse_figure(coarse_data)


def local_figure(local_data):
    """构建 A04 局部标定图（局部相图 + Eq.(2.2.2) 响应），即 ``_plots`` 的 Fig 2。"""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from ...plotting import figure_size

    # --- Fig 2: A04 Local calibration (Left = Portrait matching A03, Right = Eq.(2.2.2) response) ---
    fig = Figure(figsize=figure_size("wide", height_mm=90), layout="constrained")
    FigureCanvasAgg(fig)
    ax_locus, ax_resp = fig.subplots(1, 2)
    if local_data is not None:
        dphi = local_data["dphi_deg"]
        zc = local_data["z_corr"]
        x_mv, y_mv = zc.real * 1e3, zc.imag * 1e3
        order_loc = np.argsort(dphi)
        ax_locus.plot(local_data["x_curve_v"] * 1e3, local_data["y_curve_v"] * 1e3,
                      "-", color="#777777", lw=0.9, alpha=0.75)
        sc = ax_locus.scatter(x_mv, y_mv, c=dphi, cmap="coolwarm", s=16, edgecolor="#222222", lw=0.25, zorder=3)
        fig.colorbar(sc, ax=ax_locus, label="Phase offset dphi (deg)", pad=0.02)
        ax_locus.plot(local_data["x0"] * 1e3, local_data["y0"] * 1e3, marker="*", markersize=9,
                      color="#111111", linestyle="none", zorder=4, label="Work point")
        x_mid = 0.5 * (float(x_mv.min()) + float(x_mv.max()))
        y_mid = 0.5 * (float(y_mv.min()) + float(y_mv.max()))
        half = max(0.5 * float(np.ptp(x_mv)), 0.5 * float(np.ptp(y_mv)), 1e-3) * 1.15
        ax_locus.set(xlim=(x_mid - half, x_mid + half), ylim=(y_mid - half, y_mid + half),
                     xlabel="Demod 4 X (mV)", ylabel="Demod 4 Y (mV)", title="Local (X, Y) portrait (1:1 scale)")
        ax_locus.set_aspect("equal", adjustable="box")
        ax_locus.legend(fontsize=7.5, loc="upper right")
        if np.ptp(x_mv) < 0.25 * max(float(np.ptp(y_mv)), 1e-6):
            _add_zoom_x_inset(ax_locus, x_mv, y_mv, dphi, "coolwarm",
                              line_x=x_mv[order_loc], line_y=y_mv[order_loc], title="X zoomed",
                              bounds=(0.09, 0.14, 0.34, 0.72), s=13)

        dx_mv = (zc.real - local_data["x0"]) * 1e3
        dy_mv = (zc.imag - local_data["y0"]) * 1e3
        ax_resp.axhline(0.0, color="#888888", lw=0.7)
        ax_resp.axvline(0.0, color="#888888", lw=0.7, ls="--")
        fit_lbl = ("Eq. (2.2.2) fit" if local_data["mode"] == "eq_2_2_2" else "Linear fit")
        ax_resp.plot(dphi, dy_mv, "o", markersize=3.5, color="#d55e00", label=f"Y - Y0 data (n={dphi.size})")
        ax_resp.plot(local_data["grid_deg"], (local_data["y_curve_v"] - local_data["y0"]) * 1e3,
                     "-", color="#d55e00", lw=1.6,
                     label=f"{fit_lbl}: K0 = {local_data['k0']:.4g} V/rad (RMS {local_data['rms_v']*1e3:.2f} mV)")
        ratio = (abs(local_data["slope_x"] / local_data["k0"]) if local_data["k0"] else float("nan"))
        ratio_txt = f" | |slope_X / K0| = {ratio:.1%}" if math.isfinite(ratio) else ""
        ax_resp.plot(dphi, dx_mv, "s", markersize=3.2, color="#1f77b4",
                     label=f"X - X0 (slope = {local_data['slope_x']:.4g} V/rad)")
        ax_resp.plot(local_data["grid_deg"], (local_data["x_curve_v"] - local_data["x0"]) * 1e3,
                     "-", color="#1f77b4", lw=1.1)
        all_r = np.concatenate((dy_mv, dx_mv))
        r_lo, r_hi = float(all_r.min()), float(all_r.max())
        r_sp = max(r_hi - r_lo, 1e-3)
        ax_resp.set(xlabel="Phase offset Δφ (deg)", ylabel="Response relative to crossing (mV)",
                    ylim=(r_lo - 0.16 * r_sp, r_hi + 0.36 * r_sp),
                    title="Local X & Y phase response vs Eq. (2.2.2)")
        ax_resp.legend(fontsize=7.0, loc="upper left")
        fig.suptitle(f"A04: Local phase response near {local_data['center_deg']:.2f}° | K0 = {local_data['k0']:.4g} V/rad{ratio_txt}")
    else:
        _diagnostic(ax_locus, "No usable A04 local calibration frames")
        _diagnostic(ax_resp, "No usable A04 local calibration frames")
        ax_locus.set(xlabel="Demod 4 X (mV)", ylabel="Demod 4 Y (mV)", title="Local (X, Y) portrait")
        ax_resp.set(xlabel="Phase offset Δφ (deg)", ylabel="Response (mV)", title="Local phase response")
        fig.suptitle("A04: Local phase response")
    return fig


def _plots(directory, rows, accepted, summary, frequency, asd, phase, band_ids, phase_lor, *,
           coarse_data=None, local_data=None):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from ...plotting import figure_size, plot_style_context

    diagnostic = _diagnostic

    def figure_pair():
        figure = Figure(figsize=figure_size("wide", height_mm=115), layout="constrained")
        FigureCanvasAgg(figure)
        return figure, figure.subplots(2, 1, sharex=True)

    with plot_style_context("paper"):
        # --- Fig 1: A03 整周相位响应（coarse_figure 复用同一绘制逻辑） ---
        fig = coarse_figure(coarse_data)
        fig.savefig(directory / RESPONSE_PNG, dpi=240)
        check_cancelled()

        # --- Fig 2: A04 局部标定（local_figure 复用同一绘制逻辑） ---
        fig = local_figure(local_data)
        fig.savefig(directory / LOCAL_PNG, dpi=240)
        check_cancelled()

        check_cancelled()

        # --- Fig 3: A05 Transfer response & 0-Hz sqrt-Lorentzian normalization ---
        fig, axes = figure_pair()
        lor_fit = summary.get("transfer_lorentzian_fit") or {}
        lor_ok = bool(lor_fit.get("available"))
        clean_primary = [row for row in accepted.values() if row["quality_passed"] and row.get("h_v_per_rad", 0) > 0]
        warn_primary = [row for row in accepted.values() if not row["quality_passed"] and row.get("h_v_per_rad", 0) > 0]
        clean_repeat = [row for row in rows if row["repeat"] and row["accepted"] and row["quality_passed"]
                        and row.get("h_v_per_rad", 0) > 0]
        warn_repeat = [row for row in rows if row["repeat"] and row["accepted"] and not row["quality_passed"]
                       and row.get("h_v_per_rad", 0) > 0]
        series = (
            (clean_primary, "o", "#1f77b4", "Primary (no warnings)"),
            (warn_primary, "^", "#d55e00", "Primary (with warnings)"),
            (clean_repeat, "x", "#009e73", "Repeat (no warnings)"),
            (warn_repeat, "d", "#cc3311", "Repeat (with warnings)"),
        )
        norm_key = "normalized_h0_gain" if lor_ok else "normalized_gain"
        norm_err = "normalized_h0_gain_se" if lor_ok else "normalized_gain_se"
        norm_ylabel = "Normalized |H(f)| / H0 (0 Hz)" if lor_ok else "Normalized response |H| / |K0|"

        f_all = [r["frequency_hz"] for sel, _, _, _ in series for r in sel]
        f_grid = (np.geomspace(0.8 * min(f_all), 1.2 * max(f_all), 200) if f_all else np.array([]))
        cal_k0 = _number(summary.get("calibration", {}).get("k0_v_per_rad"))

        for ax, key, error, ylabel in zip(axes, ("h_v_per_rad", norm_key),
                                         ("h_fit_se_v_per_rad", norm_err),
                                         ("Magnitude |H| (V/rad)", norm_ylabel)):
            plotted_any = False
            if key == norm_key and any(r.get(key, 0) > 0 for sel, _, _, _ in series for r in sel):
                ax.axhline(1.0, color="#666666", linestyle="--", linewidth=0.9)
                if not lor_ok:
                    ax.axhspan(CLOSURE_RANGE[0], CLOSURE_RANGE[1], color="#888888", alpha=0.12)
            for selection, marker, color, label in series:
                points = [r for r in selection if (r.get(key) or 0) > 0]
                if not points:
                    continue
                plotted_any = True
                ax.plot([r["frequency_hz"] for r in points], [r[key] for r in points],
                        linestyle="none", marker=marker, color=color, label=label)
                bars = [r for r in points if r.get(error) is not None and r[error] < r[key]]
                if bars:
                    ax.errorbar([r["frequency_hz"] for r in bars], [r[key] for r in bars],
                                yerr=[r[error] for r in bars], fmt="none", ecolor=color, capsize=2, alpha=0.85)
            if lor_ok and f_grid.size:
                h0_fit, fc_fit = float(lor_fit["h0_v_per_rad"]), float(lor_fit["fc_hz"])
                if key == "h_v_per_rad":
                    ax.plot(f_grid, h0_fit / np.sqrt(1.0 + (f_grid / fc_fit) ** 2), "-", color="#1f77b4", lw=1.4,
                            label=f"sqrt-Lorentzian fit (H0={h0_fit:.3g} V/rad, fc={fc_fit:.1f} Hz)")
                    if cal_k0 is not None and abs(cal_k0) > 0:
                        ax.axhline(abs(cal_k0), color="#d55e00", ls=":", lw=1.2,
                                   label=f"A04 K0 = {abs(cal_k0):.3g} V/rad")
                else:
                    ax.plot(f_grid, 1.0 / np.sqrt(1.0 + (f_grid / fc_fit) ** 2), "-", color="#1f77b4", lw=1.4,
                            label=f"1 / sqrt(1 + (f/{fc_fit:.1f} Hz)^2)")
            elif key == "h_v_per_rad" and cal_k0 is not None and abs(cal_k0) > 0 and plotted_any:
                ax.axhline(abs(cal_k0), color="#d55e00", ls=":", lw=1.2, label=f"A04 K0 = {abs(cal_k0):.3g} V/rad")
            ax.set(xscale="log", ylabel=ylabel)
            ax.grid(True, which="both", alpha=0.2)
            if plotted_any:
                ax.set_yscale("log")
                ax.legend(fontsize=7.5, loc="lower left")
            else:
                diagnostic(ax, "No usable magnitude estimates" if ax is axes[0] else "No usable normalized estimates")
        n = len(summary["configured_frequencies_hz"])
        clean_n = summary["clean_primary_points"]
        warn_n = len(accepted) - clean_n
        title = f"A05 Transfer: {len(accepted)}/{n} plotted ({clean_n} clean, {warn_n} warned)"
        if lor_ok:
            title += f" | H0={lor_fit['h0_v_per_rad']:.3g}, fc={lor_fit['fc_hz']:.1f} Hz"
            if (ratio := lor_fit.get("h0_to_k0_ratio")) is not None:
                title += f" | H0/K0={ratio:.3f}"
        if summary["anchor_repeat"].get("configured", True):
            title += f" | Repeat: {'pass' if summary['anchor_repeat']['passed'] else 'not passed'}"
        fig.suptitle(title)
        axes[-1].set_xlabel("Modulation frequency (Hz)")
        fig.savefig(directory / TRANSFER_PNG, dpi=240)
        check_cancelled()

        # --- Fig 4: A06 Phase sensitivity spectrum (urad/sqrt(Hz), full-band sqrt-Lorentzian back-fill) ---
        fig, (detector, referred) = figure_pair()
        detector.set(xscale="log", ylabel=r"Detector Y ASD (V/$\sqrt{\mathrm{Hz}}$)")
        referred.set(xscale="log", xlabel="Frequency (Hz)", ylabel=r"Phase sensitivity ASD ($\mu\mathrm{rad}/\sqrt{\mathrm{Hz}}$)")
        if (asd > 0).any():
            detector.loglog(frequency, np.where(asd > 0, asd, np.nan), lw=0.9)
            detector.grid(True, which="both", alpha=0.2)
        else:
            diagnostic(detector, "Detector Y ASD is zero" if frequency.size else "No valid detector Y spectrum")
        phase_urad = phase * 1e6
        phase_lor_urad = phase_lor * 1e6
        if summary["phase_referral"]["available"]:
            has_warnings = bool(summary["advisory_warnings"])
            band_color = "#d55e00" if has_warnings else "#009e73"
            lor_band = summary["phase_referral"].get("lorentzian_full_band") or {}
            plotted_sens = False
            if lor_band.get("available") and (phase_lor_urad > 0).any():
                plotted_sens = True
                referred.loglog(frequency, np.where(phase_lor_urad > 0, phase_lor_urad, np.nan),
                                color="#1f77b4", lw=0.95, alpha=0.85,
                                label=f"Full-band sqrt-Lorentzian back-fill (median {lor_band['median_urad_per_sqrt_hz']:.3g} urad/sqrt(Hz))")
                fc_val = float(lor_band["fc_hz"])
                if frequency.size and frequency[0] <= fc_val <= frequency[-1]:
                    referred.axvline(fc_val, color="#7f7f7f", ls=":", lw=1.0, label=f"fc = {fc_val:.1f} Hz")
            bands_info = summary["phase_referral"]["bands"]
            med_txt = ", ".join(f"{b['median_urad_per_sqrt_hz']:.3g}" for b in bands_info)
            prefix = "Measured band (with warnings)" if has_warnings else "Measured band direct interp"
            label = f"{prefix} (median {med_txt} urad/sqrt(Hz))"
            for idx, band in enumerate(bands_info):
                bins = band_ids == band["transfer_band"]
                if (phase_urad[bins] > 0).any():
                    plotted_sens = True
                    referred.loglog(frequency[bins], np.where(phase_urad[bins] > 0, phase_urad[bins], np.nan),
                                    color=band_color, lw=1.3, marker="." if bins.sum() == 1 else None,
                                    label=label if idx == 0 else None)
            referred.grid(True, which="both", alpha=0.2)
            if not plotted_sens:
                diagnostic(referred, "Referred phase ASD is zero")
            else:
                referred.legend(fontsize=7.3, loc="lower left")
        else:
            diagnostic(referred, "Phase referral withheld; see summary")
        fig.suptitle("A06: Phase sensitivity spectrum")
        fig.savefig(directory / NOISE_PNG, dpi=240)


def analyze_frames(run_dir, config, params, expected_fingerprint):
    """全量计算可解析的频响与等效相位噪声；参考质检超限记为告警但不扣留绘图。"""
    configured = sorted(params.pm_frequencies_hz)
    config_calibration = _mapping(config.get("calibration"))
    calibration = {key: value for key in CALIBRATION_KEYS if (value := _number(config_calibration.get(key))) is not None}
    cal_warnings = [w for w in config_calibration.get("quality_warnings") or [] if isinstance(w, str) and w]
    if config_calibration.get("accepted") is not True and not cal_warnings:
        calibration.pop("k0_v_per_rad", None)
        calibration.pop("k0_se_v_per_rad", None)
    if reason := _text(config_calibration.get("reason")):
        calibration["reason"] = reason
    calibration["quality_warnings"] = cal_warnings
    calibration["quality_passed"] = (config_calibration.get("accepted") is True) and not cal_warnings
    attempts = config.get("transfer")
    rows, reference = _transfer_rows(run_dir, [_mapping(item) for item in attempts] if isinstance(attempts, list) else [],
                                    configured, calibration)
    accepted = {row["frequency_hz"]: row for row in rows if row["accepted"] and not row["repeat"]}
    lorentzian_fit = _fit_transfer_lorentzian(rows, accepted, calibration.get("k0_v_per_rad"))
    predicted_sweep = _mapping(config.get("predicted")).get("pm_sweep_order_hz")
    repeat_expected = isinstance(predicted_sweep, list) and len(predicted_sweep) > len(set(predicted_sweep))
    closure, repeat = _gates(rows, accepted, configured[0], params.pm_anchor_hz, repeat_expected=repeat_expected)
    bands = _bands(configured, accepted)
    frequency, psd, noise = _noise(
        run_dir,
        _mapping(config.get("noise")),
        rows,
        reference,
        params.welch_segment_s,
        expected_segments=params.noise_n_avg,
    )
    asd = np.sqrt(psd)
    block_reasons = list(noise["reasons"])
    if not bands:
        block_reasons.append("相邻有效主测频点不足，不能在任何频带插值")
    metrics = []
    if not block_reasons:
        h, phase, band_ids, metrics, h_lor, phase_lor, lor_summary = _refer_noise(
            frequency, asd, bands, accepted, lorentzian_fit=lorentzian_fit
        )
        if not metrics:
            block_reasons.append("有效频带内没有可折算的 Welch 频点")
    else:
        h = phase = h_lor = phase_lor = np.full(frequency.shape, np.nan)
        band_ids = np.zeros(frequency.shape, dtype=int)
        lor_summary = dict(available=False, reason="存在阻断噪声折算的完整性故障")

    advisory_warnings = []
    for text in cal_warnings:
        advisory_warnings.append(f"静态标定：{text}")
    pm_align = _mapping(config.get("pm_alignment"))
    align_warnings = pm_align.get("quality_warnings") or ([pm_align["reason"]] if _text(pm_align.get("reason")) else [])
    for text in align_warnings:
        if isinstance(text, str) and text:
            advisory_warnings.append(f"调制对准：{text}")
    for row in rows:
        if row["accepted"] and row["warnings"]:
            tag = f"{row['frequency_hz']:g} Hz（复测）" if row["repeat"] else f"{row['frequency_hz']:g} Hz"
            advisory_warnings.append(f"频响 {tag}：{row['warnings']}")
    for gate in (closure, repeat):
        if not gate["passed"] and gate.get("reason"):
            advisory_warnings.append(gate["reason"])
    advisory_warnings.extend(noise["warnings"])
    advisory_warnings = list(dict.fromkeys(advisory_warnings))

    status = _text(config.get("completion_status"))
    complete = bool(metrics) and len(accepted) == len(configured) and status == "completed"
    clean_primary = sum(1 for row in accepted.values() if row["quality_passed"])
    quality_passed = bool(metrics) and not block_reasons and not advisory_warnings
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                   analysis_version=ANALYSIS_VERSION, input_fingerprint=expected_fingerprint,
                   acquisition_status=status, acquisition_failure_reason=_text(config.get("failure_reason")),
                   result_status="complete" if complete else "partial" if rows or frequency.size else "no_usable_data",
                   quality_passed=quality_passed, advisory_warnings=advisory_warnings,
                   calibration=calibration, lockin_reference=reference,
                   configured_frequencies_hz=configured, transfer_attempts=len(rows),
                   accepted_primary_points=len(accepted), clean_primary_points=clean_primary,
                   k0_closure=closure, anchor_repeat=repeat, transfer_lorentzian_fit=lorentzian_fit,
                   transfer_bands_hz=[[b[0], b[-1]] for b in bands],
                   minimum_y_tone_snr=MIN_TONE_SNR, minimum_tone_cycles=MIN_TONE_CYCLES, noise=noise,
                   phase_referral=dict(available=bool(metrics), reasons=block_reasons, advisory_warnings=advisory_warnings,
                                       bands=metrics, lorentzian_full_band=lor_summary,
                                       minimum_frequency_hz=MIN_NOISE_HZ,
                                       interpolation="log_frequency_log_magnitude_within_adjacent_accepted_points",
                                       backfill="zero_centered_sqrt_lorentzian_full_band"),
                   method_notes=["频响只比较调制深度与锁相 Y 音调幅值 |H(f)| = |Y_pk(f)| / phi_pk(f)，不计算复频响相角；与基频/二次谐波不碰撞的 50–250 Hz 工频谐波在最小二乘正交解调中一并投影去除。",
                                 "A05 对 |H(f)| 拟合以 0 Hz 为中心的 √Lorentzian 线型 H0 / sqrt(1 + (f/fc)²)（若非碰撞点 ≥ 2 个则自动剔除 50–250 Hz 工频整倍频碰撞点）并以零频截距 H0 归一化；A05/A06 使用宽带滤波（默认 1 µs），不再做锁相软件滤波反卷积。",
                                 "A06 灵敏度谱以 µrad/√Hz 输出：多温控分段各自计算 Welch PSD 后按段数等权平均，不跨恢复温控窗口拼接波形；除实测频点插值带外，同时按 √Lorentzian 传递函数对全频段噪声做外推回补。",
                                 "H 与标准误仅传播 Y 拟合误差（含时间序列相关修正）；归一增益另外传播 K0/H0 标准误，不含激励幅度标定不确定度。",
                                 "低频闭合（|H|/|K0| 0.8–1.2）等参考指标仅作告警与图例标注，不阻止有效频点参与出图与相位噪声折算。"])
    invalid_sections = [key for key, kind in (("calibration", dict), ("transfer", list), ("noise", dict))
                        if config.get(key) is not None and not isinstance(config[key], kind)]
    if invalid_sections:
        summary["invalid_metadata_sections"] = invalid_sections

    def noise_rows():
        for f, p, a, band, gain, value, gl, vl in zip(frequency, psd, asd, band_ids, h, phase, h_lor, phase_lor):
            ok_band = bool(band and math.isfinite(value))
            ok_lor = bool(lor_summary.get("available") and math.isfinite(vl) and math.isfinite(gl))
            yield (f, p, a, int(band) or None,
                   gain if ok_band else None,
                   value if ok_band else None,
                   math.degrees(value) if ok_band else None,
                   value * 1e6 if ok_band else None,
                   gl if ok_lor else None,
                   vl * 1e6 if ok_lor else None)

    coarse_data = _coarse_plot_data(run_dir, config)
    local_data = _local_plot_data(run_dir, config, calibration)
    results = output_path(run_dir, "results")
    with TemporaryDirectory(dir=results, prefix=".phase-analysis-") as temporary:
        temp = Path(temporary)
        write_state(temp / SUMMARY, summary)
        _save_csv(temp / TRANSFER_CSV, TRANSFER_COLUMNS, ([row.get(key) for key in TRANSFER_COLUMNS] for row in rows))
        _save_csv(temp / NOISE_CSV, NOISE_COLUMNS, noise_rows())
        _plots(temp, rows, accepted, summary, frequency, asd, phase, band_ids, phase_lor,
               coarse_data=coarse_data, local_data=local_data)
        check_cancelled()
        if fingerprint(run_dir) != expected_fingerprint:
            raise ValueError("离线分析期间输入发生变化，请重新执行 B01")
        for name in ARTIFACTS:
            (temp / name).replace(output_path(run_dir, f"results/{name}"))
    return {key: value for key, value in summary.items() if key != "input_fingerprint"}
