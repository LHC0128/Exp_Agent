"""XY Probe AOM AM 噪声谱离线分析入口。"""

from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.definition import ADAPTER


if __name__ == "__main__":
    raise SystemExit(ADAPTER.analyze_cli())
