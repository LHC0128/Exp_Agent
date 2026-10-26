"""Mx Y RF 频率响应离线分析：完整与分步共用编排。

只读取 run_dir/raw/frequency_response.npz 与实验配置，用统一布洛赫稳态
线形拟合每条幅度曲线，报告弱驱动 HWHM 或强驱动 Rabi 劈裂双峰，并输出
频率响应叠加图与饱和度-幅度平方诊断图。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from ...steps.run_directory import write_run_config

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from lab_workflows.workflow_state import read_state, write_state
from . import analysis_steps, run_format
from .analysis_steps import AnalysisQualityError, plain
from .models import MxYRFFrequencyResponseParams
from .stages import ANALYSIS, ANALYSIS_KEYS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states

ANALYSIS_VERSION = "mx-y-rf-frequency-response-v1"


# 旧绘图与参数加载导出仍可导入，完整和分步分析只编排职责模块。
from .analysis_plotting import _plot_frequency_response, _plot_saturation_scaling
from .analysis_steps import load_params as _load_params


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None, *, params_type=MxYRFFrequencyResponseParams):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.base_params, self.config = _load_params(self.run_dir, params_type)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items() if key in ANALYSIS_KEYS})
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.input_fingerprint()
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        self.refresh_products()
        if analysis_parameters:
            if unknown := analysis_parameters.keys() - set(ANALYSIS_KEYS):
                raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
            if any(self.options[key] != value for key, value in analysis_parameters.items()):
                invalidate(self.stages, self.states, "B02")
                self.options.update(analysis_parameters)
        self.params = params_type.from_external(self.base_params.to_external() | self.options)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in self.options.values()):
            raise ValueError("分析覆盖参数必须为有限数值")
        # 离线分析仅复用模型的分析字段范围，不对历史采集重新执行当前硬件预检。
        for item in fields(self.params):
            key = item.metadata.get("external_name", item.name)
            if key not in ANALYSIS_KEYS:
                continue
            value = getattr(self.params, item.name)
            minimum, maximum = item.metadata.get("minimum"), item.metadata.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{key} 低于模型下限 {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{key} 高于模型上限 {maximum}")

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
                for path in sorted((self.run_dir / "raw").rglob("*")) if path.is_file()})

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
        _, self.config = _load_params(self.run_dir, type(self.base_params))
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
                analysis_steps.load_inputs(self.run_dir, self.config, self.params)
                self._archive_from(item)
                if item == "B01":
                    pass
                elif item == "B02":
                    self.summary.update(analysis_steps.evaluate(self.run_dir, self.config, self.params))
                elif item == "B03":
                    self.summary.update(analysis_steps.report(self.run_dir, self.config, self.params))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = [name for name in stage.artifacts if (self.results / name).is_file()]
                if missing := set(stage.artifacts) - set(files) - {"s_vs_amplitude.png"}:
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
                if isinstance(exc, AnalysisQualityError):
                    self.summary.update(exc.diagnostics)
                    stage = next(stage for stage in self.stages if stage.id == item)
                    files = [name for name in stage.artifacts if (self.results / name).is_file()]
                    self.states[item].update(artifacts=files,
                        files={name: self._file_signature(self.results / name) for name in files},
                        input_fingerprint=deepcopy(self.fingerprint), parameters=dict(self.options),
                        parameter_fingerprint=hashlib.sha256(json.dumps(self.options, sort_keys=True).encode()).hexdigest(),
                        revision=self.revision)
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


def analyze(
    run_dir: Path,
    *,
    params_type: type[MxYRFFrequencyResponseParams] = MxYRFFrequencyResponseParams,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    ctx = AnalysisWorkflow(run_dir, params_type=params_type)
    return ctx.execute([stage.id for stage in ctx.stages])


def main() -> int:
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        result = analyze(runtime_run_dir())
        print(f"Mx Y RF 频率响应分析完成: {result.get('run_dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
