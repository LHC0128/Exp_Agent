"""标准实验运行目录与配置快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
import logging
import time

import yaml

from ..common import find_project_root, load_safety_limits
from ..experiment_runtime import report_run_directory
from ..instrument_config import instrument_config_snapshot


logger = logging.getLogger(__name__)


def write_run_config(path: Path, config: dict[str, Any]) -> None:
    """同目录临时写入后原子替换；失败保留原配置，不负责并发读改写合并。"""
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
        # Windows 读者可能短暂占用目标；替换前已关闭临时文件。
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("运行配置临时文件清理失败: %s", temporary)


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
                loaded = yaml.safe_load(stream)
                data = {} if loaded is None else loaded
        if not isinstance(data, dict):
            raise ValueError("运行配置必须是 YAML 对象")
        data.update(updates)
        write_run_config(self.config_path, data)
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
    if any(character in run_tag for character in "/\\:\x00"):
        raise ValueError("运行标签不能包含路径分隔符")
    base = project_root / "data" / experiment_type
    base.mkdir(parents=True, exist_ok=True)
    stem = f"{timestamp}_{run_tag}"
    counter = 1
    while True:
        root = base / (stem if counter == 1 else f"{stem}_{counter}")
        try:
            root.mkdir()
            break
        except FileExistsError:
            counter += 1
    raw = root / "raw"
    results = root / "results"
    raw.mkdir()
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
    write_run_config(config_path, config)
    report_run_directory(root, experiment_type)
    return RunDirectory(root, raw, results, config_path)
