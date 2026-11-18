"""横向静态场补平（10 kHz 偶部）的薄会话接线；历史读取复用同一格式资格。"""
from lab_workflows.experiment_modules.bperp_null import stages
from lab_workflows.experiment_modules.bperp_null.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.bperp_null.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.bperp_null.preparation import preflight

from .workflow_history import add_history_routes
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router


class BperpNullSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
                         analysis_workflow=AnalysisWorkflow,
                         session_module="lab_workflows.experiment_modules.bperp_null.session",
                         diagram_file="lab_workflows/experiment_modules/bperp_null/workflow.excalidraw")
        self.run_history = DEFINITION.run_history


def create_router(service):
    return add_history_routes(workflow_router(service), service)
