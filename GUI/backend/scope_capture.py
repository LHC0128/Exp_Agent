"""示波器采集的会话与独立分析接口配置。"""
from fastapi import HTTPException
from lab_workflows.experiment_modules.scope_capture.definition import ADAPTER
from lab_workflows.experiment_modules.scope_capture.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.scope_capture import stages, workflow, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


def preflight(values, root):
    """会话服务契约：返回以参数对象开头的元组；复用工作流的映射条件检查。"""
    params = ADAPTER._resolved(values) if isinstance(values, dict) else values
    return workflow.preflight(params, root)


class ScopeCaptureSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.scope_capture.session",
            diagram_file="lab_workflows/experiment_modules/scope_capture/示波器采集.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and config.get("completion_status") == "running":
            raise conflict("本轮扫描仍在写入数据", "run_busy")
        return path
