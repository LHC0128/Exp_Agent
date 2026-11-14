"""XY 剩磁校准的串行会话、独立分析与只读历史接口。"""
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.preparation import preflight
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration.models import MxXYResidualFieldCalibrationParams
from lab_workflows.experiment_modules.mx_xy_residual_field_calibration import stages, run_format
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict
from .workflow_history import WorkflowRuns, WorkflowRunSummary, list_runs, run_summary, read_config


class MxXYResidualFieldCalibrationSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_xy_residual_field_calibration.session",
            diagram_file="lab_workflows/experiment_modules/mx_xy_residual_field_calibration/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if not allow_running and config.get("completion_status") == "acquired":
            raise conflict("本轮扫描尚未完成安全收尾", "run_busy")
        qualification = config | ({"completion_status": None} if allow_running else {})
        if reason := run_format.analysis_block_reason(qualification):
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
        config = read_config(service.run_directory(run_id, allow_running=True) / "experiment_config.yaml")
        try:
            value["parameters"] = MxXYResidualFieldCalibrationParams.from_external(config["parameters"],
                schema_version=int(config.get("schema_version", 1))).to_external()
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value

    return router
