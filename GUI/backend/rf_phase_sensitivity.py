"""射频相位灵敏度的会话接线；历史、产物和分析使用同一运行格式资格。"""

from lab_workflows.experiment_modules.rf_phase_sensitivity import stages
from lab_workflows.experiment_modules.rf_phase_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_phase_sensitivity.definition import ADAPTER, DEFINITION
from lab_workflows.experiment_modules.rf_phase_sensitivity.preparation import preflight

from .workflow_history import RunHistoryGate, add_history_routes
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router


class RfPhaseSensitivitySessionService(RunHistoryGate, WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(
            root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.rf_phase_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/rf_phase_sensitivity/workflow.excalidraw",
        )
        self.run_history = DEFINITION.run_history


def create_router(service):
    return add_history_routes(workflow_router(service), service)
