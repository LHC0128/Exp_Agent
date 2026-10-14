"""已知可控噪声的会话与独立分析接口。"""
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.preparation import preflight
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.noise_spectrum_xy_known_noise import stages
from .workflow_sessions import WorkflowSessionService


class KnownNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.session",
            diagram_file="lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/XY 已知可控噪声谱.excalidraw")
