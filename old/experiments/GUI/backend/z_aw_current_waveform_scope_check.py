"""实际电流验证复用硬件队列、会话协议和只读历史接口。"""
from fastapi import HTTPException

from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.definition import ADAPTER
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.preparation import preflight
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_aw_current_waveform_scope_check import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


class ZAWCurrentWaveformScopeCheckSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.session",
            diagram_file="lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_unavailable")
        return path
