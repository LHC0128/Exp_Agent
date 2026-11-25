"""完成 A03/A04 工作点对齐后，按四态切换测量解调 Y 噪声功率谱（V^2/Hz）并出图对照。

流程概要：
1. 读取并校验 ``rf-phase-sensitivity`` 运行参数（``--vpp`` / ``--duration`` / ``--welch-segment`` 可覆盖）。
2. 在运行目录门控（``.lab_runtime``）通过后连接设备与 ``王湾pump``（``TCPIP0::192.168.124.32::INSTR`` CH1），
   记录并要求 Pump 初始为 ON；经时钟同步与激励准备后依次执行 ``A03 phase_response``、
   ``A04 local_calibration`` 与宽带解调配置（``1e-6 s / 50000 Sa/s``）。
3. 保持 ``Demod 4`` 在对齐工作点与宽带参数下，依次切换并采集四态无调制噪声（全程受 6 步 ``Temp_Switch``
   门控保护，窗口外才写盘；状态间安全顺序切换并回读确认）：
   - ``all_on``: 全部开启（Pump ON + RF ON + Z 控制 ON）
   - ``rf_only``: 只开射频场、关闭 Z 控制（Pump ON + RF ON + 1200B OFF & Z 信号源 OFF）
   - ``ctrl_only``: 只开 Z 控制、关闭射频场（Pump ON + MFLI Out1 OFF 保留载波相移 + Z 控制 ON）
   - ``pump_off``: 关闭泵浦光（``192.168.124.32`` CH1 OFF + RF ON + Z 控制 ON）
4. ``finally`` 中优先恢复 ``王湾pump`` CH1 到原始状态并回读确认，随后执行 ``preparation.park(ctx)``
   安全关断与设备断开。
5. 直接计算四态解调 Y 的 Welch 单边功率谱密度（``V^2/Hz``，不做 √Lorentzian 频响回补），
   输出至 ``data/rf_phase_sensitivity/_studies/<MMDD_HHMMSS>_noise_4states/results/``，
   同时支持 ``--analyze <study_dir>`` 纯离线重算与重绘。
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.signal import welch

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

from hardware_gate import require_hardware_access, runtime_root
from signal_generator import DG900Instrument

from lab_workflows.common import data_root
from lab_workflows.devices import create_signal_generator
from lab_workflows.experiment_modules.rf_phase_sensitivity import (
    acquisition,
    analysis_steps,
    preparation,
    run_format,
)
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.experiment_modules.z_chain_common import prep as z_prep
from lab_workflows.instrument_config import load_device_definitions
from lab_workflows.plotting import (
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    new_figure,
    plot_style_context,
    style_legend,
)
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort, sleep
from lab_workflows.steps.run_directory import write_run_config


PUMP_RESOURCE = "TCPIP0::192.168.124.32::INSTR"
PUMP_DEVICE_ID = "dg9q275000733"
PUMP_CHANNEL = 1
MIN_FREQ_HZ = analysis_steps.MIN_NOISE_HZ
MAINS_HARMONICS_HZ = analysis_steps.MAINS_HARMONICS_HZ


@dataclass(frozen=True, slots=True)
class StateSpec:
    state_id: str
    label_zh: str
    label_en: str
    xy_label: str
    pump_on: bool
    rf_on: bool
    z_on: bool
    color: str
    linestyle: str
    linewidth: float
    alpha: float
    zorder: int


FOUR_STATES: tuple[StateSpec, ...] = (
    StateSpec(
        state_id="all_on",
        label_zh="全开（Pump + RF + Z 控制）",
        label_en="All ON (Pump + RF + Z ctrl)",
        xy_label="noise_all_on_xy",
        pump_on=True,
        rf_on=True,
        z_on=True,
        color=COLOR_OPTIMAL,
        linestyle="-",
        linewidth=1.35,
        alpha=0.92,
        zorder=4,
    ),
    StateSpec(
        state_id="rf_only",
        label_zh="仅开射频场（Z 控制关闭）",
        label_en="RF ON, Z ctrl OFF",
        xy_label="noise_rf_only_xy",
        pump_on=True,
        rf_on=True,
        z_on=False,
        color=COLOR_ORANGE,
        linestyle="-",
        linewidth=1.20,
        alpha=0.88,
        zorder=3,
    ),
    StateSpec(
        state_id="ctrl_only",
        label_zh="仅开控制（RF 关闭）",
        label_en="Z ctrl ON, RF OFF",
        xy_label="noise_ctrl_only_xy",
        pump_on=True,
        rf_on=False,
        z_on=True,
        color=COLOR_GREEN,
        linestyle="-",
        linewidth=1.20,
        alpha=0.88,
        zorder=3,
    ),
    StateSpec(
        state_id="pump_off",
        label_zh="关闭泵浦光（192.168.124.32 CH1 OFF）",
        label_en="Pump OFF (RF + Z ctrl ON)",
        xy_label="noise_pump_off_xy",
        pump_on=False,
        rf_on=True,
        z_on=True,
        color=COLOR_GRAY,
        linestyle="--",
        linewidth=1.15,
        alpha=0.90,
        zorder=2,
    ),
)


@dataclass(slots=True)
class StudyDirectory:
    root: Path
    raw: Path
    results: Path
    config_path: Path

    def update_config(self, **updates: Any) -> dict[str, Any]:
        payload = dict(updates)
        write_run_config(self.config_path, payload)
        return payload


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
        self.state["study_type"] = "rf_phase_noise_breakdown_4states"
        self.state["study_root"] = str(directory.root)

    def save(self) -> None:
        self.directory.update_config(**self.state)


def _create_study_directory(root: Path, tag: str) -> StudyDirectory:
    if any(ch in tag for ch in '/\\:*?"<>|\x00') or not tag.strip():
        raise ValueError(f"非法研究目录标签：{tag!r}")
    studies_root = data_root(root) / run_format.DATA_TYPE / "_studies"
    studies_root.mkdir(parents=True, exist_ok=True)
    parent_readme = studies_root / "README.md"
    if not parent_readme.exists():
        parent_readme.write_text(
            "# rf_phase_sensitivity 探索研究目录\n\n"
            "本目录存放射频相位灵敏度探索脚本生成的原始 `.npz` 帧、配置快照与结果图表（不进 git）。\n",
            encoding="utf-8",
        )
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
    raw = study_root / "raw"
    results = study_root / "results"
    raw.mkdir()
    results.mkdir(exist_ok=True)
    return StudyDirectory(root=study_root, raw=raw, results=results, config_path=study_root / "study_config.yaml")


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
    if args.duration is not None:
        merged["NOISE_DURATION_S"] = float(args.duration)
    if args.welch_segment is not None:
        merged["WELCH_SEGMENT_S"] = float(args.welch_segment)
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


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
                    raise SafetyAbort(
                        f"{PUMP_RESOURCE} CH{PUMP_CHANNEL} 已在本实验信号路径中占用，拒绝重复控制"
                    )
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
    pump_dev.set_output(bool(enabled), channel=PUMP_CHANNEL)
    pump_dev.wait_for_operation_complete()
    pump_dev.raise_for_errors()
    actual = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
    if actual is not bool(enabled):
        raise SafetyAbort(
            f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 回读为 {'ON' if actual else 'OFF'}，"
            f"与目标 {'ON' if enabled else 'OFF'} 不符"
        )
    return actual


def _apply_noise_state(
    ctx: StudyContext,
    pump_dev: Any,
    carrier: acquisition.Carrier,
    spec: StateSpec,
    *,
    settle_s: float,
) -> dict[str, Any]:
    # 1) Z 控制链：关时先关 1200B 再关 DG；开时先开 DG 并回读 ON 再开 1200B。
    z_dev, z_ch = preparation._route(ctx, "z")
    z_dg_on = preparation.dg_output(z_dev, z_ch)
    amp_on = bool(ctx.state.get("amp_output_on"))
    if spec.z_on:
        if not z_dg_on:
            z_dev.set_output(True, channel=z_ch)
            z_dev.wait_for_operation_complete()
            if not preparation.dg_output(z_dev, z_ch):
                raise SafetyAbort("Z 信号源输出未回读为 ON，拒绝开启 1200B")
        if not amp_on:
            z_prep.set_amp_output(ctx, True)
    else:
        if amp_on:
            z_prep.set_amp_output(ctx, False)
        if z_dg_on:
            z_dev.set_output(False, channel=z_ch)
            z_dev.wait_for_operation_complete()
            if preparation.dg_output(z_dev, z_ch):
                raise SafetyAbort("Z 信号源输出未回读为 OFF")

    # 2) MFLI Y 射频：仅切 sigouts/0/on，完整保留 carrier/phaseshift 与 Demod 4 相位。
    mfli = ctx.devices["hf2"]
    dev = mfli.device_id
    rf_now = bool(int(mfli.get_int(f"/{dev}/sigouts/0/on")))
    if rf_now != spec.rf_on:
        mfli.set_int(f"/{dev}/sigouts/0/on", 1 if spec.rf_on else 0)
        mfli.sync()
    rf_readback = carrier._verify(output=spec.rf_on, sidebands=False)

    # 3) 王湾pump (192.168.124.32 CH1)
    pump_now = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
    if pump_now != spec.pump_on:
        _set_pump_output(pump_dev, spec.pump_on)
    elif pump_now is not spec.pump_on:
        raise SafetyAbort(f"王湾pump CH{PUMP_CHANNEL} 状态回读不符")

    if settle_s > 0:
        sleep(settle_s)

    # 再核对三者都处于目标稳态后再进 Temp_Switch 采集窗口
    z_verified = bool(preparation.dg_output(z_dev, z_ch)) and bool(ctx.state.get("amp_output_on"))
    pump_verified = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
    if z_verified is not spec.z_on:
        raise SafetyAbort(f"{spec.state_id} 采集前 Z 控制状态回读不符：expected={spec.z_on}, actual={z_verified}")
    if pump_verified is not spec.pump_on:
        raise SafetyAbort(f"{spec.state_id} 采集前 Pump 状态回读不符：expected={spec.pump_on}, actual={pump_verified}")
    carrier._verify(output=spec.rf_on, sidebands=False)
    return dict(
        z_dg_on=preparation.dg_output(z_dev, z_ch),
        amp_output_on=bool(ctx.state.get("amp_output_on")),
        rf_output_on=bool(rf_readback["output"]),
        carrier_command_deg=float(rf_readback["command_deg"]),
        pump_output_on=pump_verified,
    )


def _welch_psd_v2_per_hz(time_s: np.ndarray, y_v: np.ndarray, rate_sa_s: float, segment_s: float):
    t, rate = analysis_steps.sample_grid(time_s, rate_sa_s)
    y = np.asarray(y_v, dtype=float)
    if y.shape != t.shape or not np.isfinite(y).all():
        raise ValueError("XY 帧 y_v 波形长度或数值无效")
    requested = max(8, math.ceil(rate * segment_s))
    nperseg = min(requested, y.size)
    noverlap = nperseg // 2
    nsegments = 1 + (y.size - nperseg) // (nperseg - noverlap)
    freq, psd = welch(
        y,
        fs=rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        detrend="linear",
        return_onesided=True,
        scaling="density",
    )
    keep = freq >= MIN_FREQ_HZ
    if not keep.any() or not np.isfinite(psd).all() or (psd < 0).any():
        raise ValueError("未生成有效的 >=1 Hz Welch PSD")
    details = dict(
        rate_sa_s=float(rate),
        samples=int(y.size),
        duration_s=float(t[-1] + 1.0 / rate),
        nperseg=int(nperseg),
        noverlap=int(noverlap),
        segments=int(nsegments),
        segment_s=float(nperseg / rate),
        requested_segment_s=float(segment_s),
        resolution_hz=float(rate / nperseg),
        resolution_met=bool(nperseg == requested),
        window="hann",
        detrend="linear",
        one_sided=True,
        unit="V^2/Hz",
    )
    return freq[keep], psd[keep], details


def _band_metrics(freq: np.ndarray, psd: np.ndarray, f_lo: float, f_hi: float) -> dict[str, Any]:
    mask = (freq >= f_lo) & (freq <= f_hi)
    if not mask.any():
        return dict(bins=0, median_v2_per_hz=None, mean_v2_per_hz=None, rms_v=None)
    f_sub = freq[mask]
    p_sub = psd[mask]
    trapz = getattr(np, "trapezoid", None) or np.trapz
    integrated = float(trapz(p_sub, f_sub)) if f_sub.size >= 2 else float(p_sub[0] * (f_sub[0] if f_sub.size == 1 else 0.0))
    return dict(
        bins=int(f_sub.size),
        median_v2_per_hz=float(np.median(p_sub)),
        mean_v2_per_hz=float(np.mean(p_sub)),
        rms_v=float(math.sqrt(max(0.0, integrated))),
    )


def analyze_study(study_dir: Path, *, welch_segment_s: float | None = None) -> dict[str, Any]:
    study_dir = Path(study_dir).resolve()
    cfg_path = study_dir / "study_config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"找不到研究配置：{cfg_path}")
    config = run_format.read_config(cfg_path)
    params_dict = config.get("parameters") or {}
    segment_s = float(welch_segment_s if welch_segment_s is not None else params_dict.get("WELCH_SEGMENT_S", 4.0))

    spectra: dict[str, dict[str, Any]] = {}
    ref_freq: np.ndarray | None = None
    four_state_cfg = config.get("four_state_noise") or {}
    state_records = four_state_cfg.get("states") or {}

    for spec in FOUR_STATES:
        meta, arr = run_format.read_frame(study_dir, spec.xy_label, "xy")
        freq, psd, details = _welch_psd_v2_per_hz(arr["time_s"], arr["y_v"], meta["rate_sa_s"], segment_s)
        if ref_freq is None:
            ref_freq = freq
        elif freq.shape != ref_freq.shape or not np.allclose(freq, ref_freq, rtol=1e-9, atol=1e-9):
            raise ValueError(f"{spec.state_id} 频点网格与 {FOUR_STATES[0].state_id} 不一致")
        state_rec = state_records.get(spec.state_id) or {}
        mains = {}
        for hz in MAINS_HARMONICS_HZ:
            if hz <= freq[-1]:
                idx = int(np.argmin(np.abs(freq - hz)))
                mains[f"{int(hz)}Hz"] = dict(
                    bin_hz=float(freq[idx]),
                    psd_v2_per_hz=float(psd[idx]),
                )
        spectra[spec.state_id] = dict(
            spec=spec,
            freq=freq,
            psd=psd,
            welch=details,
            mean_x_v=float(state_rec.get("mean_x_v", np.mean(arr["x_v"]))),
            mean_y_v=float(state_rec.get("mean_y_v", np.mean(arr["y_v"]))),
            band_1_250= _band_metrics(freq, psd, 1.0, 250.0),
            band_3_203=_band_metrics(freq, psd, 3.0, 203.0),
            band_250_5k=_band_metrics(freq, psd, 250.0, 5000.0),
            band_full=_band_metrics(freq, psd, float(freq[0]), float(freq[-1])),
            mains_peaks=mains,
        )

    assert ref_freq is not None
    results_dir = study_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "noise_4states_psd.csv"
    header = ["frequency_hz", *[f"psd_{spec.state_id}_v2_per_hz" for spec in FOUR_STATES]]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for i, f_val in enumerate(ref_freq):
            writer.writerow([f"{float(f_val):.10g}", *[f"{float(spectra[s.state_id]['psd'][i]):.10e}" for s in FOUR_STATES]])

    base_med_1_250 = spectra["all_on"]["band_1_250"]["median_v2_per_hz"]
    base_med_3_203 = spectra["all_on"]["band_3_203"]["median_v2_per_hz"]

    cal = config.get("calibration") or {}
    dyn = config.get("dynamic_lockin") or {}
    phase_resp = config.get("phase_response") or {}
    summary: dict[str, Any] = dict(
        study_dir=str(study_dir),
        psd_unit="V^2/Hz",
        lorentzian_backfill=False,
        welch_segment_s=segment_s,
        calibration=dict(
            coarse_work_point_deg=phase_resp.get("burst_command_deg"),
            lockin_phase_deg=cal.get("lockin_phase_deg"),
            refined_carrier_center_deg=cal.get("carrier_center_deg"),
            k0_v_per_rad=cal.get("k0_v_per_rad"),
            slope_x_v_per_rad=cal.get("slope_x_v_per_rad"),
        ),
        dynamic_lockin=dict(
            rate_sa_s=dyn.get("actual_rate_Sa_s"),
            time_constant_s=dyn.get("time_constant_s"),
            order=dyn.get("order"),
            signal_range_v=dyn.get("signal_range_v"),
        ),
        states={},
    )
    for spec in FOUR_STATES:
        item = spectra[spec.state_id]
        med1 = item["band_1_250"]["median_v2_per_hz"]
        med3 = item["band_3_203"]["median_v2_per_hz"]
        summary["states"][spec.state_id] = dict(
            label_zh=spec.label_zh,
            label_en=spec.label_en,
            xy_label=spec.xy_label,
            pump_on=spec.pump_on,
            rf_on=spec.rf_on,
            z_control_on=spec.z_on,
            mean_x_v=item["mean_x_v"],
            mean_y_v=item["mean_y_v"],
            welch=item["welch"],
            band_1_250hz=item["band_1_250"],
            band_3_203hz=item["band_3_203"],
            band_250_5000hz=item["band_250_5k"],
            band_full=item["band_full"],
            mains_peaks=item["mains_peaks"],
            ratio_vs_all_on_median_1_250hz=(med1 / base_med_1_250) if (med1 is not None and base_med_1_250) else None,
            ratio_vs_all_on_median_3_203hz=(med3 / base_med_3_203) if (med3 is not None and base_med_3_203) else None,
        )

    summary_path = results_dir / "noise_4states_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 绘制全英文对照图（V^2/Hz，上：1-300 Hz 基带局部；下：1 Hz - Nyquist 全频段）
    png_path = results_dir / "noise_4states_psd.png"
    with plot_style_context("paper"):
        fig, (ax_bb, ax_wide) = new_figure(figsize=(7.6, 5.8), nrows=2, ncols=1, constrained_layout=True)
        for ax, f_max in ((ax_bb, 300.0), (ax_wide, float(ref_freq[-1]))):
            mask = ref_freq <= f_max
            f_plot = ref_freq[mask]
            for spec in FOUR_STATES:
                item = spectra[spec.state_id]
                p_plot = item["psd"][mask]
                pos = p_plot > 0
                if not pos.any():
                    continue
                med3 = item["band_3_203"]["median_v2_per_hz"]
                lbl = f"{spec.label_en} (3-203 Hz med: {med3:.2e} V^2/Hz)" if med3 is not None else spec.label_en
                ax.loglog(
                    f_plot[pos],
                    p_plot[pos],
                    color=spec.color,
                    ls=spec.linestyle,
                    lw=spec.linewidth,
                    alpha=spec.alpha,
                    zorder=spec.zorder,
                    label=lbl if ax is ax_bb else None,
                )
            ax.grid(True, which="both", alpha=0.22)
            ax.set_ylabel(r"Demod 4 Y PSD ($\mathrm{V}^2/\mathrm{Hz}$)")

        ax_bb.set(
            xlim=(1.0, 300.0),
            xlabel="Frequency (Hz)",
            title="Baseband noise breakdown (1-300 Hz, uncompensated detector PSD)",
        )
        for mains in (50.0, 100.0, 150.0, 200.0, 250.0):
            ax_bb.axvline(mains, color="#BBBBBB", ls=":", lw=0.7, zorder=1)
        style_legend(ax_bb, loc="upper right", fontsize=7.0, framealpha=0.92)

        ax_wide.set(
            xlim=(1.0, float(ref_freq[-1])),
            xlabel="Frequency (Hz)",
            title="Full Demod 4 bandwidth PSD (1 Hz to Nyquist)",
        )
        k0_val = summary["calibration"]["k0_v_per_rad"]
        wp_deg = summary["calibration"]["refined_carrier_center_deg"]
        df_hz = spectra["all_on"]["welch"]["resolution_hz"]
        rate_hz = summary["dynamic_lockin"]["rate_sa_s"]
        tc_s = summary["dynamic_lockin"]["time_constant_s"]
        k0_txt = f"K0={k0_val:.4g} V/rad" if isinstance(k0_val, (int, float)) else "K0=N/A"
        wp_txt = f"phi={wp_deg:.2f} deg" if isinstance(wp_deg, (int, float)) else "phi=N/A"
        rate_txt = f"{rate_hz:g}" if isinstance(rate_hz, (int, float)) else "N/A"
        tc_txt = f"{tc_s:g}" if isinstance(tc_s, (int, float)) else "N/A"
        fig.suptitle(
            f"{study_dir.name}: 4-State Demod 4 Y Noise PSD (V^2/Hz)\n"
            f"{k0_txt}, {wp_txt} | Demod4 tc={tc_txt} s, fs={rate_txt} Sa/s, df={df_hz:.2g} Hz",
            fontsize=8.8,
        )
        fig.savefig(png_path, dpi=240)

    _write_study_readme(study_dir, summary, csv_path=csv_path, summary_path=summary_path, png_path=png_path)
    return summary


def _write_study_readme(
    study_dir: Path,
    summary: dict[str, Any],
    *,
    csv_path: Path,
    summary_path: Path,
    png_path: Path,
) -> None:
    lines = [
        f"# {study_dir.name}: 四态解调 Y 噪声谱对照（V^2/Hz）",
        "",
        "- 脚本：`scripts/rf_phase_sensitivity/noise_breakdown_4states.py`",
        f"- 工作点：`refined_carrier_center_deg = {summary['calibration']['refined_carrier_center_deg']}`°，"
        f"`K0 = {summary['calibration']['k0_v_per_rad']}` V/rad，"
        f"`slope_X = {summary['calibration']['slope_x_v_per_rad']}` V/rad",
        f"- 宽带 Demod 4：`tc = {summary['dynamic_lockin']['time_constant_s']}` s，"
        f"`fs = {summary['dynamic_lockin']['rate_sa_s']}` Sa/s，"
        f"`order = {summary['dynamic_lockin']['order']}`",
        "- 产物：",
        f"  - 图表：`{png_path.relative_to(study_dir).as_posix()}`",
        f"  - 数据：`{csv_path.relative_to(study_dir).as_posix()}`",
        f"  - 摘要：`{summary_path.relative_to(study_dir).as_posix()}`",
        "",
        "| 状态 | Pump | RF | Z 控制 | 1–250 Hz 中位 PSD (V^2/Hz) | 3–203 Hz 中位 PSD (V^2/Hz) | vs all_on (3–203 Hz) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for state_id, item in summary["states"].items():
        med1 = item["band_1_250hz"]["median_v2_per_hz"]
        med3 = item["band_3_203hz"]["median_v2_per_hz"]
        ratio = item["ratio_vs_all_on_median_3_203hz"]
        med1_s = f"{med1:.3e}" if med1 is not None else "N/A"
        med3_s = f"{med3:.3e}" if med3 is not None else "N/A"
        ratio_s = f"{ratio:.3f}" if ratio is not None else "N/A"
        lines.append(
            f"| `{state_id}` | {'ON' if item['pump_on'] else 'OFF'} | {'ON' if item['rf_on'] else 'OFF'} | "
            f"{'ON' if item['z_control_on'] else 'OFF'} | {med1_s} | {med3_s} | {ratio_s} |"
        )
    (study_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_capture(args: argparse.Namespace) -> int:
    require_hardware_access("四态射频相位噪声分解脚本")
    root = runtime_root()
    params = _load_params(root, args)
    study_dir = _create_study_directory(root, args.tag)
    ctx = StudyContext(params, root, study_dir)
    ctx.save()

    pump_dev: Any = None
    pump_initial_on: bool | None = None
    capture_failed: BaseException | None = None
    try:
        preparation.connect_and_synchronize(ctx)
        pump_dev = _connect_or_reuse_pump(ctx)
        pump_initial_on = bool(pump_dev.get_output(channel=PUMP_CHANNEL))
        ctx.state["pump_control"] = dict(
            resource=PUMP_RESOURCE,
            device_id=PUMP_DEVICE_ID,
            channel=PUMP_CHANNEL,
            initial_output_on=pump_initial_on,
            restored=False,
        )
        ctx.save()
        if not pump_initial_on:
            raise SafetyAbort(
                f"王湾pump ({PUMP_RESOURCE}) CH{PUMP_CHANNEL} 当前为 OFF；"
                "请先开启泵浦光再启动四态噪声分解测量"
            )

        carrier = preparation.prepare_measurement(ctx)
        ctx.save()

        # A03 整周粗扫 + A04 局部 Eq.(2.2.2) 细化并更新工作点与解调相位
        acquisition.phase_response(ctx, carrier)
        cal = acquisition.local_calibration(ctx, carrier)

        # 切换到 A05/A06 宽带 Demod 4 设置并回读核验
        preparation.configure_dynamic_lockin(ctx, phase_deg=float(cal["lockin_phase_deg"]))
        dyn_lockin = acquisition.lockin_readback(ctx, dynamic=True)
        ctx.save()
        if ctx.params.system_settle_s > 0:
            sleep(ctx.params.system_settle_s)

        ctx.state["four_state_noise"] = dict(
            order=[s.state_id for s in FOUR_STATES],
            duration_s=float(ctx.params.noise_duration_s),
            settle_after_switch_s=float(args.settle_after_switch),
            states={},
        )
        for idx, spec in enumerate(FOUR_STATES, 1):
            print(f"[{idx}/{len(FOUR_STATES)}] 切换状态 {spec.state_id}: {spec.label_zh}", flush=True)
            readbacks = _apply_noise_state(
                ctx,
                pump_dev,
                carrier,
                spec,
                settle_s=float(args.settle_after_switch),
            )
            mean_z = acquisition._xy(
                ctx,
                spec.xy_label,
                float(ctx.params.noise_duration_s),
                lockin=dyn_lockin,
                dynamic=True,
                source_mode=f"noise_{spec.state_id}",
                state_id=spec.state_id,
                pump_on=spec.pump_on,
                rf_on=spec.rf_on,
                z_control_on=spec.z_on,
                carrier_command_deg=readbacks["carrier_command_deg"],
            )
            ctx.state["four_state_noise"]["states"][spec.state_id] = dict(
                label_zh=spec.label_zh,
                label_en=spec.label_en,
                xy_label=spec.xy_label,
                pump_on=spec.pump_on,
                rf_on=spec.rf_on,
                z_control_on=spec.z_on,
                mean_x_v=float(mean_z.real),
                mean_y_v=float(mean_z.imag),
                verified_readbacks=readbacks,
            )
            ctx.save()
        ctx.state["completion_status"] = "completed"
        ctx.save()
    except BaseException as exc:
        capture_failed = exc
        ctx.state["completion_status"] = "failed"
        ctx.state["failure_reason"] = str(exc)
    finally:
        pump_errors: list[str] = []
        if pump_dev is not None and pump_initial_on is not None:
            try:
                restored = _set_pump_output(pump_dev, pump_initial_on)
                ctx.state["pump_control"]["restored"] = True
                ctx.state["pump_control"]["restored_output_on"] = restored
            except Exception as exc:
                pump_errors.append(f"恢复 王湾pump CH{PUMP_CHANNEL} 失败：{exc}")
                ctx.state["pump_control"]["restore_error"] = str(exc)

        park_errors: list[str] = []
        if ctx.active:
            try:
                park_errors = preparation.park(ctx)
            except Exception as exc:
                park_errors = [f"安全收尾异常：{exc}"]

        devices = reversed(ctx.device_session.unique_devices())
        disconnect_report = run_safety_shutdown(
            disconnect_targets=(DisconnectTarget(str(i), dev) for i, dev in enumerate(devices))
        )
        prior_sd = ctx.state.get("safety_shutdown") or disconnect_report.to_dict()
        all_errors = [*pump_errors, *park_errors, *disconnect_report.errors]
        ctx.state["safety_shutdown"] = {
            **prior_sd,
            "completed": not all_errors,
            "errors": all_errors,
            "pump_errors": pump_errors,
            "action_errors": park_errors,
            "disconnect_errors": list(disconnect_report.disconnect_errors),
        }
        ctx.save()
        if capture_failed is None and all_errors:
            raise SafetyAbort("安全收尾存在未完成项：" + "；".join(all_errors))

    summary = analyze_study(study_dir.root, welch_segment_s=ctx.params.welch_segment_s)
    print(f"完成采集与 PSD 分析：{study_dir.root}")
    print(f"对照图：{study_dir.results / 'noise_4states_psd.png'}")
    print(f"汇总：{json.dumps(summary['states'], ensure_ascii=False, indent=2)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="完成 A03/A04 后测量四态（全开/仅 RF/仅 Z 控制/关 Pump）Demod 4 Y 噪声谱（V^2/Hz）"
    )
    parser.add_argument("--analyze", type=Path, default=None, help="离线重算并重绘已有 _studies 目录，不连接硬件")
    parser.add_argument("--tag", default="noise_4states", help="研究目录名后缀（默认 noise_4states）")
    parser.add_argument("--vpp", type=float, default=None, help="覆盖 Y_RF_VPP（Vpp）")
    parser.add_argument("--duration", type=float, default=None, help="覆盖每态噪声采样时长（s，默认沿用参数 24 s）")
    parser.add_argument("--welch-segment", type=float, default=None, help="覆盖 Welch 分段时长（s，默认 4 s）")
    parser.add_argument("--settle-after-switch", type=float, default=0.5, help="每次切态后进温控窗前的稳定等待（s）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.analyze is not None:
        summary = analyze_study(args.analyze, welch_segment_s=args.welch_segment)
        print(f"离线 PSD 分析完成：{Path(args.analyze).resolve() / 'results'}")
        for state_id, item in summary["states"].items():
            med3 = item["band_3_203hz"]["median_v2_per_hz"]
            med1 = item["band_1_250hz"]["median_v2_per_hz"]
            print(f"  {state_id:10s} | med(1-250 Hz)={med1:.3e} V^2/Hz | med(3-203 Hz)={med3:.3e} V^2/Hz")
        return 0
    return run_capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
