# %% [markdown]
# # Z 实际电流闭环：完整运行和分步会话共用业务步骤
# %% 导入
from ...experiment_runtime import load_runtime_params
from ..z_aw_waveform_scope_check.workflow import Workflow as ScopeWorkflow
from . import acquisition, preparation
from .harmonic_feedback import MeasurementQualityError
from .models import ZAWClosedLoopWaveformCorrectionParams
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION

EXECUTION_MODE = "typed_workflow"


class Workflow(ScopeWorkflow):
    """复用设备会话、修订、参数失效、时钟和安全结束。"""
    acquisition_stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id,
            experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE)
        self.prepared = None

    def _execute_step(self, item):
        if item == "A03":
            self.prepared = preparation.prepare_closed_loop(self.root, self.params)
        elif item == "A04":
            self.scope_config, self.scope_snapshot = preparation.prepare_scope(self)
            self.outputs["Scope"] = "C4 下降沿单次采集已配置"
        elif item == "A05":
            scope = self.devices["scope"]
            if (not str(scope.get_trigger_mode()).upper().startswith("SING") or
                    str(scope.get_trigger_source()).upper() != "C4"):
                from .stages import invalidate
                invalidate(ACQUISITION, self.states, "A04")
                raise ValueError("Scope 模式已改变，请重新执行 A04 Scope 配置")
            self.states[item]["artifacts"] = acquisition.acquire_run(self)
        else:
            super()._execute_step(item)

    def execute(self, selected):
        try:
            return super().execute(selected)
        except MeasurementQualityError as exc:
            self.states["A05"] = {"status": "quality_failed", "error": str(exc)}
            self.publish(str(exc), "error")
            return self.snapshot()


# %% 完整采集
def run(params: ZAWClosedLoopWaveformCorrectionParams, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != ctx.finish_id])
        if ctx.states["A05"]["status"] == "quality_failed":
            raise MeasurementQualityError(ctx.states["A05"]["error"])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(ZAWClosedLoopWaveformCorrectionParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
