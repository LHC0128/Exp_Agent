"""标准实验运行目录与配置快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..common import find_project_root, load_safety_limits
from ..instrument_config import instrument_config_snapshot


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
    instrument_snapshot = instrument_config_snapshot(project_root)
    config = {
        "experiment_type": experiment_type,
        "schema_version": schema_version,
        "run_tag": run_tag,
        "timestamp": timestamp,
        "parameters": parameters,
        "mapping_snapshot": instrument_snapshot["resolved_mapping"],
        "device_library_revision": instrument_snapshot["device_library_revision"],
        "physical_mapping_revision": instrument_snapshot["physical_mapping_revision"],
        "device_library_snapshot": instrument_snapshot["device_library"],
        "physical_mapping_snapshot": instrument_snapshot["physical_mappings"],
        "safety_limits_snapshot": load_safety_limits(project_root),
        "actual_rates": {},
        "data_files": [],
    }
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
    return RunDirectory(root, raw, results, config_path)
