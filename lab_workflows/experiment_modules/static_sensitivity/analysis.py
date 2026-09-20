"""静磁场灵敏度运行目录的离线结果索引。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ...experiment_runtime import runtime_run_dir


def analyze(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    if not results_dir.is_dir():
        raise FileNotFoundError(f"结果目录不存在: {results_dir}")
    config_path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    artifacts = sorted(path.name for path in results_dir.iterdir() if path.is_file())
    summary = {
        "success": True,
        "experiment_id": "static-sensitivity",
        "run_dir": str(run_dir),
        "timestamp": str(config.get("timestamp", run_dir.name)),
        "parameters": config.get("parameters", {}),
        "files": artifacts,
        "plot_profile": "paper",
    }
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(summary, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    result = analyze(runtime_run_dir())
    print(f"静磁场灵敏度离线结果已索引: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
