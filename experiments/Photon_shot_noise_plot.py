# %% [markdown]
# # 光子散粒噪声：离线分析薄入口
# 必须显式传入 run_dir，不选择 latest，也不连接仪器。

# %% Cell 1
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.photon_shot_noise.analysis import analyze


def main():
    parser = argparse.ArgumentParser(description="光子散粒噪声离线分析")
    parser.add_argument("run_dir", type=Path, help="明确的实验运行目录")
    args = parser.parse_args()
    analyze(args.run_dir)
    return 0


# %% Cell 2
if __name__ == "__main__":
    raise SystemExit(main())
