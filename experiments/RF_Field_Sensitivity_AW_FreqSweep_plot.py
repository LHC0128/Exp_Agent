"""RF DirectAW 频率响应离线分析薄入口。"""

import argparse
from pathlib import Path

from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency import ADAPTER


# 这两个名称保留给尚未迁移的外部 AM 实验。LegacyScriptAdapter 会在
# 隔离子进程中覆盖它们，因此旧实验仍能把指定运行目录交给同一分析入口。
USE_LATEST = True
DATA_DIR: Path | None = None


def main() -> int:
    if DATA_DIR is not None:
        return ADAPTER.analyze_cli(DATA_DIR)
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", type=Path)
    args = parser.parse_args()
    return ADAPTER.analyze_cli(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
