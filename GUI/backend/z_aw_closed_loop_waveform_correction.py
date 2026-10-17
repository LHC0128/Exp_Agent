"""闭环分步会话与只读历史；复用队列、取消、路径和分析互斥。"""
import math
from pathlib import Path
from fastapi import HTTPException, Query
from pydantic import Field
import yaml
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.definition import ADAPTER
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.preparation import preflight
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import stages, run_format
from .workflow_history import WorkflowRunSummary, WorkflowRuns, read_config, list_runs, run_summary
from .workflow_sessions import WorkflowSessionService, create_router as workflow_router, conflict


class ZAWClosedLoopRunSummary(WorkflowRunSummary):
    target_reached: bool
    stop_reason: str
    iteration_count: int
    best_iteration: int | None
    best_relative_rms_error: float | None
    target_relative_rms: float | None
    static_calibration: dict = Field(default_factory=dict)


class ZAWClosedLoopSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.session",
            diagram_file="lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_busy")
        return path

    def summary(self, run_id):
        path = self.run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        source = path / "raw/iteration_summary.yaml"
        if not source.is_file():
            source = path / "results/iteration_summary.yaml"
        try:
            rows = yaml.safe_load(source.read_text(encoding="utf-8")) or []
        except FileNotFoundError:
            rows = []
        except (OSError, yaml.YAMLError) as exc:
            raise HTTPException(422, "闭环轮次汇总无法读取") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise HTTPException(422, "闭环轮次汇总必须为映射列表")
        valid = [row for row in rows if type(row.get("iteration")) is int and
            isinstance(row.get("relative_rms_error"), (int, float)) and math.isfinite(row["relative_rms_error"])]
        best = min(valid, key=lambda row: row["relative_rms_error"]) if valid else {}
        report = path / "results/final_waveform_report.yaml"
        if report.is_file():
            saved = read_config(report)
            if saved.get("final_command_measured"):
                best = {"iteration": saved.get("best_iteration"), "relative_rms_error": saved.get("best_relative_rms_error")}
        threshold = config["parameters"].get("TARGET_RELATIVE_RMS")
        score = best.get("relative_rms_error")
        base = run_summary(self, run_id)
        base["artifacts"] = {Path(name).name: url for name, url in base["artifacts"].items()}
        return base | dict(run_tag=str(config["parameters"].get("RUN_TAG") or config.get("run_tag") or ""),
            target_reached=bool(config.get("target_reached")), stop_reason=str(config.get("stop_reason") or ""),
            iteration_count=len(rows), best_iteration=best.get("iteration"),
            best_relative_rms_error=float(score) if isinstance(score, (int, float)) and math.isfinite(score) else None,
            target_relative_rms=float(threshold) if isinstance(threshold, (int, float)) and math.isfinite(threshold) else None,
            static_calibration=config.get("static_calibration") if isinstance(config.get("static_calibration"), dict) else {})


def create_router(service):
    router = workflow_router(service)

    @router.get("/runs", response_model=WorkflowRuns)
    def runs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        return list_runs(service, limit, offset)

    @router.get("/runs/{run_id}/summary", response_model=ZAWClosedLoopRunSummary, name="experiment_run_summary")
    def summary(run_id: str):
        return service.summary(run_id)

    return router
