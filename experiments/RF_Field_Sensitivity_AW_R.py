# %% [markdown]
# # RF 灵敏度（DirectAW R）采集入口
# 默认参数来自 params/experiments/rf-sensitivity-aw-r.yaml。

# %% 导入
from lab_workflows.common import find_project_root
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.models import RFSensitivityDirectAWRParams
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.workflow import run


# %% 正式运行
def main():
    params = RFSensitivityDirectAWRParams.from_yaml(find_project_root() / "params/experiments/rf-sensitivity-aw-r.yaml")
    run(params)


if __name__ == "__main__":
    main()
