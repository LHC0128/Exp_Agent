"""RF DirectAW 的分步会话与离线分析配置。"""
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.preparation import preflight
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw import stages
from .workflow_sessions import WorkflowSessionService, create_router


class RFSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.rf_sensitivity_direct_aw.session",
            diagram_file="lab_workflows/experiment_modules/rf_sensitivity_direct_aw/RF 灵敏度（DirectAW）.excalidraw")
