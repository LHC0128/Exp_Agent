"""标准 Mx（不打脉冲）射频相位图，以及用 theta 与用 y 求噪声谱算相位灵敏度的差异。

问题（O1）：射频相位灵敏度现有方案打 Z 双脉冲，必须用脉冲理论式拟合。标准 Mx 构型下
（GS200 常数主场 + Z 脉冲链关闭 + MFLI Out1 连续 5 kHz 载波）相图是否为圆、各相位处
斜率是否接近；在同一工作点采集 XY 时间序列后，用 theta=atan2(y-y0, x-x0) 的功率谱
直接得到相位噪声，和用 y 的功率谱除以斜率 K=|dy/dtheta| 得到的相位噪声，是否一致。

流程：
1. 门控（.lab_runtime）通过后读取 rf-phase-sensitivity 默认参数（--vpp / --freq-hz /
   --k-hz-per-ma / --b-hz 可覆盖）。GS200 主场电流只由标定公式 I=(f-b)/K 反算，
   不手填；--dry-run 只打印计划与电流，不连仪器。
2. 连接设备与参考时钟、准备载波后立即关闭 Z 脉冲链（1200B 与 Z 信号源 OFF 回读），
   把 GS200 设到公式电流；王湾 Pump 必须初始为 ON，收尾恢复原状态。
3. 相图：按 --grid-deg（默认 24 点）连续改写 MFLI 载波相移，每点一段 XY，
   圆拟合给出圆心、半径与残差，并给出逐段 |dS/dtheta| 与 |dy/dtheta|。
4. 噪声：在 |dy/dtheta| 最大的相位处切到宽带 Demod 4，按独立温控窗口采集
   --segments 段 x --duration 秒的 XY 序列。
5. 分析：Welch 单边 PSD -> 三条路径的相位噪声谱（rad/sqrt(Hz)）：theta 直接、
   y 除以斜率、以及沿切向投影；输出 summary.json 与两张图。
6. finally 恢复 Pump、preparation.park 安全收尾、断开设备；
   产物在 data/rf_phase_sensitivity/_studies/<时间戳>_<tag>/。

用法（运行目录内，开发目录无 .lab_runtime 会被门控拒绝）：
    $env:LAB_DATA_ROOT = "D:/Code/exp_agent_run/data"
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --dry-run
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --tag mx_std_stage1
    # 纯离线重算（不连仪器）：
    agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --analyze <研究目录>
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
from pyvisa.errors import VisaIOError
import yaml

try:
    import hardware_gate as _hg  # noqa: F401
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[3]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from hardware_gate import require_hardware_access, runtime_root
from signal_generator import DG900Instrument

from lab_workflows.common import data_root, load_safety_limits, validate_safety_limit
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_modules.rf_phase_sensitivity import (
    acquisition,
    preparation,
    run_format,
)
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.experiment_modules.z_chain_common import prep as z_prep
from lab_workflows.experiment_modules.z_chain_common.keys import MAIN_FIELD_KEY
from lab_workflows.instrument_config import load_device_definitions
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort, sleep
from lab_workflows.steps.run_directory import write_run_config

PUMP_RESOURCE = "TCPIP0::192.168.124.32::5025::SOCKET"
PUMP_DEVICE_ID = "dg9q275000733"
PUMP_CHANNEL = 1
DEFAULT_K_HZ_PER_MA = 11089.0
DEFAULT_B_HZ = 219.7
FORBIDDEN_TAG_CHARS = set('/:*?"<>|') | {chr(92), chr(0)}
# 与 rf_phase_response / rf_phase_sensitivity 研究同口径的低频段；50/100 Hz 工频另行报告。
BAND_HZ = (3.0, 50.0)
MAINS_HZ = (50.0, 100.0, 150.0, 200.0, 250.0)


@dataclass(frozen=True, slots=True)
class StudyDirectory:
    root: Path
    raw: Path
    results: Path
    config_path: Path

    def update_config(self, **updates: Any) -> dict[str, Any]:
        write_run_config(self.config_path, dict(updates))
        return updates


class StudyContext:
    """复用 rf_phase_sensitivity 采集原语的轻量研究会话上下文。"""

    def __init__(self, params: RfPhaseSensitivityParams, root: Path, directory: StudyDirectory) -> None:
        self.root = Path(root).resolve()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.directory = directory
        self.device_session = DeviceSession()
        self.devices: dict[str, Any] = {}
        self.outputs: dict[str, str] = {}
        self.active = False
        self.main_field_state = None
        self.state = run_format.initial_metadata(self.params)
        self.state["study_type"] = "mx_standard_phase_noise"
        self.state["study_root"] = str(directory.root)

    def save(self) -> None:
        self.directory.update_config(**self.state)


def _create_study_directory(root: Path, tag: str) -> StudyDirectory:
    if any(ch in FORBIDDEN_TAG_CHARS for ch in tag) or not tag.strip():
        raise ValueError(f"非法研究目录标签：{tag!r}")
    studies_root = data_root(root) / run_format.DATA_TYPE / "_studies"
    studies_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m%d_%H%M%S")
    stem = f"{timestamp}_{tag}"
    counter = 1
    while True:
        study_root = studies_root / (stem if counter == 1 else f"{stem}_{counter}")
        try:
            study_root.mkdir()
            break
        except FileExistsError:
            counter += 1
    (study_root / "raw").mkdir()
    (study_root / "results").mkdir()
    return StudyDirectory(root=study_root, raw=study_root / "raw", results=study_root / "results",
                          config_path=study_root / "study_config.yaml")


def _load_params(root: Path, args: argparse.Namespace) -> RfPhaseSensitivityParams:
    defaults_path = root / "params" / "experiments" / f"{run_format.EXPERIMENT_ID}.yaml"
    base = RfPhaseSensitivityParams.from_yaml(defaults_path)
    merged = base.to_external()
    schema_ver = RfPhaseSensitivityParams.schema_version
    local_path = defaults_path.with_name(f"{run_format.EXPERIMENT_ID}.local.yaml")
    if local_path.is_file():
        loaded = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            overlay = loaded.get("parameters", loaded)
            if isinstance(overlay, dict):
                merged.update(overlay)
            if isinstance(loaded.get("schema_version"), int):
                schema_ver = int(loaded["schema_version"])
    if args.vpp is not None:
        merged["Y_RF_VPP"] = float(args.vpp)
    if args.freq_hz is not None:
        merged["Y_RF_FREQ_HZ"] = float(args.freq_hz)
    if args.duration is not None:
        merged["NOISE_DURATION_S"] = float(args.duration)
    if args.segments is not None:
        merged["NOISE_N_AVG"] = int(args.segments)
    if args.temp_lag is not None:
        merged["DYNAMIC_TEMP_SWITCH_ON_LAG_S"] = float(args.temp_lag)
    if args.welch_segment is not None:
        merged["WELCH_SEGMENT_S"] = float(args.welch_segment)
    if args.point_duration is not None:
        merged["STATIC_POINT_DURATION_S"] = float(args.point_duration)
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


def gs200_current_for_frequency_ma(freq_hz: float, k_hz_per_ma: float, b_hz: float) -> float:
    """由主磁场标定式 f = K*I + b 反算 GS200 电流（mA）；只此一条路径，不接受手填覆盖。"""
    for name, value in (("射频频率", freq_hz), ("标定斜率 K", k_hz_per_ma), ("标定截距 b", b_hz)):
        if not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限值：{value}")
    if k_hz_per_ma <= 0:
        raise ValueError(f"标定斜率 K 必须为正：{k_hz_per_ma}")
    return (freq_hz - b_hz) / k_hz_per_ma


def _resolve_pump_config(ctx: StudyContext) -> dict[str, Any]:
    mapped = ctx.mapping.get("Pump_modulation")
    if isinstance(mapped, dict) and mapped.get("resource") == PUMP_RESOURCE:
        ch = int(mapped.get("channel", PUMP_CHANNEL))
        if ch != PUMP_CHANNEL:
            raise SafetyAbort(f"Pump_modulation 当前映射在 CH{ch}，与目标 CH{PUMP_CHANNEL} 不一致")
        return {**mapped, "channel": PUMP_CHANNEL, "mapping_key": "Pump_modulation"}
    library = load_device_definitions(ctx.root)
    defn = library.get(PUMP_DEVICE_ID)
    if defn is None:
        raise SafetyAbort(f"设备库缺少 {PUMP_DEVICE_ID}（王湾pump {PUMP_RESOURCE}）")
    if defn.instrument != "signal_generator" or defn.resource != PUMP_RESOURCE:
        raise SafetyAbort(f"{PUMP_DEVICE_ID} 资源或类型与 {PUMP_RESOURCE} 不符：{defn}")
    return {**defn.to_config(), "channel": PUMP_CHANNEL, "mapping_key": "Pump_modulation"}


def _connect_or_reuse_pump(ctx: StudyContext) -> DG900Instrument | Any:
    pump_cfg = _resolve_pump_config(ctx)
    signals = ctx.devices.get("signals")
    if signals is not None:
        for route in signals.routes.values():
            if getattr(route.device, "resource_string", None) == PUMP_RESOURCE:
                if route.actual_channel == PUMP_CHANNEL:
                    raise SafetyAbort(f"{PUMP_RESOURCE} CH{PUMP_CHANNEL} 已在本实验信号路径中占用，拒绝重复控制")
                return route.device
    device = ctx.device_session.connect(
        "pump",
        f"pump://{PUMP_RESOURCE}",
        lambda: create_signal_generator(pump_cfg, channel=PUMP_CHANNEL, apply_load_policy=False),
    )
    if not isinstance(device, DG900Instrument):
        raise SafetyAbort(f"王湾pump 驱动类型异常：{type(device).__name__}")
    return device


def _set_pump_output(pump_dev: Any, enabled: bool) -> bool:
    last_exc: VisaIOError | None = None
    can_reconnect = callable(getattr(pump_dev, "disconnect", None)) and callable(getattr(pump_dev, "connect", None))
    for attempt in range(3):
        if attempt > 0:
            print(f"[警告] 王湾pump CH{PUMP_CHANNEL} VISA 超时/异常，断开重连重试（{attempt}/2）：{last_exc}", flush=True)
            try:
                pump_dev.disconnect()
            except Exception:
                pass
            try:
                pump_dev.connect()
            except Exception as exc:
                last_exc = exc if isinstance(exc, VisaIOError) else last_exc
                continue
        try:
            pump_dev.set_output(bool(enabled), channel=PUMP_CHANNEL)
            actual = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
            if actual != bool(enabled):
                raise SafetyAbort(f"王湾pump CH{PUMP_CHANNEL} 回读不符：期望 {enabled}，实际 {actual}")
            return actual
        except VisaIOError as exc:
            last_exc = exc
            if attempt == 2 or not can_reconnect:
                raise
    raise SafetyAbort(f"王湾pump CH{PUMP_CHANNEL} 设置失败：{last_exc}")


def _disable_z(ctx: StudyContext) -> dict[str, Any]:
    """关闭 Z 双脉冲链：先关 1200B、再关 Z 信号源输出，并逐项回读。"""
    z_dev, z_ch = preparation._route(ctx, "z")
    if bool(ctx.state.get("amp_output_on")):
        z_prep.set_amp_output(ctx, False)
        if bool(ctx.state.get("amp_output_on")):
            raise SafetyAbort("1200B 功放输出未回读为 OFF")
    if preparation.dg_output(z_dev, z_ch):
        z_dev.set_output(False, channel=z_ch)
        z_dev.wait_for_operation_complete()
        if preparation.dg_output(z_dev, z_ch):
            raise SafetyAbort("Z 信号源输出未回读为 OFF")
    record = dict(amp_output_on=bool(ctx.state.get("amp_output_on")),
                  z_source_output=preparation.dg_output(z_dev, z_ch))
    ctx.state["z_pulse_chain"] = record
    ctx.outputs["Z 通道"] = "脉冲链关闭（标准 Mx：不打脉冲）"
    return record


def _set_main_field_ma(ctx: StudyContext, target_ma: float) -> dict[str, Any]:
    limits = load_safety_limits(ctx.root)
    target_ma = float(target_ma)
    validate_safety_limit(MAIN_FIELD_KEY, target_ma, limits)
    gs200 = ctx.devices["gs200"]
    target_a = target_ma / 1000.0
    cur_a = float(gs200.get_current())
    on_now = bool(gs200.get_output())
    if target_ma == 0.0:
        if on_now:
            gs200.set_output(False)
        if gs200.get_output():
            raise SafetyAbort("GS200 主磁场未回读为 OFF")
        return dict(output_on=False, current_ma=0.0)
    if not (on_now and abs(cur_a - target_a) <= 1e-6):
        limit_a = float(limits[MAIN_FIELD_KEY]["max"]) / 1000.0
        gs200.set_output(False)
        gs200.set_source_function("CURRent")
        if float(gs200.get_current_limit()) < abs(target_a):
            gs200.set_current_limit(limit_a)
        if float(gs200.get_current_range()) < abs(target_a):
            gs200.set_current_range(max(0.01, abs(target_a)))
        gs200.set_current(target_a)
        gs200.set_output(True)
    actual_a = float(gs200.get_current())
    actual_on = bool(gs200.get_output())
    if not actual_on or abs(actual_a - target_a) > max(1e-6, abs(target_a) * 0.01):
        raise SafetyAbort(
            f"GS200 主磁场回读不符：设定 {target_ma:g} mA，回读 {actual_a * 1000.0:g} mA（output={actual_on}）")
    return dict(output_on=actual_on, current_ma=float(actual_a * 1000.0))


def circle_fit(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """代数最小二乘圆拟合：x^2+y^2 = a*x + b*y + c。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3 or x.size != y.size:
        raise ValueError(f"圆拟合至少需要 3 个同长数据点：x={x.size}, y={y.size}")
    design = np.column_stack([x, y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(design, x * x + y * y, rcond=None)
    cx, cy = float(sol[0] / 2.0), float(sol[1] / 2.0)
    radius_sq = float(sol[2] + cx * cx + cy * cy)
    if not math.isfinite(radius_sq) or radius_sq <= 0:
        raise ValueError(f"圆拟合失败：半径平方 {radius_sq}")
    radius = math.sqrt(radius_sq)
    residual = np.hypot(x - cx, y - cy) - radius
    return dict(center_x_v=cx, center_y_v=cy, radius_v=radius,
                rms_residual_v=float(np.sqrt(np.mean(residual ** 2))),
                max_abs_residual_v=float(np.max(np.abs(residual))))


def diagram_metrics(phases_deg: np.ndarray, phasors: np.ndarray) -> dict[str, Any]:
    """逐段差分给出 |dS/dtheta| 与 |dy/dtheta| 的分布；闭合回绕到首点。"""
    phases_deg = np.asarray(phases_deg, dtype=float)
    phasors = np.asarray(phasors, dtype=complex)
    closed_phase = np.r_[phases_deg, phases_deg[0] + 360.0]
    closed_phasor = np.r_[phasors, phasors[0]]
    dtheta = np.radians(np.diff(closed_phase))
    dphasor = np.diff(closed_phasor)
    arc = np.abs(dphasor) / dtheta
    dy = np.abs(np.diff(closed_phasor.imag)) / dtheta
    dy_signed = np.diff(closed_phasor.imag) / dtheta
    return dict(
        arc_v_per_rad=arc.tolist(),
        abs_dy_v_per_rad=dy.tolist(),
        signed_dy_v_per_rad=dy_signed.tolist(),
        arc_median_v_per_rad=float(np.median(arc)),
        arc_relative_spread=float((np.max(arc) - np.min(arc)) / max(np.median(arc), 1e-15)),
        abs_dy_median_v_per_rad=float(np.median(dy)),
        abs_dy_relative_spread=float((np.max(dy) - np.min(dy)) / max(np.median(dy), 1e-15)),
        segment_center_deg=((closed_phase[:-1] + closed_phase[1:]) / 2.0 % 360.0).tolist(),
    )


def best_slope_phase(phases_deg: np.ndarray, phasors: np.ndarray) -> tuple[float, float, int]:
    """取 |dy/dtheta| 最大的那一段，返回段中点相位、该段带符号斜率与段索引。"""
    metrics = diagram_metrics(phases_deg, phasors)
    dy = np.asarray(metrics["abs_dy_v_per_rad"], dtype=float)
    index = int(np.argmax(dy))
    return float(metrics["segment_center_deg"][index]), float(metrics["signed_dy_v_per_rad"][index]), index


def _scan_phase(ctx: StudyContext, carrier: Any, grid_deg: list[float],
                point_duration_s: float) -> tuple[list[str], np.ndarray]:
    coarse_lockin = acquisition.lockin_readback(ctx)
    labels: list[str] = []
    phasors: list[complex] = []
    for index, phase in enumerate(grid_deg):
        carrier.set_phase(phase)
        label = f"mx_std_coarse_{index:02d}"
        mean = acquisition._xy(ctx, label, point_duration_s, lockin=coarse_lockin,
                               burst_command_deg=phase, save_frame=acquisition._save_frame,
                               write_temperature=preparation.write_temperature, wait=sleep)
        labels.append(label)
        phasors.append(mean)
        print(f"  相图 {index + 1}/{len(grid_deg)}：{phase:6.1f}° -> "
              f"X={mean.real:+.6f} V, Y={mean.imag:+.6f} V", flush=True)
    ctx.state["phase_diagram"] = {"labels": labels, "grid_deg": list(grid_deg)}
    ctx.save()
    return labels, np.asarray(phasors, dtype=complex)


def _capture_noise(ctx: StudyContext, segments: int, duration_s: float, temp_lag_s: float) -> list[str]:
    lockin = acquisition.lockin_readback(ctx, dynamic=True)
    labels: list[str] = []
    for index in range(segments):
        label = f"mx_std_noise_{index:02d}_xy"
        acquisition._xy(ctx, label, duration_s, lockin=lockin, dynamic=True,
                        recovery_wait_s=temp_lag_s, segment_index=index, segment_count=segments,
                        save_frame=acquisition._save_frame,
                        write_temperature=preparation.write_temperature, wait=sleep,
                        source_mode="mx_standard_unpulsed")
        labels.append(label)
        print(f"  噪声 {index + 1}/{segments} 段完成（温控已恢复并等待 {temp_lag_s:g} s）", flush=True)
    ctx.state["noise_paths"] = dict(labels=labels, segments=segments, duration_s=duration_s,
                                    temp_switch_on_lag_s=temp_lag_s)
    ctx.save()
    return labels


def _load_xy_frames(study_root: Path, labels: list[str]) -> list[dict[str, Any]]:
    frames = []
    for label in labels:
        meta, arrays = run_format.read_frame(study_root, label, "xy")
        frames.append(dict(label=label, metadata=meta, time_s=arrays["time_s"],
                           x_v=arrays["x_v"], y_v=arrays["y_v"]))
    return frames


def _welch_psd(signals: list[np.ndarray], rate_sa_s: float,
               segment_s: float) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import welch

    nperseg = max(16, int(round(segment_s * rate_sa_s)))
    spectra = []
    freqs = None
    for values in signals:
        if values.size < nperseg:
            raise ValueError(f"单段样本 {values.size} 少于 Welch 段长 {nperseg}")
        freqs, psd = welch(values - np.mean(values), fs=rate_sa_s, nperseg=nperseg,
                           detrend="constant", scaling="density")
        spectra.append(psd)
    return freqs, np.mean(np.stack(spectra, axis=0), axis=0)


def _band_median(freqs: np.ndarray, asd: np.ndarray, band: tuple[float, float]) -> float:
    mask = (freqs >= band[0]) & (freqs <= band[1])
    return float(np.median(asd[mask])) if np.any(mask) else float("nan")


def _mains_median(freqs: np.ndarray, asd: np.ndarray) -> float:
    values = []
    for line in MAINS_HZ:
        mask = np.abs(freqs - line) <= 1.0
        if np.any(mask):
            values.append(float(np.median(asd[mask])))
    return float(np.median(values)) if values else float("nan")


def analyze_study(study_root: Path, *, welch_segment_s: float | None = None) -> dict[str, Any]:
    """离线重算：读原始 XY 帧，给出相图指标与三条相位噪声路径。"""
    config = yaml.safe_load((study_root / "study_config.yaml").read_text(encoding="utf-8")) or {}
    diagram = config.get("phase_diagram") or {}
    noise = config.get("noise_paths") or {}
    grid = [float(v) for v in diagram.get("grid_deg") or ()]
    coarse_labels = [str(v) for v in diagram.get("labels") or ()]
    noise_labels = [str(v) for v in noise.get("labels") or ()]
    if not coarse_labels or not noise_labels:
        raise ValueError("研究目录缺少相图或噪声帧清单，无法分析")
    segment_s = float(welch_segment_s or config.get("WELCH_SEGMENT_S") or 1.0)

    coarse = _load_xy_frames(study_root, coarse_labels)
    phasors = np.asarray([complex(np.mean(f["x_v"]), np.mean(f["y_v"])) for f in coarse], dtype=complex)
    fit = circle_fit(phasors.real, phasors.imag)
    metrics = diagram_metrics(np.asarray(grid, dtype=float), phasors)
    work_phase, slope_y, work_index = best_slope_phase(np.asarray(grid, dtype=float), phasors)
    radius = float(fit["radius_v"])

    frames = _load_xy_frames(study_root, noise_labels)
    rate = float(frames[0]["metadata"]["rate_sa_s"])
    for frame in frames:
        time = frame["time_s"]
        if not np.allclose(1.0 / np.median(np.diff(time)), rate, rtol=3e-3):
            raise ValueError(f"{frame['label']} 采样率与首段不一致")
    cx, cy = float(fit["center_x_v"]), float(fit["center_y_v"])
    centered = []
    for frame in frames:
        theta = np.arctan2(frame["y_v"] - cy, frame["x_v"] - cx)
        mean_angle = np.angle(np.mean(np.exp(1j * theta)))
        centered.append(np.angle(np.exp(1j * (theta - mean_angle))))
    tangent = complex(-math.sin(math.radians(work_phase)), math.cos(math.radians(work_phase)))
    projected = []
    for frame in frames:
        dx = frame["x_v"] - cx
        dy = frame["y_v"] - cy
        projected.append((dx * tangent.real + dy * tangent.imag) / radius)

    freqs, psd_y = _welch_psd([frame["y_v"] for frame in frames], rate, segment_s)
    _, psd_theta = _welch_psd(centered, rate, segment_s)
    _, psd_proj = _welch_psd(projected, rate, segment_s)
    slope_mag = abs(slope_y) if abs(slope_y) > 1e-12 else float("nan")
    asd_theta = np.sqrt(psd_theta)
    asd_y_route = np.sqrt(psd_y / (slope_mag ** 2))
    asd_proj = np.sqrt(psd_proj)
    asd_raw_y = np.sqrt(psd_y)

    ratio = asd_theta / asd_y_route
    band_mask = (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])
    ratio_band = float(np.median(ratio[band_mask])) if np.any(band_mask) else float("nan")
    consistent = math.isfinite(ratio_band) and abs(ratio_band - 1.0) <= 0.1
    verdict = ("theta 与 y 两条路径在 3-50 Hz 的相位噪声一致（中位比 %.2f）" % ratio_band if consistent
               else "theta 与 y 两条路径在 3-50 Hz 的相位噪声不一致（中位比 %.2f）" % ratio_band)

    summary = {
        "study_root": str(study_root),
        "gs200_target_ma": config.get("gs200_target_ma"),
        "rf_frequency_hz": config.get("Y_RF_FREQ_HZ"),
        "rf_vpp": config.get("Y_RF_VPP"),
        "circle_fit": fit,
        "diagram": {
            "grid_deg": grid,
            "phasors": [[float(z.real), float(z.imag)] for z in phasors],
            "arc_median_v_per_rad": metrics["arc_median_v_per_rad"],
            "arc_relative_spread": metrics["arc_relative_spread"],
            "abs_dy_median_v_per_rad": metrics["abs_dy_median_v_per_rad"],
            "abs_dy_relative_spread": metrics["abs_dy_relative_spread"],
            "abs_dy_v_per_rad": metrics["abs_dy_v_per_rad"],
            "segment_center_deg": metrics["segment_center_deg"],
        },
        "working_point_deg": work_phase,
        "working_slope_dy_dtheta_v_per_rad": slope_y,
        "working_segment_index": work_index,
        "rate_sa_s": rate,
        "welch_segment_s": segment_s,
        "segments": len(frames),
        "asd": {
            "frequency_hz": freqs.tolist(),
            "theta_rad_per_sqrt_hz": asd_theta.tolist(),
            "y_over_slope_rad_per_sqrt_hz": asd_y_route.tolist(),
            "projected_rad_per_sqrt_hz": asd_proj.tolist(),
            "y_v_per_sqrt_hz": asd_raw_y.tolist(),
            "theta_over_y_route_ratio": ratio.tolist(),
        },
        "band_3_50_hz": {
            "theta_rad_per_sqrt_hz": _band_median(freqs, asd_theta, BAND_HZ),
            "y_over_slope_rad_per_sqrt_hz": _band_median(freqs, asd_y_route, BAND_HZ),
            "projected_rad_per_sqrt_hz": _band_median(freqs, asd_proj, BAND_HZ),
            "theta_over_y_route_ratio": ratio_band,
            "mains_theta_rad_per_sqrt_hz": _mains_median(freqs, asd_theta),
        },
        "verdict": verdict,
    }
    results = study_root / "results"
    results.mkdir(exist_ok=True)
    (results / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _plot_diagram(results, summary)
    _plot_noise(results, summary)
    return summary


def _plot_diagram(results: Path, summary: dict[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = np.asarray(summary["diagram"]["phasors"], dtype=float)
    fit = summary["circle_fit"]
    grid = np.asarray(summary["diagram"]["grid_deg"], dtype=float)
    theta_line = np.linspace(0.0, 2.0 * math.pi, 361)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8))
    ax = axes[0]
    ax.scatter(points[:, 0], points[:, 1], c=grid, cmap="twilight", s=28, zorder=3, label="measured")
    ax.plot(fit["center_x_v"] + fit["radius_v"] * np.cos(theta_line),
            fit["center_y_v"] + fit["radius_v"] * np.sin(theta_line), "--", c="0.35", lw=1.0, label="circle fit")
    ax.plot([fit["center_x_v"]], [fit["center_y_v"]], "+", c="k", ms=9, label="center")
    ax.set_xlabel("X (V)")
    ax.set_ylabel("Y (V)")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(f"phase diagram, R={fit['radius_v'] * 1e3:.3f} mV, rms={fit['rms_residual_v'] * 1e3:.3f} mV")
    ax.legend(loc="best", fontsize=8)
    ax = axes[1]
    ax.plot(summary["diagram"]["segment_center_deg"], summary["diagram"]["abs_dy_v_per_rad"],
            "o-", ms=3, lw=1.0, label="|dY/dtheta|")
    ax.axvline(summary["working_point_deg"], ls="--", c="0.4", lw=0.9, label="working point")
    ax.set_xlabel("RF phase (deg)")
    ax.set_ylabel("|dY/dtheta| (V/rad)")
    ax.set_title("per-segment slope")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(results / "phase_diagram.png", dpi=160)
    fig.savefig(results / "phase_diagram.pdf")
    plt.close(fig)


def _plot_noise(results: Path, summary: dict[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freq = np.asarray(summary["asd"]["frequency_hz"], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.6))
    ax = axes[0]
    ax.loglog(freq, summary["asd"]["theta_rad_per_sqrt_hz"], lw=1.0, label="theta = atan2(y-y0, x-x0)")
    ax.loglog(freq, summary["asd"]["y_over_slope_rad_per_sqrt_hz"], lw=1.0, label="Y / |dY/dtheta|")
    ax.loglog(freq, summary["asd"]["projected_rad_per_sqrt_hz"], lw=0.8, ls=":", label="tangent projection")
    ax.axvspan(*BAND_HZ, color="0.9", zorder=0)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("phase noise (rad/sqrt(Hz))")
    ax.set_title("phase sensitivity: theta vs Y route")
    ax.legend(loc="best", fontsize=8)
    ax = axes[1]
    ax.semilogx(freq, summary["asd"]["theta_over_y_route_ratio"], lw=1.0)
    ax.axhline(1.0, ls="--", c="0.4", lw=0.9)
    ax.axvspan(*BAND_HZ, color="0.9", zorder=0)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("theta / Y-route ratio")
    ax.set_title("ratio of the two paths")
    fig.tight_layout()
    fig.savefig(results / "noise_paths.png", dpi=160)
    fig.savefig(results / "noise_paths.pdf")
    plt.close(fig)


def run_capture(args: argparse.Namespace) -> int:
    require_hardware_access("Mx 标准射频相位噪声脚本")
    root = runtime_root()
    params = _load_params(root, args)
    grid = [float(v) for v in (args.grid_deg or list(np.arange(0.0, 360.0, 15.0)))]
    if len(grid) < 6:
        raise ValueError(f"相图相位点数过少：{len(grid)}")
    current_ma = gs200_current_for_frequency_ma(params.y_rf_freq_hz, args.k_hz_per_ma, args.b_hz)
    validate_safety_limit(MAIN_FIELD_KEY, current_ma, load_safety_limits(root))

    study_dir = _create_study_directory(root, args.tag or "mx_std_phase_noise")
    ctx = StudyContext(params, root, study_dir)
    ctx.state.update(main_field_current_ma=float(current_ma),
                     gs200_target_ma=float(current_ma),
                     calibration_formula=dict(k_hz_per_ma=float(args.k_hz_per_ma), b_hz=float(args.b_hz)),
                     phase_grid_deg=list(grid),
                     Y_RF_FREQ_HZ=float(params.y_rf_freq_hz),
                     Y_RF_VPP=float(params.y_rf_vpp),
                     WELCH_SEGMENT_S=float(params.welch_segment_s))
    ctx.outputs["主磁场"] = f"GS200 {current_ma:.4f} mA（由 f={params.y_rf_freq_hz:g} Hz 与标定式反算）"
    ctx.save()
    print(f"研究目录：{study_dir.root}", flush=True)
    print(f"GS200 目标电流：{current_ma:.4f} mA（K={args.k_hz_per_ma:g} Hz/mA，b={args.b_hz:g} Hz，"
          f"f={params.y_rf_freq_hz:g} Hz）", flush=True)

    pump_dev: Any = None
    pump_initial_on: bool | None = None
    capture_failed: BaseException | None = None
    try:
        preparation.connect_and_synchronize(ctx)
        pump_dev = _connect_or_reuse_pump(ctx)
        pump_initial_on = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
        ctx.state["pump_control"] = dict(resource=PUMP_RESOURCE, device_id=PUMP_DEVICE_ID,
                                         channel=PUMP_CHANNEL, initial_output_on=pump_initial_on,
                                         restored=False)
        ctx.save()
        if not pump_initial_on:
            raise SafetyAbort(f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 当前为 OFF；请先开启泵浦光")

        carrier = preparation.prepare_measurement(ctx)
        ctx.save()
        _disable_z(ctx)
        readback = _set_main_field_ma(ctx, current_ma)
        ctx.state["readbacks"] = dict(gs200=readback, carrier=carrier.verify_unmodulated())
        ctx.save()
        if params.system_settle_s > 0:
            sleep(params.system_settle_s)

        print(f"开始整周相位扫描：{len(grid)} 点 x {params.static_point_duration_s:g} s", flush=True)
        _, phasors = _scan_phase(ctx, carrier, grid, float(params.static_point_duration_s))
        work_phase, slope_y, _ = best_slope_phase(np.asarray(grid, dtype=float), phasors)
        print(f"工作点 {work_phase:.1f}°，|dY/dtheta|={abs(slope_y):.6g} V/rad", flush=True)
        carrier.set_phase(work_phase)

        phase = acquisition.lockin_readback(ctx)["phase_deg"]
        preparation.configure_dynamic_lockin(ctx, phase_deg=float(phase))
        acquisition.lockin_readback(ctx, dynamic=True)
        ctx.state["working_point_deg"] = float(work_phase)
        ctx.save()
        print(f"开始噪声采集：{params.noise_n_avg} 段 x {params.noise_duration_s:g} s", flush=True)
        _capture_noise(ctx, int(params.noise_n_avg), float(params.noise_duration_s),
                       float(params.dynamic_temp_switch_on_lag_s))
        ctx.state["completion_status"] = "completed"
        ctx.save()
    except BaseException as exc:
        capture_failed = exc
        ctx.state["completion_status"] = "failed"
        ctx.state["failure_reason"] = str(exc)
        ctx.save()
    finally:
        cleanup_interrupt: KeyboardInterrupt | None = None
        pump_errors: list[str] = []
        if pump_dev is not None and pump_initial_on is not None:
            try:
                restored = _set_pump_output(pump_dev, pump_initial_on)
                ctx.state["pump_control"]["restored"] = True
                ctx.state["pump_control"]["restored_output_on"] = restored
            except (Exception, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cleanup_interrupt = cleanup_interrupt or exc
                pump_errors.append(f"恢复 王湾pump CH{PUMP_CHANNEL} 失败：{exc}")
                ctx.state["pump_control"]["restore_error"] = str(exc)
        park_errors: list[str] = []
        if ctx.active:
            try:
                park_errors = preparation.park(ctx)
            except (Exception, KeyboardInterrupt) as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cleanup_interrupt = cleanup_interrupt or exc
                park_errors = [f"安全收尾异常：{exc}"]
        devices = reversed(ctx.device_session.unique_devices())
        disconnect_report = run_safety_shutdown(
            disconnect_targets=(DisconnectTarget(str(i), dev) for i, dev in enumerate(devices)))
        prior_sd = ctx.state.get("safety_shutdown") or disconnect_report.to_dict()
        all_errors = [*pump_errors, *park_errors, *disconnect_report.errors]
        ctx.state["safety_shutdown"] = {**prior_sd, "completed": not all_errors, "errors": all_errors,
                                        "pump_errors": pump_errors, "action_errors": park_errors,
                                        "disconnect_errors": list(disconnect_report.disconnect_errors)}
        ctx.save()
        if capture_failed is None and cleanup_interrupt is not None:
            raise cleanup_interrupt
        if capture_failed is None and all_errors:
            raise SafetyAbort("安全收尾存在未完成项：" + "；".join(all_errors))

    if capture_failed is not None:
        raise capture_failed

    summary = analyze_study(study_dir.root, welch_segment_s=float(params.welch_segment_s))
    print(f"完成：{study_dir.root}", flush=True)
    print(f"相图：{study_dir.results / 'phase_diagram.png'}", flush=True)
    print(f"噪声路径对比：{study_dir.results / 'noise_paths.png'}", flush=True)
    print("3-50 Hz：" + json.dumps(summary["band_3_50_hz"], ensure_ascii=False), flush=True)
    print(summary["verdict"], flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="标准 Mx（不打脉冲）射频相位图与 theta/y 噪声谱路径对比")
    parser.add_argument("--analyze", type=Path, default=None, help="离线重算并重绘已有研究目录，不连接硬件")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与反算电流，不连接硬件")
    parser.add_argument("--tag", default=None, help="研究目录名后缀（默认 mx_std_phase_noise）")
    parser.add_argument("--vpp", type=float, default=None, help="覆盖 Y_RF_VPP（Vpp）")
    parser.add_argument("--freq-hz", type=float, default=None, help="覆盖射频频率（Hz），同时用于反算电流")
    parser.add_argument("--k-hz-per-ma", type=float, default=DEFAULT_K_HZ_PER_MA, help="主磁场标定斜率 K（Hz/mA）")
    parser.add_argument("--b-hz", type=float, default=DEFAULT_B_HZ, help="主磁场标定截距 b（Hz）")
    parser.add_argument("--grid-deg", type=float, nargs="*", default=None, help="整周相位网格（度，默认每 15°）")
    parser.add_argument("--point-duration", type=float, default=None, help="覆盖单点相图采样时长（s）")
    parser.add_argument("--duration", type=float, default=None, help="覆盖单段噪声采样时长（s）")
    parser.add_argument("--segments", type=int, default=None, help="覆盖噪声分段数")
    parser.add_argument("--temp-lag", type=float, default=None, help="覆盖宽带温控恢复等待（s）")
    parser.add_argument("--welch-segment", type=float, default=None, help="覆盖 Welch 段长（s）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.analyze is not None:
        summary = analyze_study(Path(args.analyze).resolve(), welch_segment_s=args.welch_segment)
        print(json.dumps(summary["band_3_50_hz"], ensure_ascii=False, indent=2))
        print(summary["verdict"])
        return 0
    if args.dry_run:
        freq = args.freq_hz if args.freq_hz is not None else 5000.0
        current_ma = gs200_current_for_frequency_ma(freq, args.k_hz_per_ma, args.b_hz)
        print(f"[dry-run] GS200 电流 = ({freq:g} - {args.b_hz:g}) / {args.k_hz_per_ma:g} = {current_ma:.4f} mA（不连接仪器）")
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
