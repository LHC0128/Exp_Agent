"""XY DirectAW DC 标定薄命令行入口。"""

from lab_workflows.experiment_modules.xy_direct_aw_dc_calibration import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
