# %% [markdown]
# # Z 任意波实际电流验证：完整与分步共用同一采集业务
# %% 导入
from ...experiment_runtime import load_runtime_params
from ..z_aw_waveform_scope_check.workflow import Workflow as ScopeWorkflow, run_scope_check
from ..z_aw_waveform_scope_check.excitation import _configure_outputs
from . import preparation
from .models import ZAWCurrentWaveformScopeCheckParams
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION

EXECUTION_MODE = "typed_workflow"


class Workflow(ScopeWorkflow):
    """只替换目标电流准备；设备连接、采集、取消和收尾沿用电压验证。"""

    acquisition_stages = ACQUISITION
    preflight = staticmethod(preparation.preflight)

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id,
                         experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, measurement_mode="current")

    def _execute_step(self, item):
        if item == "A03":
            self.source = preparation.prepare_control(self.params, self.root)
            self.outputs_started = True
            _configure_outputs(self.params, self.devices, self.channels, self.source.theory, self.source.applied)
            self.outputs.update({"Z 任意波": "外部下降沿 Burst ON", "共同触发": "OFF"})
        else:
            super()._execute_step(item)


# %% 完整采集
def run(params: ZAWCurrentWaveformScopeCheckParams, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != ctx.finish_id])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(ZAWCurrentWaveformScopeCheckParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
