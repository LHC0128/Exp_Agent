"""示波器离线分析薄入口，必须明确指定运行目录。"""

# %% 选择历史运行并分析
import argparse
from pathlib import Path
from lab_workflows.experiment_modules.scope_capture import ADAPTER

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="示波器离线分析")
    parser.add_argument("run_dir", type=Path)
    raise SystemExit(ADAPTER.analyze_cli(parser.parse_args().run_dir))
