"""局部优化分步会话、只读历史及旧 RAMP 批次摘要。"""
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Query
from pydantic import Field
import yaml

from lab_workflows.experiment_modules.noise_spectrum_xy.state_io import read_state
from lab_workflows.experiment_modules.static_sensitivity_optimize_local import run_format, stages
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.analysis_steps import plain
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.definition import ADAPTER
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.models import StaticSensitivityOptimizeLocalParams
from lab_workflows.experiment_modules.static_sensitivity_optimize_local.preparation import preflight
from .workflow_history import WorkflowRuns, WorkflowRunSummary, run_item
from .workflow_sessions import WorkflowSessionService, conflict, create_router as workflow_router


class LocalOptimizationRunSummary(WorkflowRunSummary):
    optimization_summary: dict[str, Any] = Field(default_factory=dict)


class StaticLocalSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.static_sensitivity_optimize_local.session",
            diagram_file="lab_workflows/experiment_modules/static_sensitivity_optimize_local/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        if not run_id or any(char in run_id for char in '/\\:') or run_id in (".", ".."):
            raise HTTPException(404, "运行目录不存在")
        base = (self.root() / "data" / run_format.DATA_TYPE).resolve()
        path = (base / run_id).resolve()
        if path.parent != base or not path.is_dir():
            raise HTTPException(404, "运行目录不存在")
        config = run_format.read_config(path)
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and config.get("completion_status") in ("running", "acquired"):
            raise conflict("批次仍在写入，请先安全封存或结束会话", "run_busy")
        return path

    def runs(self, limit, offset):
        base = self.root() / "data" / run_format.DATA_TYPE
        rows = []
        for path in sorted(base.iterdir(), reverse=True) if base.is_dir() else []:
            try:
                path = self.run_directory(path.name, allow_running=True)
                rows.append(run_item(path, run_format.read_config(path)))
            except HTTPException as exc:
                if exc.status_code not in (404, 422):
                    raise
        return dict(runs=rows[offset:offset + limit], total=len(rows))

    def summary(self, run_id):
        path = self.run_directory(run_id, allow_running=True)
        config = run_format.read_config(path)
        try:
            state = read_state(path / "analysis_state.json") or {}
            if not isinstance(state, dict) or not isinstance(state.get("summary", {}), dict):
                raise ValueError("分析状态必须为映射")
            analysis = state.get("summary") or read_state(path / "results/analysis.json") or {}
            if config["raw_format_version"] == 0 and not analysis:
                legacy = yaml.safe_load((path / run_format.LEGACY_SUMMARY).read_text(encoding="utf-8"))
                analysis = dict(legacy["decision"], status="completed", point_count=len(legacy["results"]), source="legacy_existing_summary")
            parameters = StaticSensitivityOptimizeLocalParams.migrate_external(config["parameters"], 1)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            raise HTTPException(422, str(exc)) from exc
        artifacts = {}
        for artifact in [*sorted((path / "results").glob("*")), *sorted(path.glob("optimization_local_summary.*"))]:
            if artifact.is_file() and path in artifact.resolve().parents:
                name = artifact.relative_to(path).as_posix()
                artifacts[name] = f"/api/experiments/{ADAPTER.id}/runs/{quote(run_id, safe='')}/files/{quote(name, safe='/')}?v={artifact.stat().st_mtime_ns}"
        return dict(**run_item(path, config), parameters=parameters, artifacts=artifacts,
            analysis_status=str(analysis.get("status") or config.get("analysis_status") or "not_started"),
            analysis_error=str(analysis.get("error") or config.get("analysis_error") or ""),
            optimization_summary=plain(analysis))


def create_router(service, *, include_history=True):
    router = workflow_router(service)
    if include_history:
        @router.get("/runs", response_model=WorkflowRuns)
        def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
            return service.runs(limit, offset)

        @router.get("/runs/{run_id}/summary", response_model=LocalOptimizationRunSummary)
        def summary(run_id: str):
            return service.summary(run_id)
    return router
