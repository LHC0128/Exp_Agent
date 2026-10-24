"""主场示波器噪声谱的分步会话、独立分析与历史接线。"""
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.definition import ADAPTER
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.preparation import preflight
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum import stages
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.models import MxMainFieldScopeNoiseSpectrumParams
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router
from .workflow_history import WorkflowRuns, WorkflowRunSummary, list_runs, run_summary, read_config


class MxMainFieldScopeNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.session",
            diagram_file="lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/workflow.excalidraw")


def create_router(service):
    router = workflow_router(service)

    @router.get("/runs", response_model=WorkflowRuns)
    def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        return list_runs(service, limit, offset)

    @router.get("/runs/{run_id}/summary", response_model=WorkflowRunSummary)
    def summary(run_id: str):
        value = run_summary(service, run_id)
        config = read_config(service.run_directory(run_id, allow_running=True) / "experiment_config.yaml")
        try:
            # GUI 详情/填回复用模型迁移，不继承当前 YAML，也不改写历史快照。
            value["parameters"] = MxMainFieldScopeNoiseSpectrumParams.from_external(
                config["parameters"], schema_version=int(config.get("schema_version", 1))).to_external()
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value

    return router
