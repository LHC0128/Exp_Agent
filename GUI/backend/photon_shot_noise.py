"""光子散粒噪声的薄会话接线与只读历史参数迁移。"""
from fastapi import HTTPException, Query
from lab_workflows.experiment_modules.photon_shot_noise.definition import ADAPTER
from lab_workflows.experiment_modules.photon_shot_noise.preparation import preflight
from lab_workflows.experiment_modules.photon_shot_noise.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.photon_shot_noise import stages, run_format
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router
from .workflow_history import read_config, list_runs, run_summary, WorkflowRuns, WorkflowRunSummary


class PhotonShotNoiseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.photon_shot_noise.session",
            diagram_file="lab_workflows/experiment_modules/photon_shot_noise/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        reason = run_format.format_block_reason(config) if allow_running else run_format.analysis_block_reason(config)
        if reason:
            raise HTTPException(422, reason)
        return path


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
            value["parameters"] = run_format.parameters(config).to_external()
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"历史参数迁移失败：{exc}") from exc
        return value

    return router
