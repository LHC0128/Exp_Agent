# %% [markdown]
# # 多控制 Scale RF 响应与灵敏度
# 沿用 RF DirectAW；控制多选，不注入人为噪声。

# %% 导入
from lab_workflows.common import find_project_root
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.models import RFControlScaleParams
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.workflow import run


# %% 正式运行
def main():
    params = RFControlScaleParams.from_yaml(find_project_root() / "params/experiments/rf-control-scale-sensitivity.yaml")
    run(params)


if __name__ == "__main__":
    main()
