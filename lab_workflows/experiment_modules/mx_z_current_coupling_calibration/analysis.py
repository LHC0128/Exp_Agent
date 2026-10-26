"""Mx Z 实际电流标定的完整与分阶段离线分析编排。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

import yaml

from ...current_feedback import sha256_file
from ...experiment_runtime import (WorkflowCancelled, check_cancelled,
                                   report_runtime_progress, runtime_run_dir)
from lab_workflows.workflow_state import read_state, write_state
from . import analysis_steps as steps
from .models import MxZCurrentCouplingCalibrationParams
from .run_format import EXPERIMENT_ID
from .stages import ANALYSIS, ANALYSIS_KEYS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states
# 保留已有科学函数的导入路径。
from .analysis_steps import (_analyze_current_curve, _analyze_curve, _builtin,
                             _load_params, _load_scan_index, _weighted_linear_fit)
from .analysis_plotting import _plot_calibration, _plot_frequency_responses

ANALYSIS_VERSION = "mx-z-current-coupling-stages-v1"


class AnalysisWorkflow:
    """绑定一个历史目录，只覆盖已发布的分析阈值。"""

    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.base_params, self.config = _load_params(self.run_dir)
        previous = read_state(self.state_path) or {}
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items()
                             if key in ANALYSIS_KEYS})
        self.states = initial_states(self.stages)
        self.states.update({key: value for key, value in previous.get("steps", {}).items()
                            if key in self.states})
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {"status": "not_started"}))
        self.fingerprint = previous.get("fingerprint")
        self._refresh_validity()
        if analysis_parameters is not None:
            if unknown := analysis_parameters.keys() - set(ANALYSIS_KEYS):
                raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
            changed = any(self.options[key] != value for key, value in analysis_parameters.items())
            self.options.update(analysis_parameters)
            if changed:
                invalidate(self.stages, self.states, "B03")
                self.summary.update(status="stale", error="分析阈值已改变，请显式重算 B03/B04")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= 1 for value in self.options.values()):
            raise ValueError("LINEAR_R_SQUARED_MIN 必须为 0 到 1 之间的有限数值")
        self.params = MxZCurrentCouplingCalibrationParams.from_external(
            self.base_params.to_external() | self.options)

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, sha256_file(path)]

    def input_fingerprint(self):
        self.base_params, self.config = _load_params(self.run_dir)
        config = {key: value for key, value in self.config.items()
                  if key not in ("analysis_status", "analysis_error")}
        return dict(analysis_version=ANALYSIS_VERSION,
            config_sha256=hashlib.sha256(json.dumps(steps.plain(config), sort_keys=True,
                ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")).hexdigest(),
            raw_files={path.relative_to(self.run_dir).as_posix(): self._file_signature(path)
                for path in sorted((self.run_dir / "raw").rglob("*")) if path.is_file()})

    def _parameter_fingerprint(self, item):
        parameters = self.options if item in ("B03", "B04") else {}
        return hashlib.sha256(json.dumps(parameters, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()

    def _refresh_validity(self):
        fingerprint = self.input_fingerprint()
        reason = ""
        if self.fingerprint is not None and self.fingerprint != fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
            reason = "运行参数或原始数据已改变，请从 B01 重算"
        self.fingerprint = fingerprint
        for stage in self.stages:
            record = self.states[stage.id]
            files = record.get("files", {})
            checked = record["status"] == "completed" or (
                record["status"] == "quality_failed" and set(stage.artifacts).issubset(files))
            if record["status"] == "running" or (checked and (
                    record.get("parameter_fingerprint") != self._parameter_fingerprint(stage.id) or any(
                        self._file_signature(self.results / name) != files.get(name)
                        for name in stage.artifacts))):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                reason = f"{stage.id} 分析产物已改变或执行中断，请显式重算"
                break
        if reason:
            self.summary.update(status="stale", error=reason)

    def snapshot(self):
        return steps.plain(dict(run_id=self.run_dir.name, revision=self.revision,
            options=dict(self.options), steps=deepcopy(self.states),
            fingerprint=deepcopy(self.fingerprint), summary=deepcopy(self.summary)))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        write_state(self.results / "analysis_summary.json", steps.plain(self.summary))
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"),
                      analysis_error=self.summary.get("error"))
        temporary = self.config_path.with_suffix(".yaml.analysis.tmp")
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(self.config_path)
        report_runtime_progress("analysis", "电流耦合分析状态已保存",
                                data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(self.stages, item) for name in stage.artifacts}
        paths = [self.results / name for name in sorted(names)
                 if (self.results / name).is_file()]
        if not paths:
            return None
        if (self.results / "analysis_summary.json").is_file():
            paths.append(self.results / "analysis_summary.json")
        backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        backup.mkdir()
        if self.state_path.is_file():
            shutil.copyfile(self.state_path, backup / self.state_path.name)
        for path in paths:
            path.replace(backup / path.name)
        return backup.name

    def execute(self, selected, expected_revision=None):
        self._refresh_validity()
        self.params = MxZCurrentCouplingCalibrationParams.from_external(
            self.base_params.to_external() | self.options)
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        for item in selected:
            check_cancelled()
            self._refresh_validity()
            validate_selection(self.stages, self.states, [item])
            backup = self._archive_from(item)
            invalidate(self.stages, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.update(status="running", options=dict(self.options))
            self.summary.pop("error", None)
            failure = None
            try:
                self.persist()
                if item == "B01":
                    entries = steps.load_inputs(self.run_dir, self.config)
                    write_state(self.results / "analysis_inputs.json", dict(
                        entries=entries, fingerprint=self.fingerprint,
                        scan_index_present=(self.run_dir / "raw/z_scan_index.npz").is_file()))
                    self.summary = dict(experiment_id=EXPERIMENT_ID, run_dir=str(self.run_dir),
                        total_curve_count=len(entries), options=dict(self.options))
                elif item == "B02":
                    self.summary.update(steps.fit_curves(self.run_dir, self.params, self.config))
                elif item == "B03":
                    self.summary.update(steps.evaluate(self.run_dir, self.params, self.config))
                elif item == "B04":
                    payload = steps.write_report(self.run_dir)
                    self.summary.update({key: payload[key] for key in
                        ("success", "K_Z_Hz_per_A", "K_Z_Hz_per_Vsense", "f_0_at_zero_current_hz", "files", "rejection_reasons")})
                stage = next(stage for stage in self.stages if stage.id == item)
                signatures = {name: self._file_signature(self.results / name) for name in stage.artifacts}
                if any(value is None for value in signatures.values()):
                    raise RuntimeError(f"{item} 缺少应当输出的分析产物")
                quality_failed = item == "B04" and not self.summary["success"]
                error = "；".join(self.summary["rejection_reasons"]) if quality_failed else ""
                self.states[item] = dict(status="quality_failed" if quality_failed else "completed",
                    error=error, artifacts=list(signatures), files=signatures,
                    input_fingerprint=deepcopy(self.fingerprint),
                    parameter_fingerprint=self._parameter_fingerprint(item),
                    parameters=dict(self.options) if item in ("B03", "B04") else {}, revision=self.revision)
                self.summary["status"] = "quality_failed" if quality_failed else "completed" if item == "B04" else "partial"
                if backup:
                    self.summary["backup_directory"] = backup
                if quality_failed:
                    self.summary["error"] = error
                    break
            except BaseException as exc:
                failure = exc
                status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
                          "quality_failed" if isinstance(exc, ValueError) else "failed")
                self.states[item] = dict(status=status, error=str(exc), artifacts=[
                    name for stage in self.stages if stage.id == item for name in stage.artifacts
                    if (self.results / name).is_file()])
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return steps.plain(deepcopy(self.summary))


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    summary = ctx.execute([stage.id for stage in ANALYSIS] if step_ids is None else step_ids,
                          expected_revision)
    if step_ids is None:
        return json.loads((ctx.results / "analysis.json").read_text(encoding="utf-8"))
    return summary


def main() -> int:
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
