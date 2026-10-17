"""长飘分步会话及只读、兼容旧 NaN 的父趋势摘要。"""
from typing import Any

from fastapi import HTTPException, Query
from pydantic import Field
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.preparation import preflight
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.analysis_steps import read_summary
from lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift import stages, run_format
from .workflow_history import WorkflowRuns, WorkflowRunSummary, list_runs, read_config, run_summary
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict


class MxYRFDriftRunSummary(WorkflowRunSummary):
    drift_summary: dict[str, Any] = Field(default_factory=dict)


class MxYRFDriftSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and config.get("completion_status") in ("running", "acquired"):
            raise conflict("父采集仍在执行，请先完成安全收尾", "run_busy")
        return path

    def summary(self, run_id):
        summary = run_summary(self, run_id)
        try:
            summary["drift_summary"] = read_summary(
                self.run_directory(run_id, allow_running=True), project_root=self.root())
        except (OSError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return summary


def create_router(service, *, include_history=False):
    router = workflow_router(service)
    if include_history:
        @router.get("/runs", response_model=WorkflowRuns)
        def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
            return list_runs(service, limit, offset)

        @router.get("/runs/{run_id}/summary", response_model=MxYRFDriftRunSummary)
        def summary(run_id: str):
            return service.summary(run_id)

    return router
