"""π 脉冲宽度原子域定标的薄会话接线；历史读取复用同一格式资格。"""
from lab_workflows.experiment_modules.pi_width_scan import stages
from lab_workflows.experiment_modules.pi_width_scan.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.pi_width_scan.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.pi_width_scan.preparation import preflight

from .workflow_history import add_history_routes
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router


class PiWidthScanSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
                         analysis_workflow=AnalysisWorkflow,
                         session_module="lab_workflows.experiment_modules.pi_width_scan.session",
                         diagram_file="lab_workflows/experiment_modules/pi_width_scan/workflow.excalidraw")
        self.run_history = DEFINITION.run_history


def create_router(service):
    return add_history_routes(workflow_router(service), service)
