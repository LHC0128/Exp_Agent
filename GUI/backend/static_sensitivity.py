"""静磁场分步会话和独立分析接入公共服务。"""
from lab_workflows.experiment_modules.static_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.static_sensitivity.preparation import preflight
from lab_workflows.experiment_modules.static_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.static_sensitivity import stages
from .workflow_sessions import WorkflowSessionService, create_router


class StaticSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.static_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/static_sensitivity/静磁场灵敏度.excalidraw")
