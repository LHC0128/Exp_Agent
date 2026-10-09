"""静磁场灵敏度离线分析薄入口。"""

# %% Cell 0 - 显式选择运行目录，不连接硬件
import argparse
from pathlib import Path

from lab_workflows.experiment_modules.static_sensitivity.analysis import analyze
from lab_workflows.experiment_runtime import runtime_run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="离线分析版本 2 的直流色散，旧 RAMP 仅能查看已有结果。")
    parser.add_argument("run_dir", nargs="?", type=Path, help="运行目录；省略时必须设置 LAB_RUN_DIR")
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir is not None else runtime_run_dir()
    analyze(run_dir)
    print(f"静磁场灵敏度离线分析完成: {run_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
