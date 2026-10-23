# %% [markdown]
# # 多控制整体幅度扫描：完整运行与交互会话共用业务步骤
# %% 导入
from ...experiment_runtime import load_runtime_params
from ..rf_sensitivity_direct_aw import workflow as shared
from ..rf_sensitivity_direct_aw import preparation as direct_preparation
from .models import RFControlScaleParams
from .preparation import preflight, new_directory
from .acquisition import acquire_scales, seal_points
from . import stages, run_format


class ControlWorkflow(shared.SingleControlWorkflow):
    params_type = RFControlScaleParams
    identity = run_format

    def preflight(self, params):
        return direct_preparation.preflight(params, self.root, experiment_id=run_format.EXPERIMENT_ID)


class Workflow(shared.SingleControlWorkflow):
    params_type = RFControlScaleParams
    identity = run_format
    stages = stages.ACQUISITION

    def __init__(self, *args, **kwargs):
        self.controls = {}
        super().__init__(*args, **kwargs)
        self.state.update(raw_format_version=run_format.RAW_FORMAT_VERSION,
                          phase_calibration_scope=run_format.PHASE_CALIBRATION_SCOPE)

    def preflight(self, params):
        return preflight(params, self.root)

    def _new_directory(self, selected):
        new_directory(self, selected, ControlWorkflow)

    def update_parameters(self, values):
        if self.devices and values.get("TEC_EXTERNAL_CONTROL", self.params.tec_external_control) != self.params.tec_external_control:
            raise ValueError("改变 TEC 控制来源前必须结束并新建会话")
        super().update_parameters(values)

    def _execute_step(self, item):
        if item == "A02" and self.params.tec_external_control and not self.devices:
            self.devices = direct_preparation.connect_devices(self.mapping, params=self.params,
                experiment_id=run_format.EXPERIMENT_ID, connect_tec=False)
        if item == "A13":
            # 首点校相失败时还没有响应计数，也必须给重试创建新目录。
            if (self.directory.raw / "point_manifest.json").exists():
                self._new_directory([item])
            acquire_scales(self)
        else:
            super()._execute_step(item)
            if item == "A02":
                for ctx in self.controls.values():
                    ctx.devices = self.devices
            if item == "A15":
                try:
                    seal_points(self)
                except BaseException as exc:
                    self.state.update(completion_status="failed", failure_reason=f"子运行封存失败：{exc}")
                    raise


# %% 完整采集入口
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([step.id for step in ctx.stages if step.id != stages.FINISH_ID])
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
