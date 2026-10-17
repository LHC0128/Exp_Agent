"""Mx Y RF 的分步会话、独立分析与源图配置。"""
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.preparation import preflight
from lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_sensitivity import stages
from .workflow_sessions import WorkflowSessionService, create_router


class MxYRFSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_rf_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.excalidraw")
