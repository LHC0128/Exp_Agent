# %% [markdown]
# # 多控制整体幅度扫描：完整运行与交互会话共用业务步骤
#
# 执行引擎在共享层 `lab_workflows.steps.direct_aw_control`；节点动作与安全收尾在
# 父实验模块的 `execute_shared_nodes` / `shutdown_outputs`，父子实验共用同一份实现。
# 本模块只声明本实验的阶段清单、运行格式与逐控制 × Scale 的扫描编排。

# %% 导入
import hashlib
from pathlib import Path
import time
import numpy as np
from ...experiment_runtime import load_runtime_params, report_runtime_progress
from ...steps import direct_aw_state
from ...steps.direct_aw_control import DirectAWControlRun
from ...steps.direct_aw_control_runs import create_control_run as _create_control_run
from ..rf_sensitivity_direct_aw import preparation as direct_preparation
from ..rf_sensitivity_direct_aw._helpers import _RuntimeCancellation
from ..rf_sensitivity_direct_aw.stages import FINISH_ID, SINGLE_ACQUISITION
from ..rf_sensitivity_direct_aw.workflow import execute_shared_nodes, shutdown_outputs
from .models import RFControlScaleParams
from .preparation import preflight, new_directory
from .acquisition import acquire_scales, seal_points
from . import stages, run_format


# 采集与结束节点：A13 逐控制 × Scale 测量，A15 结束本轮采集。
RUN_ID = "A15"


def _start_run(ctx):
    """建立本轮的运行目录初始元数据、取消令牌与计时起点（改前由父执行类隐式提供）。"""
    ctx.state = direct_aw_state.initial_state(ctx.params, identity=ctx.identity)
    ctx.cancellation = _RuntimeCancellation()
    ctx.started = time.monotonic()


def _report_progress(ctx, stage, completed):
    """采集单元进度：RF 响应点数与噪声平均次数合计。"""
    total = ctx.params.rf_amp_points + ctx.params.noise_n_avg
    report_runtime_progress(stage, f"已完成 {completed}/{total} 个采集单元", 25 + 65 * completed / total,
        estimated_remaining_seconds=(time.monotonic() - ctx.started) / completed * (total - completed))


class ControlWorkflow(DirectAWControlRun):
    """单个控制的子运行：逐点校相与测量由本实验的 acquisition 驱动。"""
    params_type = RFControlScaleParams
    identity = run_format
    finish_id, run_id = FINISH_ID, RUN_ID
    stages = SINGLE_ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        _start_run(self)

    @classmethod
    def create_control_run(cls, params, **kwargs):
        """创建已选定一个控制的子运行；共享工厂返回 (ctx, run_dir)，这里保持只返回 ctx。"""
        ctx, _ = _create_control_run(cls, params, **kwargs)
        return ctx

    def _control_run_metadata(self):
        return dict(
            control_source_sha256=hashlib.sha256(Path(self.envelope["source_waveform_path"]).read_bytes()).hexdigest(),
            raw_format_version=run_format.RAW_FORMAT_VERSION,
            phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE,
            control_metrics={
                "envelope_rms_V": float(np.sqrt(np.mean(self.envelope["envelope_V"] ** 2))),
                "envelope_peak_V": float(np.max(np.abs(self.envelope["envelope_V"]))),
            })

    def preflight(self, params):
        return direct_preparation.preflight(params, self.root, experiment_id=run_format.EXPERIMENT_ID)

    def progress(self, stage, completed):
        _report_progress(self, stage, completed)

    def _execute_step(self, item):
        """逐点校相与测量节点：与父实验共用同一份节点动作。"""
        execute_shared_nodes(self, item)

    def shutdown(self, failure):
        return shutdown_outputs(self, failure)


class Workflow(DirectAWControlRun):
    """多控制编排：每个控制建立子运行，逐点执行独立校相与测量。"""
    params_type = RFControlScaleParams
    identity = run_format
    finish_id, run_id = FINISH_ID, RUN_ID
    stages = stages.ACQUISITION

    def __init__(self, *args, **kwargs):
        self.controls = {}
        super().__init__(*args, **kwargs)
        _start_run(self)
        self.state.update(raw_format_version=run_format.RAW_FORMAT_VERSION,
                          phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE)

    def progress(self, stage, completed):
        _report_progress(self, stage, completed)

    def on_quality_failure(self):
        """校相门槛失败后在原会话内恢复温控，保持可重试的物理状态。"""
        direct_preparation.restore_temperature(self)

    def preflight(self, params):
        return preflight(params, self.root)

    def _new_directory(self, selected):
        new_directory(self, selected, ControlWorkflow)

    def update_parameters(self, values):
        if self.devices and values.get("TEC_EXTERNAL_CONTROL", self.params.tec_external_control) != self.params.tec_external_control:
            raise ValueError("改变 TEC 控制来源前必须结束并新建会话")
        super().update_parameters(values)

    def _execute_step(self, item):
        if item == "A13":
            # 首点校相失败时还没有响应计数，也必须给重试创建新目录。
            if (self.directory.raw / "point_manifest.json").exists():
                self._new_directory([item])
            acquire_scales(self)
            return
        if item == "A02" and self.params.tec_external_control and not self.devices:
            self.devices = direct_preparation.connect_devices(self.mapping, params=self.params,
                experiment_id=run_format.EXPERIMENT_ID, connect_tec=False)
        execute_shared_nodes(self, item)
        if item == "A02":
            for ctx in self.controls.values():
                ctx.devices = self.devices
        if item == "A15":
            try:
                seal_points(self)
            except BaseException as exc:
                self.state.update(completion_status="failed", failure_reason=f"子运行封存失败：{exc}")
                raise

    def shutdown(self, failure):
        return shutdown_outputs(self, failure)


# %% 完整采集入口
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([step.id for step in ctx.stages if step.id != FINISH_ID])
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(RFControlScaleParams))


if __name__ == "__main__":
    main()
