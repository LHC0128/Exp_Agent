"""Mx Y RF 频率响应的分步会话、独立分析与源图配置。"""
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.definition import ADAPTER
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.preparation import preflight
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis_steps import plain
from lab_workflows.experiment_modules.mx_y_rf_frequency_response import stages, run_format
from lab_workflows.experiment_modules.noise_spectrum_xy.state_io import read_state
from fastapi import HTTPException
from .workflow_history import read_config
from .workflow_sessions import WorkflowSessionService, create_router, conflict


class MxYRFFrequencyResponseSessionService(WorkflowSessionService):
    def __init__(self, root, manager):
        super().__init__(root, manager, adapter=ADAPTER, preflight=preflight, stages=stages,
            analysis_workflow=AnalysisWorkflow,
            session_module="lab_workflows.experiment_modules.mx_y_rf_frequency_response.session",
            diagram_file="lab_workflows/experiment_modules/mx_y_rf_frequency_response/workflow.excalidraw")

    def run_directory(self, run_id, *, allow_running=False):
        path = super().run_directory(run_id, allow_running=True)
        config = read_config(path / "experiment_config.yaml")
        if reason := run_format.format_block_reason(config):
            raise HTTPException(422, reason)
        if not allow_running and (reason := run_format.analysis_block_reason(config)):
            raise conflict(reason, "run_busy")
        return path

    def analysis_state(self, run_id):
        snapshot = super().analysis_state(run_id)
        directory = self.run_directory(run_id)
        report = directory / "results/analysis.json"
        # 无阶段记录的旧报告仅用于展示，不能冒充已完成的分析步骤。
        if not (directory / "analysis_state.json").exists() and report.is_file():
            if directory not in report.resolve().parents:
                raise HTTPException(404, "产物不存在")
            try:
                diagnostics = read_state(report)
            except (OSError, ValueError) as exc:
                raise HTTPException(422, "拟合诊断无法读取") from exc
            if not isinstance(diagnostics, dict):
                raise HTTPException(422, "拟合诊断必须为映射")
            snapshot["summary"] = diagnostics
        return plain(snapshot)
