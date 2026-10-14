# %% [markdown]
# # 已知噪声下 RF 灵敏度（DirectAW）
# %% 采集入口
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.definition import ADAPTER

if __name__ == "__main__":
    ADAPTER.run(ADAPTER.defaults().to_external(), progress=None, cancellation=None)
