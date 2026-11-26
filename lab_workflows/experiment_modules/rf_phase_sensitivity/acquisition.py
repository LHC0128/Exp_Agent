"""同一轮四段采集：MFLI MF-MOD 整周调相、纯锁相局部 K₀ 标定、相干边带 PM 频响与无调制噪声。

整周扫描、局部标定与 XY 帧采集已抽到 ``z_chain_common.rf_phase``；本模块保留灵敏度
实验的参数、帧标签与状态键绑定，并继续保存 A05/A06 的基带扫频与噪声分段流程。
"""
from __future__ import annotations

import math
import time

from lockin_amplifier import daq  # noqa: F401  历史测试与脚本按 acquisition.daq 替换 DAQ 读取

from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps.coil_drive_chain import SafetyAbort, sleep
from ..z_chain_common import rf_phase, signal_fit
from . import preparation, run_format

# 兼容入口：测试、准备步骤与现场脚本继续按旧路径引用这些名字。
Carrier = rf_phase.Carrier
lockin_readback = rf_phase.lockin_readback
local_offsets = rf_phase.local_offsets
_xy = rf_phase._xy
_STATIC_GRID_DEG = rf_phase.DEFAULT_STATIC_GRID_DEG
_LOCAL_STEPS_PER_SIDE = rf_phase.LOCAL_STEPS_PER_SIDE


def _save_frame(root, label, kind, metadata, **arrays):
    """把灵敏度实验的 run_format 写盘适配成共享模块的 save_frame 回调。"""
    return run_format.write_frame(root, label, kind, metadata, **arrays)


def acquire_noise_segments(
    ctx,
    *,
    label_prefix: str,
    count: int,
    duration_s: float,
    lockin: dict,
    recovery_wait_s: float | None = None,
    single_label: str | None = None,
    on_segment=None,
    **record,
) -> tuple[list[str], list[complex]]:
    """按独立温控窗口分段采集噪声 XY 帧；每段恢复温控后写入磁盘并立即回调。"""
    if count < 1:
        raise SafetyAbort(f"噪声分段次数必须为正整数：{count}")
    labels: list[str] = []
    means: list[complex] = []
    for seg_idx in range(count):
        check_cancelled()
        label = single_label if (single_label is not None and count == 1) else f"{label_prefix}_seg_{seg_idx:02d}_xy"
        mean_z = _xy(
            ctx,
            label,
            duration_s,
            lockin=lockin,
            dynamic=True,
            recovery_wait_s=recovery_wait_s,
            segment_index=seg_idx,
            segment_count=count,
            save_frame=_save_frame,
            write_temperature=preparation.write_temperature,
            wait=sleep,
            **record,
        )
        labels.append(label)
        means.append(mean_z)
        if on_segment is not None:
            on_segment(seg_idx + 1, count, label, mean_z, list(labels), list(means))
    return labels, means


def phase_response(ctx, carrier):
    """整周调相扫描找最大切线，并把 MFLI Demod 4 解调相位转到该切线。"""
    return rf_phase.phase_response(
        ctx, carrier, save_frame=_save_frame, write_temperature=preparation.write_temperature,
        wait=sleep, grid_deg=_STATIC_GRID_DEG, stage_tag="A03",
        state_key="phase_response", output_key="相位灵敏度",
    )


def local_calibration(ctx, carrier):
    """在整周工作点两侧以 MFLI 指令角加密细扫，线性回归标定本轮 K₀。"""
    return rf_phase.local_calibration(
        ctx, carrier, save_frame=_save_frame, write_temperature=preparation.write_temperature,
        wait=sleep, stage_tag="A04", state_key="calibration",
        phase_state_key="phase_response", output_key="相位灵敏度",
    )


def _sweep(ctx, carrier, cal, lockin):
    frequencies = ctx.params.sweep_order_hz
    start = time.monotonic()
    for i, f_hz in enumerate(frequencies):
        settings = carrier.frequency(f_hz)
        pm_rad = float(settings["mod_index_rad"])
        if not math.isfinite(pm_rad) or pm_rad <= 0:
            raise SafetyAbort(f"MFLI MF-MOD 调相深度回读无效：{pm_rad}")
        xy_label = f"pm_{i:02d}_xy"
        row = dict(frequency_hz=f_hz, repeat=False, scope_label=None, xy_label=None,
                   phase_pk_rad=pm_rad, modulation_hz=f_hz, electrical_accepted=True,
                   accepted=False, reason="尚未完成")
        ctx.state["transfer"].append(row)
        _xy(ctx, xy_label, ctx.params.pm_tone_duration_s, lockin=lockin, dynamic=True,
            frequency_hz=f_hz, phase_pk_rad=pm_rad, save_frame=_save_frame,
            write_temperature=preparation.write_temperature, wait=sleep)
        row.update(xy_label=xy_label, accepted=True, reason="")
        ctx.save()
        done = i + 1
        report_runtime_progress("A05", f"基带频响 {done}/{len(frequencies)}，{f_hz:g} Hz：已采 XY",
                                15 + 75 * done / len(frequencies),
                                estimated_remaining_seconds=(time.monotonic() - start) / done * (len(frequencies) - done),
                                data={"eta_scope": "当前基带扫频"})


