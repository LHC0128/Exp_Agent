# %% 局部优化批次的离线汇总薄入口
"""必须显式指定批次目录；不连接仪器，也不重新拟合旧 RAMP 数据。"""
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.definition import ADAPTER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    return ADAPTER.analyze_cli(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
