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

from ...analysis_execution import file_signature as signature
from ...experiment_runtime import check_cancelled
from ...workflow_state import write_state
from ..z_chain_common import phase_plot, spectrum_plot, tone_fit, transfer, welch_psd
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

# 音调拟合（含工频与 5 kHz 载波泄漏模型）、Welch 谱、√Lorentzian 归一化与绘图实现都在
# z_chain_common；这里保留旧名字供测试、历史脚本与产物列名使用。
MIN_TONE_SNR = tone_fit.MIN_TONE_SNR
MIN_TONE_CYCLES = tone_fit.MIN_TONE_CYCLES
MIN_NOISE_HZ = welch_psd.MIN_NOISE_HZ
MIN_WELCH_SEGMENTS = welch_psd.MIN_WELCH_SEGMENTS
MAINS_HARMONICS_HZ = tone_fit.MAINS_HARMONICS_HZ
CARRIER_HARMONICS_HZ = tone_fit.CARRIER_HARMONICS_HZ
CARRIER_RATE_FRACTION = tone_fit.CARRIER_RATE_FRACTION
CLOSURE_RANGE = transfer.CLOSURE_RANGE
PLOT_FREQUENCY_RANGE_HZ = spectrum_plot.PLOT_FREQUENCY_RANGE_HZ
REPEAT_FLOOR = 0.15
REPEAT_CAP = 0.30
REPEAT_SIGMA = 2.0
STEP_RTOL = tone_fit.STEP_RTOL
RATE_RTOL = tone_fit.RATE_RTOL
DAQ_STREAM_RTOL = 0.003
GRID_ATOL_SAMPLES = tone_fit.GRID_ATOL_SAMPLES
LOCKIN_PHASE_ATOL_DEG = 1e-3
LOCKIN_RTOL = 1e-6

FRAME_ERRORS = (OSError, ValueError, TypeError, KeyError, EOFError, BadZipFile, np.linalg.LinAlgError)
CALIBRATION_KEYS = ("k0_v_per_rad", "k0_se_v_per_rad", "burst_command_deg", "carrier_center_deg",
                    "slope_x_v_per_rad", "lockin_phase_deg")
