# %% Bell Bloom Z 磁场频率标定：显式指定目录进行离线分析
import argparse
from pathlib import Path

from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.definition import ADAPTER


def main() -> int:
    parser = argparse.ArgumentParser(description="Bell Bloom Z 标定离线分析")
    parser.add_argument("run_dir", type=Path, help="包含 raw/ 的运行目录")
    return ADAPTER.analyze_cli(parser.parse_args().run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
