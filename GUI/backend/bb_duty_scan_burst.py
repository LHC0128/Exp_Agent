"""BB 占空比的薄会话接线；历史读取复用同一格式资格和参数迁移。"""
from functools import partial
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.bb_duty_scan_burst.definition import ADAPTER
from lab_workflows.experiment_modules.bb_duty_scan_burst.preparation import preflight
from lab_workflows.experiment_modules.bb_duty_scan_burst.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.bb_duty_scan_burst import stages, run_format
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict
from .workflow_history import read_config, list_runs, run_summary, WorkflowRuns, WorkflowRunSummary


read_bb_config = partial(read_config, loader=run_format.ConfigLoader)


class BBDutySessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.bb_duty_scan_burst.session",
            diagram_file="lab_workflows/experiment_modules/bb_duty_scan_burst/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        base = (self.root() / "data" / self.adapter.data_type).resolve()
        path = (base / run_id).resolve()
        if path.parent != base or not path.is_dir():
            raise HTTPException(404, "运行目录不存在")
        config = read_bb_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and config.get("completion_status") in ("running", "acquired"):
            raise conflict("本轮采集仍在写入数据", "run_busy")
        return path


def create_router(service):
    router = workflow_router(service)

    @router.get("/runs", response_model=WorkflowRuns)
    def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        return list_runs(service, limit, offset, config_reader=read_bb_config)

    @router.get("/runs/{run_id}/summary", response_model=WorkflowRunSummary)
    def summary(run_id: str):
        value = run_summary(service, run_id, config_reader=read_bb_config)
        config = read_bb_config(service.run_directory(run_id, allow_running=True) / "experiment_config.yaml")
        try:
            value["parameters"] = run_format.recorded_parameters(config)
            if value["analysis_status"] == "not_started" and any(name.endswith("analysis_pub.json") for name in value["artifacts"]):
                value["analysis_status"] = "已有历史分析产物（尚无分步记录）"
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value

    return router
