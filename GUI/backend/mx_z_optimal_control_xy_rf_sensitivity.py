"""Mx Z 灵敏度分步会话、历史与只读科学诊断。"""
from fastapi import HTTPException
from fastapi import Query
from urllib.parse import quote
import json
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.preparation import preflight
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.analysis_steps import plain
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity import stages, run_format
from .workflow_history import read_config, list_runs, run_summary, WorkflowRuns, WorkflowRunSummary
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict


class MxZOptimalControlXYRFSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_unavailable")
        return path


def create_router(service, *, include_history=True):
    router = workflow_router(service, include_history=False)

    if include_history:
        @router.get("/runs", response_model=WorkflowRuns)
        def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
            return list_runs(service, limit, offset)

        @router.get("/runs/{run_id}/summary", response_model=WorkflowRunSummary)
        def summary(run_id: str):
            value = run_summary(service, run_id)
            directory = service.run_directory(run_id, allow_running=True)
            for path in sorted((directory / "results/points").rglob("*")):
                if path.is_file() and directory in path.resolve().parents:
                    name = path.relative_to(directory).as_posix()
                    value["artifacts"][name] = (f"/api/experiments/{service.adapter.id}/runs/"
                        f"{quote(run_id, safe='')}/files/{quote(name, safe='/')}?v={path.stat().st_mtime_ns}")
            return value

    @router.get("/runs/{run_id}/result")
    def result(run_id: str):
        directory = service.run_directory(run_id, allow_running=True)
        state_path = directory / "analysis_state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("summary", {}).get("status") not in (None, "completed"):
                    return plain(state["summary"])
            except (ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(422, "分析阶段记录无法读取") from exc
        path = directory / "results/analysis.yaml"
        if not path.is_file():
            return {"status": "not_started"}
        return plain(read_config(path))

    return router
