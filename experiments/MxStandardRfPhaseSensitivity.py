"""MxStandardRfPhaseSensitivity 薄命令行入口。"""

from lab_workflows.experiment_modules.mx_standard_rf_phase_sensitivity import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
