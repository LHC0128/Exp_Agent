"""π 脉冲面积标定的薄命令行入口。"""

from lab_workflows.experiment_modules.pi_pulse_calibration import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
