"""已知不可控 Probe AM 噪声的会话与独立分析接口。"""
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.definition import ADAPTER
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.preparation import preflight
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.noise_spectrum_xy_probe_am import stages
from .workflow_sessions import WorkflowSessionService


class ProbeAMSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.noise_spectrum_xy_probe_am.session",
            diagram_file="lab_workflows/experiment_modules/noise_spectrum_xy_probe_am/XY 已知不可控 Probe AM 噪声谱.excalidraw")
