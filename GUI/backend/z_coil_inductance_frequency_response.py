"""Z 线圈电感频响的会话、独立分析与历史接口配置。"""
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.preparation import preflight
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_coil_inductance_frequency_response import stages
from .workflow_sessions import WorkflowSessionService, create_router


class ZCoilInductanceFrequencyResponseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.z_coil_inductance_frequency_response.session",
            diagram_file="lab_workflows/experiment_modules/z_coil_inductance_frequency_response/workflow.excalidraw")
