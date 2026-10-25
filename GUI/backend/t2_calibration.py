"""本实验会话薄接线与只读历史；规则及安全恢复均在共享工作流。"""
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.t2_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.t2_calibration.workflow import preflight
from lab_workflows.experiment_modules.t2_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.t2_calibration import stages, run_format
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router
from .workflow_history import WorkflowRuns, WorkflowRunSummary, read_config, list_runs, run_summary


class T2SessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow, session_module="lab_workflows.experiment_modules.t2_calibration.session",
            diagram_file="lab_workflows/experiment_modules/t2_calibration/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        reason = run_format.format_block_reason(config) if allow_running else run_format.analysis_block_reason(config)
        if reason:
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
        try:
            value["parameters"] = run_format.historical_parameters(read_config(path / "experiment_config.yaml"))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value
    return router
