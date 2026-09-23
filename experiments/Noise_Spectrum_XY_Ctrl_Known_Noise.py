"""XY 控制测量已知可控噪声谱薄命令行入口。"""

from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
