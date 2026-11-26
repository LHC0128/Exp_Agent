"""A03 整周射频相位扫描：复用「射频相位灵敏度 A03」的 MFLI MF-MOD 公共扫描。

本模块只绑定本实验的帧标签、写盘回调、温控回调与状态键：每个相位点由共享扫描在
独立温控窗口内采集一段 Demod 4 的同步 X/Y，恢复温控并等待后写 ``raw/coarse_XX.npz``；
扫描结束后把 Demod 4 相位转到实测工作点并保存 ``phase_response`` 状态。
"""
from __future__ import annotations

import time

from ...experiment_runtime import report_runtime_progress
from ...steps.coil_drive_chain import sleep
from ..z_chain_common import rf_phase
from . import preparation, run_format


def measure_phase_response(ctx):
    """A03：Z 波形已 arm，逐相位调 MFLI 载波相位并采集 Demod 4 的同步 X/Y。"""
    p = ctx.params
    carrier = getattr(ctx, "carrier", None)
    if carrier is None:
        raise preparation.SafetyAbort("缺少本轮已准备的 MFLI 载波会话；请先运行 A02")
    phases = p.phase_angles_deg()
    start = time.monotonic()
    ctx.state["phase_response_acquisition"] = {
        "mode": "shared_rf_phase_scan",
        "stage": "A03",
        "point_duration_s": p.static_point_duration_s,
        "phases_deg": phases,
        "demod_idx": p.demod0_idx,
        "signals": ["sample.x", "sample.y"],
        "frame_kind": "xy",
        "frame_label_prefix": run_format.COARSE_FILE_PREFIX,
        "temperature_gating": "each_point",
    }

    def save_frame(root, label, kind, metadata, **arrays):
        """把共享扫描的帧写盘适配到本实验 run_format，并补记载波指令相位。"""
        command = getattr(carrier, "command_deg", None)
        return run_format.write_frame(
            root, label, kind, dict(metadata, carrier_command_deg=command), **arrays)

    rf_phase.phase_response(
        ctx, carrier,
        save_frame=save_frame,
        write_temperature=preparation.write_temperature,
        wait=sleep,
        grid_deg=phases,
        stage_tag="A03",
        state_key="phase_response",
        output_key="相位响应",
    )
    elapsed = time.monotonic() - start
    ctx.outputs["相位响应"] = (
        f"{len(phases)} 个射频相位点、每点 {p.static_point_duration_s:g} s 同步 X/Y 采集；"
        f"Demod {p.demod0_idx + 1} 已转到实测工作点，用时 {elapsed:.1f} s"
    )
    report_runtime_progress("A03", "整周相位响应采集完成", 96, estimated_remaining_seconds=None)
    return ctx.state.get("phase_response")
