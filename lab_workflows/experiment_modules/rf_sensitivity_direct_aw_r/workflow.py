# %% [markdown]
# # DirectAW R：复用设备会话与采集，仅改变步骤顺序及阶段频率

# %% 导入
from ...experiment_runtime import load_runtime_params
from ..rf_sensitivity_direct_aw import workflow as shared, phase, acquisition
from . import run_format
from .models import RFSensitivityDirectAWRParams
from .stages import ACQUISITION


class Workflow(shared.Workflow):
    stages = ACQUISITION
    params_type = RFSensitivityDirectAWRParams
    identity = run_format

    def _execute_step(self, item):
        if item == "A13":
            acquisition.restore_response(self)
            phase.set_rf_frequency(self, self.params.z_rf_freq)
        elif item == "A12":
            phase.set_rf_frequency(self, self.params.z_rf_noise_freq)
        super()._execute_step(item)
        if item == "A12":
            self.outputs["Z"] = "ON"
            acquisition.record_stage_settings(self, "calibration")
            self.save()


# %% 完整采集和分步会话调用同一工作流
def run(params, *, project_root=None):
    return shared.run(params, project_root=project_root, workflow_type=Workflow)


def main():
    run(load_runtime_params(RFSensitivityDirectAWRParams))


if __name__ == "__main__":
    main()