def _baseline(ctx, carrier, lockin):
    report_runtime_progress("A06", "关闭调制边带保留 5 kHz 连续载波", 10, estimated_remaining_seconds=None)
    carrier.stop_pm()
    count = int(ctx.params.noise_n_avg)
    state = dict(
        accepted=False,
        electrical_accepted=True,
        quality_passed=False,
        quality_warnings=[],
        reason="未完成基线采集",
        scope_labels=[],
        xy_label=None,
        xy_labels=[],
        segments_requested=count,
        segments_completed=0,
    )
    ctx.state["noise"] = state
    ctx.save()
    start = time.monotonic()

    def _on_segment(done, total, label, _mean_z, labels, means):
        mean_all = sum(means) / len(means)
        state.update(
            xy_labels=labels,
            xy_label=labels[0] if total == 1 else None,
            segments_completed=done,
            mean_x_v=float(mean_all.real),
            mean_y_v=float(mean_all.imag),
        )
        ctx.save()
        report_runtime_progress(
            "A06",
            f"无调制噪声分段 {done}/{total}",
            15 + 80 * done / total,
            estimated_remaining_seconds=(time.monotonic() - start) / done * (total - done),
            data={"eta_scope": "当前无调制噪声"},
        )

    report_runtime_progress("A06", f"开始分段采集无调制基带记录（共 {count} 段 × {ctx.params.noise_duration_s:g} s）",
                            15, estimated_remaining_seconds=None)
    acquire_noise_segments(
        ctx,
        label_prefix="noise",
        count=count,
        duration_s=ctx.params.noise_duration_s,
        lockin=lockin,
        single_label="noise_xy",
        on_segment=_on_segment,
        source_mode="unmodulated_carrier",
    )
    after = carrier.verify_unmodulated()
    state.update(
        carrier_command_deg=after["command_deg"],
        accepted=True,
        quality_passed=True,
        quality_warnings=[],
        reason="",
    )


def _calibration_state(ctx, *, dynamic: bool = False):
    cal = ctx.state.get("calibration") or {}
    if cal.get("accepted") is not True:
        raise SafetyAbort("缺少本轮合格的局部 K₀ 标定；请先运行 A04")
    lockin = lockin_readback(ctx, dynamic=dynamic)
    if abs(signal_fit.wrap_deg(lockin["phase_deg"] - cal["lockin_phase_deg"])) > 0.5:
        raise SafetyAbort("MFLI 解调相位与本轮标定不一致；请整链重跑")
    return cal, lockin


def frequency_response(ctx):
    try:
        cal, _ = _calibration_state(ctx, dynamic=False)
        preparation.configure_dynamic_lockin(ctx, phase_deg=float(cal["lockin_phase_deg"]))
        _, lockin = _calibration_state(ctx, dynamic=True)
        pm_settings = ctx.carrier.begin_pm()
        pm_rad = float(pm_settings["mod_index_rad"])
        ctx.state["pm_alignment"] = dict(skipped=True, method="mf_mod_coherent_direct",
                                         command_deg=pm_settings["command_deg"], index_rad=pm_rad,
                                         quality_passed=True, quality_warnings=[], reason="")
        ctx.save()
        _sweep(ctx, ctx.carrier, cal, lockin)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    ctx.outputs["相位灵敏度"] = "基带频率响应采集完成；待无调制噪声基线"
    report_runtime_progress("A05", "基带频响完成", 95, estimated_remaining_seconds=None)


def noise_measurement(ctx):
    try:
        _, lockin = _calibration_state(ctx, dynamic=True)
        if not any(row.get("accepted") for row in ctx.state.get("transfer") or ()):
            raise SafetyAbort("缺少本轮有效频响采集记录；请先运行 A05")
        _baseline(ctx, ctx.carrier, lockin)
    except ValueError as exc:
        raise SafetyAbort(str(exc)) from exc
    ctx.outputs["相位灵敏度"] = "数据已读入；频响与相位噪声详情见离线分析"
    report_runtime_progress("A06", "完成原始采集，开始安全恢复", 95, estimated_remaining_seconds=None)
