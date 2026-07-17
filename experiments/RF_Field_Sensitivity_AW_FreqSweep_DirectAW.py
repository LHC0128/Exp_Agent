"""RF DirectAW 频率响应薄命令行入口。"""

from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
