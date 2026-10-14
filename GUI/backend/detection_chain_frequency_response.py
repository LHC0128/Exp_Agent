"""探测链路频率响应标定的会话与独立分析接口。"""
from lab_workflows.experiment_modules.detection_chain_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.detection_chain_frequency_response.preparation import preflight
from lab_workflows.experiment_modules.detection_chain_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.detection_chain_frequency_response import stages
from .workflow_sessions import WorkflowSessionService, create_router


class DetectionChainFrequencyResponseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.detection_chain_frequency_response.session",
            diagram_file="lab_workflows/experiment_modules/detection_chain_frequency_response/探测链路频率响应标定.excalidraw")
