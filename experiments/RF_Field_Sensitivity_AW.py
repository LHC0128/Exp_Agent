# %% [markdown]
# # RF 灵敏度（DirectAW）采集入口
# 当前默认参数来自 params/experiments/rf-sensitivity-aw.yaml。

# %% 导入
from lab_workflows.common import find_project_root
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.workflow import run

# %% 正式运行
def main() -> None:
    params = RFSensitivityDirectAWParams.from_yaml(find_project_root() / "params/experiments/rf-sensitivity-aw.yaml")
    run(params)


if __name__ == "__main__":
    main()
