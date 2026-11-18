"""BperpNull 薄命令行入口。"""

from lab_workflows.experiment_modules.bperp_null import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
