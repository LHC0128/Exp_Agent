"""π 脉冲宽度原子域定标的薄命令行入口。"""

from lab_workflows.experiment_modules.pi_width_scan import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
