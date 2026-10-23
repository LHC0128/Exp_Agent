# %% [markdown]
# # 多控制 Scale RF 独立离线分析
# 只读取明确指定的运行目录；不连接仪器或依赖当前理论波形目录。

# %% 导入
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.analysis import analyze


# %% 选择运行目录并分析
def main():
    parser = argparse.ArgumentParser(description="多控制 Scale RF 响应与灵敏度离线分析")
    parser.add_argument("run_dir", type=Path)
    analyze(parser.parse_args().run_dir)


if __name__ == "__main__":
    main()
