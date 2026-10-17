"""Mx Y 最优控制 RF 频率响应的分步会话、独立分析与源图配置。"""
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.preparation import preflight
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response import stages, run_format
from fastapi import HTTPException
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


class MxYOptimalControlFrequencySessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_busy")
        return path
