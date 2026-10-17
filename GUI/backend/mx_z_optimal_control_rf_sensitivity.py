"""Mx Z 灵敏度分步会话、历史与只读科学诊断。"""
from fastapi import HTTPException
import json
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.preparation import preflight
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.analysis_steps import plain
from lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict


class MxZOptimalControlRFSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.session",
            diagram_file="lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_unavailable")
        return path


def create_router(service, *, include_history=True):
    router = workflow_router(service, include_history=include_history)

    @router.get("/runs/{run_id}/result")
    def result(run_id: str):
        directory = service.run_directory(run_id, allow_running=True)
        state_path = directory / "analysis_state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("summary", {}).get("status") not in (None, "completed"):
                    return plain(state["summary"])
            except (ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(422, "分析阶段记录无法读取") from exc
        path = directory / "results/analysis.yaml"
        if not path.is_file():
            return {"status": "not_started"}
        return plain(read_config(path))

    return router
