"""按阶段逐版本激活控制，保留各自的校相与响应设置。

共享实现在 `lab_workflows.steps.direct_aw_control_runs`；硬件相关的 activate 与
本模块的取消检查、等待注入留在实验侧，公开导入保持可用。
"""
from dataclasses import replace
from ...experiment_runtime import check_cancelled
from ...steps.direct_aw_control_runs import (
    execute_controls as _execute_controls,
    inherit_control,
    make_control_directory,
    seal_controls as _seal_controls,
)
from lockin_amplifier import demod
from . import excitation as shared_excitation
from ._helpers import wait


def activate(ctx, step, *, settle_wait=None):
    if step == "A09":
        return
    if ctx.xy_phase is None or not ctx.xy_phase.converged:
        raise ValueError("当前控制版本 XY 校相未通过")
    shared_excitation.upload_xy(ctx.devices["signals"], ctx.devices["channels"], ctx.params,
        ctx.envelope, ctx.xy_phase.final_phase_deg)
    demod.configure_demodulator(ctx.devices["hf2"], replace(ctx.d0_config,
        phase=ctx.phase0.phase_shift_deg, time_constant=ctx.params.demod0_tc_meas))
    if ctx.response_config is not None:
        demod.configure_demodulator(ctx.devices["hf2"], ctx.response_config, for_acquisition=True)
        ctx.demod_mode = "response"
    (settle_wait or wait)(ctx.params.system_settle_s)


def execute_controls(parent, step, *, activate_control=None):
    return _execute_controls(parent, step, activate_control=activate_control or activate,
                             check_cancelled=check_cancelled)


def seal_controls(parent):
    return _seal_controls(parent, check_cancelled=check_cancelled)
