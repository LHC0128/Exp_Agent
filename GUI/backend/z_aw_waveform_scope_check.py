"""Z 任意波线圈波形核验的会话与独立分析接口配置。"""
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.definition import ADAPTER
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.preparation import preflight
from lab_workflows.experiment_modules.z_aw_waveform_scope_check.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_aw_waveform_scope_check import stages
from .workflow_sessions import WorkflowSessionService, create_router


class ZAWWaveformScopeCheckSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.z_aw_waveform_scope_check.session",
            diagram_file="lab_workflows/experiment_modules/z_aw_waveform_scope_check/Z任意波线圈波形核验.excalidraw")
