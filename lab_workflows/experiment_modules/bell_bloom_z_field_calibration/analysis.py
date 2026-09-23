"""Bell Bloom Z 频率标定离线入口，不连接仪器。"""

from pathlib import Path
import yaml

from ...experiment_runtime import runtime_run_dir
from ..mx_z_field_calibration.analysis import analyze_calibration
from .definition import EXPERIMENT_ID


def analyze(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    with (run_dir / "experiment_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if config["experiment_id"] != EXPERIMENT_ID:
        raise ValueError("运行目录不是 Bell Bloom Z 磁场频率标定")
    return analyze_calibration(
        run_dir, config, config["parameters"]["LINEAR_R_SQUARED_MIN"],
        frequency_label="Pump modulation frequency (kHz)",
    )


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
