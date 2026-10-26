# %% [markdown]
# # Z 线圈实际电流频响：完整与分步共用编排
# %% 导入
from pathlib import Path
from ...experiment_runtime import load_runtime_params
from ..z_coil_inductance_frequency_response.workflow import Workflow as FrequencyWorkflow, run_frequency_response
from ..z_coil_inductance_frequency_response.preparation import preflight
from .acquisition import validate_capture_current as _validate_capture_current
from .models import ZCoilCurrentFrequencyResponseParams
from .run_format import EXPERIMENT_ID, DATA_TYPE, OUTPUT_PROTOCOL, COMMAND_VOLTAGE_REFERENCE
from .stages import ACQUISITION, FINISH_ID

EXECUTION_MODE = "typed_workflow"


class Workflow(FrequencyWorkflow):
    """共用准备、正弦、SDS 采集与收尾；注入电流实验的轴、校验及步骤。"""
    acquisition_stages = ACQUISITION
    preflight = staticmethod(preflight)

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id,
            experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE,
            acquisition_signals=["SDS CH3 low-side sense-resistor voltage", "SDS CH4 common trigger reference"],
            physical_limit="CH3 仅允许跨接低端采样电阻；仅可靠同帧驱动参考可用于闭环相位标定。",
            completion_label="Z 线圈实际电流频率响应")
        self._refresh_capture_options()

    def _refresh_capture_options(self):
        self.configure_capture(capture_validator=lambda frame: _validate_capture_current(frame, self.params),
            capture_guard_s=self.params.scope_capture_guard_s,
            drive_metadata=dict(output_protocol=OUTPUT_PROTOCOL, command_voltage_reference=COMMAND_VOLTAGE_REFERENCE,
                frequency_spacing=self.params.frequency_spacing,
                frequency_points_per_decade=self.params.frequency_points_per_decade))

    def update_parameters(self, values):
        super().update_parameters(values)
        self._refresh_capture_options()


# %% 完整采集；保留旧 run_frequency_response 注入入口
def run(params: ZCoilCurrentFrequencyResponseParams, *, project_root=None) -> Path:
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main() -> int:
    run(load_runtime_params(ZCoilCurrentFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
