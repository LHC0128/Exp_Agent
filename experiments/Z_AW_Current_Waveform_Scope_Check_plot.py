"""实际电流波形离线分析薄入口，必须显式指定运行目录。"""
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check import ADAPTER


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="待分析的明确运行目录")
    raise SystemExit(ADAPTER.analyze_cli(parser.parse_args().run_dir))
