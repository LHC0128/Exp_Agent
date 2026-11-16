"""ZChainStability 薄命令行入口。"""

from lab_workflows.experiment_modules.z_chain_stability import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
