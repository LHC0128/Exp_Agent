"""光功率优化复用全局队列、会话与历史接口，不复制实验规则。"""
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.preparation import preflight
from lab_workflows.experiment_modules.mx_y_rf_power_optimization.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import stages
from lab_workflows.experiment_modules.mx_y_rf_power_optimization import run_format
from fastapi import HTTPException
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


class MxYRFPowerOptimizationSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_rf_power_optimization.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_busy")
        return path
