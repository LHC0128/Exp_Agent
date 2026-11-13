# %% [markdown]
# # 已知噪声下的 RF 灵敏度：完整运行与分步会话共用流程
#
# 采集引擎（会话状态、快照、发布、保存、运行目录轮换、执行循环、结束）在共享层
# `lab_workflows.steps.direct_aw_control`；DirectAW 家族的节点动作与安全收尾由父实验
# 模块级 `execute_shared_nodes` / `shutdown_outputs` 提供，父实验与两个子实验共用同一份
# 实现。本模块只声明阶段清单、运行格式、多控制编排与本实验的覆写节点。导入不连接硬件。
# %% 导入
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import time

import numpy as np

from ...experiment_runtime import load_runtime_params, report_runtime_progress
from ...steps import direct_aw_state
from ...steps.direct_aw_control import DirectAWControlRun
from ...steps.direct_aw_control_runs import create_control_run as _create_control_run
from ..rf_sensitivity_direct_aw import preparation as direct_preparation
from ..rf_sensitivity_direct_aw._helpers import _RuntimeCancellation
from ..rf_sensitivity_direct_aw.stages import FINISH_ID, SINGLE_ACQUISITION, invalidate, invalidated_stages
from ..rf_sensitivity_direct_aw.workflow import execute_shared_nodes, shutdown_outputs
from .models import RFSensitivityKnownNoiseParams
from .preparation import preflight
from .waveform import generate_noise
from .excitation import upload_z
from .acquisition import execute_controls, seal_controls
from . import run_format, stages


# 采集与结束节点：A15 完成本轮采集，A16 安全收尾并关闭会话。
RUN_ID = "A15"
# 输出显示键由共享引擎按本清单建立，键序与历史会话一致。
OUTPUT_KEYS = ("X", "Y", "Z", "温控", "Pump", "共用触发")


def initialise_session(ctx):
    """写入运行目录初始元数据、取消令牌与进度计时；本实验两个执行类共用。"""
    ctx.state = direct_aw_state.initial_state(ctx.params, identity=ctx.identity)
    ctx.cancellation = _RuntimeCancellation()
    ctx.started = time.monotonic()


def report_progress(ctx, stage, completed):
    total = ctx.params.rf_amp_points + ctx.params.noise_n_avg
    report_runtime_progress(stage, f"已完成 {completed}/{total} 个采集单元", 25 + 65 * completed / total,
        estimated_remaining_seconds=(time.monotonic() - ctx.started) / completed * (total - completed))


class ControlWorkflow(DirectAWControlRun):
    """单个控制版本的子运行：噪声波形由父会话注入，先建目录再保存同一份噪声。"""
    stages = SINGLE_ACQUISITION
    params_type = RFSensitivityKnownNoiseParams
    identity = run_format
    finish_id, run_id = FINISH_ID, RUN_ID
    output_keys = OUTPUT_KEYS

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        initialise_session(self)

    @classmethod
    def create_control_run(cls, params, *, parent_directory, project_root, session_id,
                           devices, noise, shared_calibration=None, stage_states=None,
                           previous=None, affected_stages=frozenset()):
        """复用本轮已生成的噪声；先建目录和继承有效数据，再保存同一份噪声。"""
        ctx, _ = _create_control_run(cls, params, parent_directory=parent_directory,
            project_root=project_root, session_id=session_id, devices=devices,
            shared_calibration=shared_calibration, stage_states=stage_states,
            previous=previous, affected_stages=affected_stages)
        ctx.noise, ctx.z_rf_frequency_hz = noise, params.z_rf_freq
        np.savez(ctx.directory.raw / "noise_waveform.npz", **noise)
        return ctx

    def _control_run_metadata(self):
        return {"control_metrics": {
            "envelope_rms_V": float(np.sqrt(np.mean(self.envelope["envelope_V"] ** 2))),
            "envelope_peak_V": float(np.max(np.abs(self.envelope["envelope_V"]))),
            "repeat_frequency_Hz": self.envelope["source_repeat_freq_Hz"],
        }}

    def preflight(self, params):
        return preflight(params, self.root)

    def upload_z_rf(self, amplitude, phase_base=None):
        return upload_z(self, amplitude, phase_base)

    def _execute_step(self, item):
        execute_shared_nodes(self, item)
        if item == "A14" or item == "A13B":
            self.outputs["Z"] = "ON"

    def _is_quality_failure(self, item, exc):
        """校相门槛失败与 A11 扫描拟合失败都在原会话内停止后继，可重试。"""
        return super()._is_quality_failure(item, exc) or (item == "A11" and isinstance(exc, ValueError))

    def on_quality_failure(self):
        direct_preparation.restore_temperature(self)

    def progress(self, stage, completed):
        report_progress(self, stage, completed)

    def after_step(self, item):
        super().after_step(item)
        if item == self.run_id:
            # 本轮完成后校相链失效以便下次显式重跑；本轮结束节点本身保持已完成。
            record = dict(self.states[item])
            invalidate(self.stages, self.states, "A09")
            self.states[item] = record

    def shutdown(self, failure):
        return shutdown_outputs(self, failure)


