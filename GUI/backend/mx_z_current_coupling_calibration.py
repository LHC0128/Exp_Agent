"""Mx Z 实际电流标定的分步会话、独立分析和原图配置。"""
import json
from typing import Any

from fastapi import HTTPException
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.definition import ADAPTER
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.preparation import preflight
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis_steps import plain
from lab_workflows.experiment_modules.mx_z_current_coupling_calibration import stages, run_format
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict


class MxZCurrentCouplingCalibrationSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_z_current_coupling_calibration.session",
            diagram_file="lab_workflows/experiment_modules/mx_z_current_coupling_calibration/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=allow_running)
        config = read_config(path / "experiment_config.yaml")
        if not allow_running and config.get("completion_status") == "acquired":
            raise conflict("本轮扫描尚未完成安全收尾", "run_busy")
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise HTTPException(422, reason)
        return path

    def result(self, run_id):
        directory = self.run_directory(run_id, allow_running=True)
        config = read_config(directory / "experiment_config.yaml")
        if (config.get("experiment_id") != self.adapter.id
                or config.get("data_type") not in (None, self.adapter.data_type)):
            raise HTTPException(422, "运行目录与实验不匹配")
        path = (directory / "results/analysis.json").resolve()
        if directory not in path.parents or not path.is_file():
            raise HTTPException(404, "标定结果不存在")
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(422, "标定结果无法读取") from exc
        if not isinstance(result, dict):
            raise HTTPException(422, "标定结果必须为映射")
        return plain(result)


def create_router(service, *, include_history=False):
    router = workflow_router(service, include_history=include_history)

    @router.get("/runs/{run_id}/result", response_model=dict[str, Any])
    def result(run_id: str):
        return service.result(run_id)

    return router
