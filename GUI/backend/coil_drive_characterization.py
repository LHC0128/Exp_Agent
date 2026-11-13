"""线圈驱动链路电学特性的薄会话接线；历史读取复用同一格式资格。"""
from fastapi import HTTPException, Query

from lab_workflows.common import data_root
from lab_workflows.experiment_modules.coil_drive_characterization import run_format, stages
from lab_workflows.experiment_modules.coil_drive_characterization.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.coil_drive_characterization.definition import ADAPTER
from lab_workflows.experiment_modules.coil_drive_characterization.preparation import preflight

from .workflow_history import WorkflowRunSummary, WorkflowRuns, list_runs, read_config, run_summary
from .workflow_sessions import WorkflowSessionService, conflict, create_router as workflow_router


class CoilDriveSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
                         analysis_workflow=AnalysisWorkflow,
                         session_module="lab_workflows.experiment_modules.coil_drive_characterization.session",
                         diagram_file="lab_workflows/experiment_modules/coil_drive_characterization/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        base = (data_root(self.root()) / self.adapter.data_type).resolve()
        path = (base / run_id).resolve()
        if path.parent != base or not path.is_dir():
            raise HTTPException(404, "运行目录不存在")
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and config.get("completion_status") == "running":
            raise conflict("测量模块仍在写入数据", "run_busy")
        return path


def create_router(service):
    router = workflow_router(service)

    @router.get("/runs", response_model=WorkflowRuns)
    def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        return list_runs(service, limit, offset)

    @router.get("/runs/{run_id}/summary", response_model=WorkflowRunSummary)
    def summary(run_id: str):
        value = run_summary(service, run_id)
        config = read_config(service.run_directory(run_id, allow_running=True) / "experiment_config.yaml")
        value["parameters"] = run_format.recorded_parameters(config)
        return value

    return router
