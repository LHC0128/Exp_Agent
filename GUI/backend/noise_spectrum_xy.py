"""XY 正弦实验的会话配置。"""
from lab_workflows.experiment_modules.noise_spectrum_xy.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy.preparation import preflight
from lab_workflows.experiment_modules.noise_spectrum_xy.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.noise_spectrum_xy import stages
from .workflow_sessions import WorkflowSessionService, create_router


class XYSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.noise_spectrum_xy.session",
            diagram_file="lab_workflows/experiment_modules/noise_spectrum_xy/XY 控制测量噪声谱（正弦）.excalidraw")
