"""Mx Z 最优控制 XY 噪声谱离线分析薄入口。"""

from argparse import ArgumentParser
from pathlib import Path

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum import (
    ADAPTER,
)


if __name__ == "__main__":
    parser = ArgumentParser(description="分析明确指定的 XY 噪声运行目录")
    parser.add_argument("run_dir", type=Path, help="包含 experiment_config.yaml 和 raw 的运行目录")
    raise SystemExit(ADAPTER.analyze_cli(parser.parse_args().run_dir))
