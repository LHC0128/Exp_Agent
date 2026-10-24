# %% [markdown] Cell 0
# # T1 标定：CH1 PDB 差分、CH4 同步下降沿；结束将 X/Y/Z 场归零关闭。
# 参数来源：params/experiments/t1-calibration.yaml；GUI 完整/分步共用同一工作流。

# %% Cell 1
from lab_workflows.experiment_modules.t1_calibration.workflow import main

# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
