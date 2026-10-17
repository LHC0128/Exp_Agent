"""父运行的离线分阶段分析与采集中的即时趋势编排。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path

import yaml

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_plotting, analysis_steps, run_format
from .analysis_steps import FIELDNAMES, read_summary
from .stages import ANALYSIS, initial_states, invalidate, invalidated_stages, validate_selection

ANALYSIS_VERSION = "mx-y-rf-drift-summary-v1"


def _diagnostics(inputs):
    return {key: inputs[key] for key in ("cycle_count", "valid_cycle_count", "invalid_cycles")}


def refresh_trend(run_dir: Path) -> dict:
    """保留 A09 单路径接口；此在线入口允许未封存父运行。"""
    run_dir = Path(run_dir).resolve()
    inputs = analysis_steps.load_inputs(run_dir, allow_running=True)
    results = run_dir / "results"
    results.mkdir(exist_ok=True)
    analysis_steps.write_table(results, inputs["rows"])
    return analysis_plotting.report(run_dir, inputs["rows"], _diagnostics(inputs), status="online")


class AnalysisWorkflow:
    """分析固定父目录；options 为空，完整与分步共用表格和绘图职责。"""

    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None, *, project_root=None):
        self.run_dir = Path(run_dir).resolve()
        self.root = analysis_steps.project_directory(self.run_dir, project_root)
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = analysis_steps.read_mapping(self.config_path)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        if analysis_parameters:
            raise ValueError("父趋势分析没有覆盖参数：" + "、".join(sorted(analysis_parameters)))
        self.options = {}
        previous = read_state(self.state_path) or {}
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or self.input_fingerprint()
        self._refresh()

    def input_fingerprint(self):
        analysis_steps.project_directory(self.run_dir, self.root)
        config = analysis_steps.read_mapping(self.config_path)
        if reason := run_format.analysis_block_reason(config):
            raise ValueError(reason)
        return analysis_steps.input_fingerprint(self.run_dir, self.root, ANALYSIS_VERSION)

    def _file_signature(self, path):
        signature = analysis_steps.file_signature(analysis_steps.contained(path, self.run_dir))
        return [signature] if signature is not None else None

    def _refresh(self):
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != record.get("files", {}).get(name)
                    for name in stage.artifacts)):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if (any(record["status"] == "stale" for record in self.states.values()) and not any(
                record["status"] in ("failed", "quality_failed", "cancelled") for record in self.states.values())):
            self.summary.update(status="stale", success=False)
        elif not self.summary:
            self.summary.update(status="not_started", success=False)

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options={},
            steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        config = analysis_steps.read_mapping(self.config_path)
        config.update(analysis_status=self.summary.get("status", "not_started"), analysis_error=self.summary.get("error"))
        temporary = self.config_path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(self.config_path)
        report_runtime_progress("analysis", "长飘父趋势分析状态已保存", data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(self.stages, item) for name in stage.artifacts}
        files = [self.results / name for name in sorted(names) if (self.results / name).is_file()]
        if files:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                analysis_steps.contained(path, self.run_dir)
                path.replace(backup / path.name)
            self.summary["backup_directory"] = backup.name

    def _failed(self, item, exc):
        status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
            "quality_failed" if isinstance(exc, ValueError) else "failed")
        invalidate(self.stages, self.states, item)
        self.states[item] = dict(status=status, error=str(exc))
        self.summary.update(status=status, success=False, error=str(exc))
        try:
            self.persist()
        except Exception as save_error:
            if hasattr(exc, "add_note"):
                exc.add_note(f"分析诊断保存另失败：{save_error}")

    def execute(self, selected, expected_revision=None):
        self._refresh()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        # 所有读取资格在归档前验证；损坏引用不能先挪走仍可查看的旧结果。
        try:
            check_cancelled()
            inputs = analysis_steps.load_inputs(self.run_dir, project_root=self.root)
            if self.input_fingerprint() != self.fingerprint:
                raise analysis_steps.AnalysisQualityError("分析输入在读取期间发生变化，请重新执行 B01")
        except BaseException as exc:
            self.revision += 1
            self._failed("B01", exc)
            raise
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        for item in selected:
            try:
                check_cancelled()
                self._archive_from(item)
                invalidate(self.stages, self.states, item)
                self.states[item] = dict(status="running", error="")
                self.summary.update(status="running", success=False)
                self.summary.pop("error", None)
                self.persist()
                if item == "B01":
                    analysis_steps.write_inputs(self.results, inputs, self.fingerprint)
                    self.summary.update(experiment_id=run_format.EXPERIMENT_ID, run_dir=str(self.run_dir),
                        **_diagnostics(inputs), status="partial")
                elif item == "B02":
                    analysis_steps.write_table(self.results, inputs["rows"])
                    self.summary.update(**_diagnostics(inputs), status="partial")
                elif item == "B03":
                    self.summary.update(analysis_plotting.report(self.run_dir,
                        analysis_steps.read_table(self.results), _diagnostics(inputs)))
                stage = next(stage for stage in self.stages if stage.id == item)
                signatures = {name: self._file_signature(self.results / name) for name in stage.artifacts}
                if any(signature is None for signature in signatures.values()):
                    raise RuntimeError(f"{item} 缺少应当生成的分析产物")
                self.states[item] = dict(status="completed", error="", files=signatures,
                    input_fingerprint=deepcopy(self.fingerprint), parameters={}, revision=self.revision)
                self.persist()
            except BaseException as exc:
                self._failed(item, exc)
                raise
        return deepcopy(self.summary)


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None,
        project_root=None) -> dict:
    ctx = AnalysisWorkflow(run_dir, analysis_parameters, project_root=project_root)
    return ctx.execute([stage.id for stage in ANALYSIS] if step_ids is None else step_ids, expected_revision)


def main() -> int:
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    result = analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    print(f"Mx Y RF 灵敏度长飘分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
