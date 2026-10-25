# %% [markdown]
# # 固定 XY 的 Z 射频场频率响应
# 默认参数见 params/experiments/rf-sensitivity-constxy-frequency.yaml。
# 完整运行与 GUI 分步流程共用模块；0 Hz 背景保持 XY，仅关闭 Z。
# 说明：docs/experiments/rf_sensitivity_constxy_frequency.md
# %% 采集入口
import argparse
from pathlib import Path
import subprocess
import sys
from lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.definition import ADAPTER


def main():
    parser = argparse.ArgumentParser(description="ConstXY RF 扫频；默认运行会连接真实仪器")
    parser.add_argument("--acquisition-test", action="store_true", help="只运行假仪器采集回归，不连接硬件")
    args = parser.parse_args()
    if args.acquisition_test:
        test = Path(__file__).resolve().parents[1] / "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/test_workflow.py"
        return subprocess.run([sys.executable, "-m", "pytest", str(test)], check=False).returncode
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
