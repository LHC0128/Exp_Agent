"""Probe/PZT 分步会话、独立分析、历史与原图接口。"""
from fastapi import HTTPException
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.preparation import preflight
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService


class MxYRFProbeDetuningSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if allow_running:
            config = config | {"completion_status": "completed"}
        if reason := run_format.analysis_block_reason(config):
            raise HTTPException(422, reason)
        return path
