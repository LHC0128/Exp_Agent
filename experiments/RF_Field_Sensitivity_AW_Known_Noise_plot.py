# %% [markdown]
# # 已知噪声下 RF 灵敏度：独立离线分析
# %% 分析入口
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.analysis import analyze

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="离线分析已知噪声下 RF 灵敏度")
    parser.add_argument("run_dir", type=Path)
    analyze(parser.parse_args().run_dir)
