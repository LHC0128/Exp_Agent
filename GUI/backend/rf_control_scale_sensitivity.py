"""复用全局互斥、会话、历史、独立分析和安全文件路由。"""
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.preparation import preflight
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_control_scale_sensitivity import stages
from fastapi import HTTPException
from lab_workflows.experiment_modules.rf_control_scale_sensitivity.run_format import format_block_reason
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService


class RFControlScaleSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.rf_control_scale_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/rf_control_scale_sensitivity/RF Control Scale.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        if reason := format_block_reason(read_config(path / "experiment_config.yaml")):
            raise HTTPException(422, reason)
        return path
