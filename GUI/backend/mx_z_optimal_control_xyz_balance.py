"""Mx Z 最优控制 XYZ 平衡场会话；复用队列、历史与只读科学诊断。"""
from fastapi import HTTPException
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.preparation import preflight
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, conflict
from .mx_z_optimal_control_xy_rf_sensitivity import create_router


class MxZOptimalControlXYZBalanceSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.session",
            diagram_file="lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_unavailable")
        return path