LOCKIN_KEYS = ("phase_deg", "order", "time_constant_s", "stream_rate_sa_s", "oscillator_hz")
TRANSFER_COLUMNS = (
    "attempt", "frequency_hz", "repeat", "scope_label", "xy_label", "electrical_accepted", "electrical_reason",
    "phase_pk_rad", "phase_scatter_rad", "am_fraction_pk", "center_error_deg", "rate_sa_s", "samples", "cycles",
    "y_pk_v", "y_pk_se_v", "y_snr", "y_pk_se_clean_v", "y_snr_clean", "x_pk_v", "x_2f_pk_v", "y_2f_pk_v",
    "h_v_per_rad", "h_fit_se_v_per_rad", "normalized_gain", "normalized_gain_se",
    "normalized_h0_gain", "normalized_h0_gain_se", "mains_collision",
    "carrier_terms_hz", "carrier_skipped",
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


sample_grid = tone_fit.sample_grid





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










fit_tone = tone_fit.fit_tone



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
                fit = fit_tone(arrays["time_s"], arrays["x_v"], arrays["y_v"], metadata["rate_sa_s"], f,
                               correlation_s=state["order"] * state["time_constant_s"])
                collisions = fit.pop("mains_collision_hz", [])
                fit.pop("mains_harmonics_hz", None)
                fit.pop("mains_guard_hz", None)
                carrier_terms = fit.pop("carrier_terms_hz", [])
                carrier_skipped = fit.pop("carrier_skipped", [])
                row.update(fit)
                row["mains_collision"] = "，".join(f"{value:g}" for value in collisions)
                row["carrier_terms_hz"] = "，".join(f"{value:g}" for value in carrier_terms)
                row["carrier_skipped"] = "；".join(carrier_skipped)
                # 质检与告警使用联合扣除工频与载波泄漏后的 clean 口径；原始口径同时写入同一告警便于复算。
                if row["y_snr_clean"] < MIN_TONE_SNR:
                    warnings.append(f"Y 音调信噪比低于 {MIN_TONE_SNR:g}"
                                    f"（原始 {row['y_snr']:.2f}，扣除工频与载波泄漏后 {row['y_snr_clean']:.2f}）")
                if (phase := calibration.get("lockin_phase_deg")) is not None and not _same_phase(state["phase_deg"], phase):
                    hard_reasons.append("频响帧锁相相位与静态标定不一致")
                if reference is not None and not _same_lockin(state, reference):
                    hard_reasons.append("频响帧锁相状态与首个有效频点不一致")
            except FRAME_ERRORS as exc:
                hard_reasons.append(f"XY 帧或音调拟合无效：{exc}")
        else:
            hard_reasons.append("缺少 XY 帧")
        if not hard_reasons:
            # 增益沿用同一目标音调幅值；标准误取扣除工频与载波泄漏后的 clean 口径，避免干扰能量虚高误差棒。
            h, h_se = row["y_pk_v"] / pm, row["y_pk_se_clean_v"] / pm
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


_bands = transfer.transfer_bands



_welch = welch_psd.welch_spectrum



welch_segmented = welch_psd.segment_average



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


_fit_transfer_lorentzian = transfer.fit_transfer_lorentzian



_refer_noise = transfer.refer_phase_asd



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


_plot_band = spectrum_plot._plot_band



_referral_plot_style = spectrum_plot._referral_plot_style



_draw_phase_sensitivity = spectrum_plot._draw_phase_sensitivity



_transfer_gain_groups = spectrum_plot._transfer_gain_groups



_draw_transfer_gain = spectrum_plot._draw_transfer_gain



phase_noise_figure = spectrum_plot.phase_noise_figure



phase_transfer_figure = spectrum_plot.phase_transfer_figure



def _plots(directory, rows, accepted, summary, frequency, phase, band_ids, phase_lor, *,
           coarse_data=None, local_data=None):
    from ...plotting import plot_style_context

    with plot_style_context("paper"):
        # --- Fig 1: A03 整周相位响应（coarse_figure 复用同一绘制逻辑） ---
        fig = coarse_figure(coarse_data)
        fig.savefig(directory / RESPONSE_PNG, dpi=240)
        check_cancelled()

        # --- Fig 2: A04 局部标定（local_figure 复用同一绘制逻辑） ---
        fig = local_figure(local_data)
        fig.savefig(directory / LOCAL_PNG, dpi=240)
        check_cancelled()

        # --- Fig 3: A05/A06 合并双纵轴图（左轴相位灵敏度、右轴归一化频响） ---
        fig = phase_transfer_figure(rows, accepted, summary, frequency, phase, phase_lor, band_ids)
        fig.savefig(directory / TRANSFER_PNG, dpi=240)
        check_cancelled()

        # --- Fig 4: A06 相位灵敏度单面板（µrad/√Hz） ---
        fig = phase_noise_figure(frequency, phase, phase_lor, band_ids, summary,
                                 title="A06: Phase sensitivity spectrum")
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

    # 折算可用性与质量分开：只要存在 ≥2 个相邻硬合格频点就插值输出（即使 quality_passed=False），
    # 质检不足只标 advisory 与 referral_warnings，不再整段扣留折算曲线。
    referral_available = bool(metrics)
    referral_warnings = list(advisory_warnings)
    referral_quality = "ok" if referral_available and not referral_warnings else "advisory"

    status = _text(config.get("completion_status"))
    complete = bool(metrics) and len(accepted) == len(configured) and status == "completed"
    clean_primary = sum(1 for row in accepted.values() if row["quality_passed"])
    quality_passed = bool(metrics) and not block_reasons and not advisory_warnings
    summary = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                   analysis_version=ANALYSIS_VERSION, input_fingerprint=expected_fingerprint,
                   acquisition_status=status, acquisition_failure_reason=_text(config.get("failure_reason")),
                   result_status="complete" if complete else "partial" if rows or frequency.size else "no_usable_data",
                   quality_passed=quality_passed, advisory_warnings=advisory_warnings,
                   referral_available=referral_available, referral_warnings=referral_warnings,
                   referral_quality=referral_quality,
                   transfer_plot_title="A05/A06: Phase sensitivity ASD (left) and |H(f)| / H0 (right)",
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
                                 "A05 行内 y_pk_se_v / y_snr 保留未扣工频与载波泄漏的原始口径，y_pk_se_clean_v / y_snr_clean 为联合扣除 50–250 Hz 工频谐波与 5 kHz 载波泄漏（含 10/15/20 kHz 谐波）后的 clean 口径；质检与告警按 clean SNR 判定，mains_collision 记录与目标音调或二次谐波共线而跳过的工频谐波，carrier_terms_hz / carrier_skipped 记录实际纳入与跳过的载波泄漏项。",
                                 "A05 的 5 kHz 载波泄漏来自 Demod 4 宽带解调（DEMOD0_DYNAMIC_TC = 1 µs）：输入直流偏置经解调混频落到载波频率，10/15/20 kHz 为其谐波；它是读出链已知寄生项、不是原子信号，只作为回归干扰项扣除，不与目标音调幅值一起发布。",
                                 "A05 对 |H(f)| 拟合以 0 Hz 为中心的 √Lorentzian 线型 H0 / sqrt(1 + (f/fc)²)（若非碰撞点 ≥ 2 个则自动剔除 50–250 Hz 工频整倍频碰撞点）并以零频截距 H0 归一化；A05/A06 使用宽带滤波（默认 1 µs），不再做锁相软件滤波反卷积。",
                                 "A06 灵敏度谱以 µrad/√Hz 输出：多温控分段各自计算 Welch PSD 后按段数等权平均，不跨恢复温控窗口拼接波形；只要存在 ≥2 个相邻硬合格频点就做对数插值与全频段回补，质检不足只标 referral_quality=advisory，不再整段扣留。",
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
        _plots(temp, rows, accepted, summary, frequency, phase, band_ids, phase_lor,
               coarse_data=coarse_data, local_data=local_data)
        check_cancelled()
        if fingerprint(run_dir) != expected_fingerprint:
            raise ValueError("离线分析期间输入发生变化，请重新执行 B01")
        for name in ARTIFACTS:
            (temp / name).replace(output_path(run_dir, f"results/{name}"))
    return {key: value for key, value in summary.items() if key != "input_fingerprint"}
