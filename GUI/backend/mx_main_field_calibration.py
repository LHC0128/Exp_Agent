"""Mx 主磁场标定 的分步会话、独立分析与源图配置。"""
from lab_workflows.experiment_modules.mx_main_field_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_main_field_calibration.preparation import preflight
from lab_workflows.experiment_modules.mx_main_field_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_main_field_calibration import stages
from .workflow_sessions import WorkflowSessionService, create_router


class MxMainFieldCalibrationSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_main_field_calibration.session",
            diagram_file="lab_workflows/experiment_modules/mx_main_field_calibration/workflow.excalidraw")
