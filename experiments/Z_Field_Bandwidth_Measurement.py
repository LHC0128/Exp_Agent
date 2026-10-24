# %% [markdown]
# # Z 场频率响应与带宽
# 默认参数见 params/experiments/z-field-bandwidth.yaml。
# 完整运行与 GUI 分步流程共用模块；0 Hz 为 Z OFF 背景，XY 保持关闭。
# 说明：docs/z_field_bandwidth.md
# %% 采集入口
import argparse
from pathlib import Path
import subprocess
import sys
from lab_workflows.experiment_modules.z_field_bandwidth.definition import ADAPTER


def main():
    parser = argparse.ArgumentParser(description="Z 场带宽 RF 扫频；默认运行会连接真实仪器")
    parser.add_argument("--acquisition-test", action="store_true", help="只运行假仪器采集回归，不连接硬件")
    args = parser.parse_args()
    if args.acquisition_test:
        test = Path(__file__).resolve().parents[1] / "lab_workflows/experiment_modules/z_field_bandwidth/test_workflow.py"
        return subprocess.run([sys.executable, "-m", "pytest", str(test)], check=False).returncode
    return ADAPTER.run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
