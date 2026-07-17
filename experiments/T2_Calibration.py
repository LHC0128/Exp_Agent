"""T2 光学 FID 标定薄命令行入口。"""

from lab_workflows.experiment_modules.t2_calibration import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
