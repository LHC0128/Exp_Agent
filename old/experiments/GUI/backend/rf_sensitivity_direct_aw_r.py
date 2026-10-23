"""R 标定实验复用全局会话服务，按独立 ID 隔离状态。"""
from functools import partial
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.definition import ADAPTER
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r import stages
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.preparation import preflight
from .workflow_sessions import WorkflowSessionService


class RFRSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=partial(preflight, experiment_id=ADAPTER.id),
            stages=stages, analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.session",
            diagram_file="lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/RF 灵敏度（DirectAW R）.excalidraw")
