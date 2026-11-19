"""射频相位响应的薄命令行入口。"""

from lab_workflows.experiment_modules.rf_phase_response import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