class Workflow(DirectAWControlRun):
    """多控制整体编排：逐控制建立子运行并聚合运行配置。"""
    stages = stages.ACQUISITION
    params_type = RFSensitivityKnownNoiseParams
    identity = run_format
    finish_id, run_id = FINISH_ID, RUN_ID
    output_keys = OUTPUT_KEYS

    def __init__(self, *args, **kwargs):
        self.controls = {}
        super().__init__(*args, **kwargs)
        initialise_session(self)

    def preflight(self, params):
        return preflight(params, self.root)

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        old_controls = self.controls
        if previous and self.state["completion_status"] in ("running", "acquired"):
            previous.update_config(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据")
        # 子运行按实际有效前置复制，避免共用编排把旧噪声记录带到新轮次。
        self.directory = None
        super()._new_directory(selected)
        for key in ("calibration", "clock_sync", "temperature_control", "optional_connection_errors", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        affected = {stage.id for item in selected if item != "A01" for stage in invalidated_stages(self.stages, item)}
        self.controls = {}
        noise = generate_noise(self.params)
        np.savez(self.directory.raw / "noise_waveform.npz", **noise)
        self.state["data_files"] = ["raw/noise_waveform.npz"]
        self.state["controls_sealed"] = False
        for relative in self.params.control_waveform_files:
            params = replace(self.params, arb_waveform_file=relative, control_waveform_files=[relative])
            ctx = ControlWorkflow.create_control_run(params, parent_directory=self.directory,
                project_root=self.root, session_id=self.session_id, devices=self.devices,
                noise=noise, shared_calibration=self.state["calibration"],
                stage_states=self.states, previous=old_controls.get(Path(relative).parent.parent.name),
                affected_stages=affected)
            self.controls[ctx.directory.root.name] = ctx
        self.save()

    def _execute_step(self, item):
        if item in ("A09", "A10", "A11", "A12", "A13", "A14", "A13B"):
            execute_controls(self, item)
        else:
            execute_shared_nodes(self, item)
            if item == "A02":
                for ctx in self.controls.values():
                    ctx.devices = self.devices
            if item == "A15":
                seal_controls(self)

    def _is_quality_failure(self, item, exc):
        """校相门槛失败与 A11 扫描拟合失败都在原会话内停止后继，可重试。"""
        return super()._is_quality_failure(item, exc) or (item == "A11" and isinstance(exc, ValueError))

    def on_quality_failure(self):
        direct_preparation.restore_temperature(self)

    def progress(self, stage, completed):
        report_progress(self, stage, completed)

    def after_step(self, item):
        super().after_step(item)
        if item == self.run_id:
            # 本轮完成后校相链失效以便下次显式重跑；本轮结束节点本身保持已完成。
            record = dict(self.states[item])
            invalidate(self.stages, self.states, "A09")
            self.states[item] = record

    def shutdown(self, failure):
        return shutdown_outputs(self, failure)


# %% 完整采集入口
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        selected = [stage.id for stage in ctx.stages if stage.id not in ("A16", "A13B")]
        if params.linewidth_mode == "frequency_sweep":
            selected.insert(selected.index("A15"), "A13B")
        ctx.execute(selected)
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(RFSensitivityKnownNoiseParams))


if __name__ == "__main__":
    main()
