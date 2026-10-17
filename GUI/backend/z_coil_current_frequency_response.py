"""Z 线圈实际电流频响的会话、独立分析与历史配置。"""
from lab_workflows.experiment_modules.z_coil_current_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.z_coil_current_frequency_response.workflow import preflight
from lab_workflows.experiment_modules.z_coil_current_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_coil_current_frequency_response import stages
from .workflow_sessions import WorkflowSessionService, create_router


class ZCoilCurrentFrequencyResponseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.z_coil_current_frequency_response.session",
            diagram_file="lab_workflows/experiment_modules/z_coil_current_frequency_response/workflow.excalidraw")
