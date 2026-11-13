"""本实验会话薄接线与只读历史；规则及安全恢复均在共享工作流。"""
from lab_workflows.experiment_modules.t2_calibration.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.t2_calibration.workflow import preflight
from lab_workflows.experiment_modules.t2_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.t2_calibration import stages
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router
from .workflow_history import RunHistoryGate, add_history_routes


class T2SessionService(RunHistoryGate, WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow, session_module="lab_workflows.experiment_modules.t2_calibration.session",
            diagram_file="lab_workflows/experiment_modules/t2_calibration/workflow.excalidraw")
        self.run_history = DEFINITION.run_history


def create_router(service):
    return add_history_routes(workflow_router(service), service)
