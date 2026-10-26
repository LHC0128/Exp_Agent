"""分步实验的只读运行列表和历史参数快照。"""

from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from fastapi import HTTPException
from pydantic import Field
from lab_workflows.workflow_state import read_state

from .schemas import StrictModel


class WorkflowRunItem(StrictModel):
    run_id: str
    timestamp: str
    run_tag: str
    completion_status: str


class WorkflowRuns(StrictModel):
    runs: list[WorkflowRunItem]
    total: int


class WorkflowRunSummary(WorkflowRunItem):
    analysis_status: str
    analysis_error: str
    parameters: dict[str, Any]
    artifacts: dict[str, str] = Field(default_factory=dict)


def read_config(path: Path, *, loader=None) -> dict[str, Any]:
    try:
        config = yaml.load(path.read_text(encoding="utf-8"), Loader=loader or getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except FileNotFoundError as exc:
        raise HTTPException(404, "运行配置不存在") from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(422, "运行配置无法读取") from exc
    if not isinstance(config, dict):
        raise HTTPException(422, "运行配置必须为映射")
    return config


def run_item(path: Path, config: dict[str, Any]) -> dict[str, str]:
    return dict(
        run_id=path.name,
        timestamp=str(config.get("timestamp") or ""),
        run_tag=str(config.get("run_tag") or ""),
        completion_status=str(config.get("completion_status") or "unknown"),
    )


def list_runs(service, limit: int, offset: int, *, config_reader=read_config):
    base = service.root() / "data" / service.adapter.data_type
    items = []
    if base.is_dir():
        for path in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
            if not path.is_dir() or not (path / "experiment_config.yaml").is_file():
                continue
            try:
                path = service.run_directory(path.name, allow_running=True)
                items.append(run_item(path, config_reader(path / "experiment_config.yaml")))
            except HTTPException as exc:
                if exc.status_code not in (404, 422):
                    raise
    return dict(runs=items[offset:offset + limit], total=len(items))


def run_summary(service, run_id: str, *, config_reader=read_config):
    path = service.run_directory(run_id, allow_running=True)
    config = config_reader(path / "experiment_config.yaml")
    parameters = config.get("parameters", {})
    if not isinstance(parameters, dict):
        raise HTTPException(422, "运行参数快照必须为映射")
    try:
        state = read_state(path / "analysis_state.json")
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "分析阶段记录无法读取") from exc
    if state is not None and (not isinstance(state, dict) or not isinstance(state.get("summary", {}), dict)):
        raise HTTPException(422, "分析阶段记录必须为映射")
    summary = (state or {}).get("summary", {})
    analysis_status = summary.get("status") or config.get("analysis_status") or "not_started"
    analysis_error = summary.get("error", "") if summary.get("status") else config.get("analysis_error", "")
    artifacts = {}
    for artifact in sorted((path / "results").glob("*")):
        if not artifact.is_file() or path not in artifact.resolve().parents:
            continue
        name = artifact.relative_to(path).as_posix()
        artifacts[name] = (
            f"/api/experiments/{service.adapter.id}/runs/{quote(run_id, safe='')}/files/"
            f"{quote(name, safe='/')}?v={artifact.stat().st_mtime_ns}"
        )
    return {
        **run_item(path, config),
        "analysis_status": str(analysis_status),
        "analysis_error": str(analysis_error or ""),
        "parameters": parameters,
        "artifacts": artifacts,
    }
