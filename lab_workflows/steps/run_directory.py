"""标准实验运行目录与配置快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..common import find_project_root, load_mapping, load_safety_limits


@dataclass(slots=True)
class RunDirectory:
    root: Path
    raw: Path
    results: Path
    config_path: Path

    def update_config(self, **updates: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.config_path.exists():
            with self.config_path.open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream) or {}
        data.update(updates)
        with self.config_path.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
        return data


def create_run_directory(
    experiment_type: str,
    run_tag: str,
    parameters: dict[str, Any],
    *,
    schema_version: int = 1,
    project_root: Path | None = None,
) -> RunDirectory:
    project_root = project_root or find_project_root()
    timestamp = datetime.now().strftime("%m%d_%H%M%S")
    root = project_root / "data" / experiment_type / f"{timestamp}_{run_tag}"
    raw = root / "raw"
    results = root / "results"
    raw.mkdir(parents=True, exist_ok=False)
    results.mkdir(exist_ok=True)
    config_path = root / "experiment_config.yaml"
    config = {
        "experiment_type": experiment_type,
        "schema_version": schema_version,
        "run_tag": run_tag,
        "timestamp": timestamp,
        "parameters": parameters,
        "mapping_snapshot": load_mapping(project_root),
        "safety_limits_snapshot": load_safety_limits(project_root),
        "actual_rates": {},
        "data_files": [],
    }
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
    return RunDirectory(root, raw, results, config_path)
