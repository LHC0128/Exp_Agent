"""Mx Z 最优控制 XY 平衡场 RF 相位响应离线分析薄入口。"""

from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response import (
    ADAPTER,
)


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="明确的运行目录；不自动选择 latest")
    raise SystemExit(ADAPTER.analyze_cli(parser.parse_args().run_dir))
