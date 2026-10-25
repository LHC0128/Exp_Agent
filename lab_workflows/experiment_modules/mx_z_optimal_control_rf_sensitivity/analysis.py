"""Mx Z 的原灵敏度/剩磁分析、内容指纹与分阶段编排。"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml
from ...steps.run_directory import write_run_config

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_steps, run_format
from .analysis_steps import AnalysisQualityError, plain
from ..mx_y_rf_sensitivity.analysis_plotting import _load_params
from .models import MxZOptimalControlRFParams
from .stages import ANALYSIS, ANALYSIS_KEYS, initial_states, invalidate, invalidated_stages, validate_selection

ANALYSIS_VERSION = "mx-z-optimal-control-sensitivity-v1"
EXPERIMENT_ID = run_format.EXPERIMENT_ID


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.config = _load_params(self.run_dir, MxZOptimalControlRFParams)[1]
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {}
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        self.refresh_products()
        if analysis_parameters:
            raise ValueError("本实验没有可调分析覆盖参数")

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return [path.stat().st_size, digest.hexdigest()]

    def input_fingerprint(self):
        config = {key: value for key, value in self.config.items()
            if key not in ("analysis_status", "analysis_error")}
        return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True,
            ensure_ascii=False, default=str).encode()).hexdigest(), analysis_version=ANALYSIS_VERSION,
            raw_files={path.relative_to(self.run_dir).as_posix(): self._file_signature(path)
                for path in [*sorted((self.run_dir / "raw").rglob("*")), self.results / "phase_calibration.yaml"] if path.is_file()})

    def refresh_products(self):
        for stage in self.stages:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != signature for name, signature in record.get("files", {}).items())):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
            steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, plain(self.snapshot()))
        # 只更新分析状态，不把历史目录的分析覆盖值写入采集快照。
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"), analysis_error=self.summary.get("error"))
        write_run_config(self.config_path, config)
        report_runtime_progress("analysis", "离线分析状态已保存", data={"analysis_workflow": plain(self.snapshot())})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(self.stages, item) for name in stage.artifacts}
        files = [self.results / name for name in names if (self.results / name).is_file()]
        if files:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                path.replace(backup / path.name)

    def execute(self, selected, expected_revision=None):
        self.config = _load_params(self.run_dir, MxZOptimalControlRFParams)[1]
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        fingerprint = self.input_fingerprint()
        if fingerprint != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.fingerprint = fingerprint
            self.revision += 1
        self.refresh_products()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary = {"status": "running", "options": dict(self.options)}
            self.persist()
            failure = None
            try:
                # 每次重算先验收当前输入，坏数据不能移动上次完整科学产物。
                analysis_steps.load_inputs(self.run_dir)
                self._archive_from(item)
                if item == "B01":
                    pass
                elif item == "B02":
                    self.summary.update(analysis_steps.evaluate(self.run_dir))
                elif item == "B03":
                    self.summary.update(plain(analysis_steps.render_report(self.run_dir)))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = [name for name in stage.artifacts if (self.results / name).is_file()]
                required = {"analysis.yaml", "analysis.json", "phase_calibration.png"} if item == "B03" else set(stage.artifacts)
                if item == "B03" and self.config.get("measurement_mode") != "residual_control_phase_scan":
                    required.update(("response_fit.npz", "noise_psd.npz", "sensitivity.npz", "amplitude_response.png", "full_analysis.png"))
                if missing := required - set(files):
                    raise RuntimeError("分析阶段缺少产物：" + "、".join(sorted(missing)))
                self.states[item] = {"status": "completed", "error": "", "artifacts": files,
                    "files": {name: self._file_signature(self.results / name) for name in files},
                    "input_fingerprint": deepcopy(self.fingerprint), "parameters": dict(self.options),
                    "parameter_fingerprint": hashlib.sha256(json.dumps(self.options, sort_keys=True).encode()).hexdigest(),
                    "revision": self.revision}
                self.summary["status"] = "completed" if item == "B03" else "partial"
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else (
                    "quality_failed" if isinstance(exc, AnalysisQualityError) else "failed")
                self.states[item] = {"status": status, "error": str(exc)}
                stage = next(stage for stage in self.stages if stage.id == item)
                files = [name for name in stage.artifacts if (self.results / name).is_file()]
                self.states[item].update(artifacts=files,
                    files={name: self._file_signature(self.results / name) for name in files},
                    input_fingerprint=deepcopy(self.fingerprint), parameters={},
                    parameter_fingerprint=hashlib.sha256(b"{}").hexdigest(), revision=self.revision)
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


def analyze(run_dir):
    ctx = AnalysisWorkflow(run_dir)
    return ctx.execute([stage.id for stage in ctx.stages])


def main() -> int:
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        result = analyze(runtime_run_dir())
        print(f"Mx Z 最优控制 RF 灵敏度分析完成: {result.get('run_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
