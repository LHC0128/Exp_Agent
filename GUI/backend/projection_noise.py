"""投影噪声分步会话、独立分析与只读历史接线。"""
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.projection_noise.definition import ADAPTER
from lab_workflows.experiment_modules.projection_noise.preparation import preflight
from lab_workflows.experiment_modules.projection_noise.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.projection_noise import stages, run_format, analysis_steps
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router
from .workflow_history import WorkflowRuns, WorkflowRunSummary, list_runs, run_summary, read_config


class ProjectionNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.projection_noise.session",
            diagram_file="lab_workflows/experiment_modules/projection_noise/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        return path


def create_router(service):
    router = workflow_router(service)

    @router.get("/runs", response_model=WorkflowRuns)
    def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        return list_runs(service, limit, offset)

    @router.get("/runs/{run_id}/summary", response_model=WorkflowRunSummary)
    def summary(run_id: str):
        value = run_summary(service, run_id)
        path = service.run_directory(run_id, allow_running=True)
        if not analysis_steps.is_legacy(path):
            config = read_config(path / "experiment_config.yaml")
            try:
                value["parameters"] = analysis_steps.parameters_from_config(config).to_external()
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value

    return router
