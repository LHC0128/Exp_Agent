"""Bell Bloom Z 分步会话、独立分析与可编辑流程图。"""
from fastapi import HTTPException
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.preparation import preflight
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.bell_bloom_z_field_calibration import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


class BellBloomZFieldCalibrationSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.bell_bloom_z_field_calibration.session",
            diagram_file="lab_workflows/experiment_modules/bell_bloom_z_field_calibration/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if not allow_running and config.get("completion_status") == "acquired":
            raise conflict("本轮扫描尚未完成安全收尾", "run_busy")
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise HTTPException(422, reason)
        return path
