"""XY 正弦控制噪声谱薄命令行入口。"""

from lab_workflows.experiment_modules.noise_spectrum_xy import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
