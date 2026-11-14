"""XY 偏置噪声谱会话，复用通用队列、历史及只读结果路由。"""
from fastapi import HTTPException
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.preparation import preflight
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, conflict
from .mx_z_optimal_control_xy_rf_sensitivity import create_router


class MxZOptimalControlXYNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.session",
            diagram_file="lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_unavailable")
        return path
