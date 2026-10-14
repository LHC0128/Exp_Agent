"""已知噪声实验复用全局硬件会话服务。"""
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.preparation import preflight
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise import stages
from .workflow_sessions import WorkflowSessionService


class RFKnownNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.session",
            diagram_file="lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/RF 已知噪声灵敏度.excalidraw")
