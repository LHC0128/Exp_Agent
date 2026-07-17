"""XY DirectAW Demod3 R 噪声谱薄命令行入口。"""

from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.run_cli())
