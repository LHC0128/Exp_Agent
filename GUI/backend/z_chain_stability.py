"""Z 链路零点与脉冲角稳定性的薄会话接线；历史读取复用同一格式资格。"""
from lab_workflows.experiment_modules.z_chain_stability import stages
from lab_workflows.experiment_modules.z_chain_stability.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_chain_stability.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.z_chain_stability.preparation import preflight

from .workflow_history import add_history_routes
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router


class ZChainStabilitySessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
                         analysis_workflow=AnalysisWorkflow,
                         session_module="lab_workflows.experiment_modules.z_chain_stability.session",
                         diagram_file="lab_workflows/experiment_modules/z_chain_stability/workflow.excalidraw")
        self.run_history = DEFINITION.run_history


def create_router(service):
    return add_history_routes(workflow_router(service), service)
