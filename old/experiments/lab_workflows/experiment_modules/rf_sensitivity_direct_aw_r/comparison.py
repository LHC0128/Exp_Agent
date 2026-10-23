"""仅比较 R 实验历史，复用已有英文图例及 PNG/PDF 导出。"""
import json
import os
from pathlib import Path
from ..rf_sensitivity_direct_aw import comparison as shared
from . import run_format


def compare_runs(runs, output_dir):
    return shared.compare_runs(runs, output_dir, identity=run_format)


def main():
    compare_runs(json.loads(os.environ["LAB_TYPED_PARAMETERS"])["runs"], Path(os.environ["LAB_RUN_DIR"]))


if __name__ == "__main__":
    main()
