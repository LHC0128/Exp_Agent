"""区分整组灵敏度偏差与少数高噪声记录拉高平均 PSD（正式模块 noise_diagnostics 的命令行薄入口）。"""
# %% [markdown]
# # 区分整组灵敏度偏差与少数高噪声记录拉高平均 PSD
# 用法（仓库根目录）：
# agent_exp_env/Scripts/python.exe scripts/RF_Control_Scale_Sensitivity/analyze_noise_records.py <数据根>/RF_Control_Scale_Sensitivity/<run_id>
# 默认只读诊断，输出到本次运行 results/noise_record_diagnostics/<时间戳>/。
# --whole-group-ratio 调整整组偏差判定倍数；不改原始数据或主分析结果。

# %% 复用正式分析模块
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.noise_diagnostics import main


if __name__ == "__main__":
    main()
