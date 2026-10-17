"""Mx Y RF 完整/分步分析共用编排；历史采集参数保持只读。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import os
import yaml
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_steps, run_format
from .models import MxYRFParams
from .stages import ANALYSIS, ANALYSIS_KEYS, initial_states, invalidate, invalidated_stages, validate_selection
from .analysis_plotting import (_builtin, _load_params, _plot_full_analysis,
    _plot_measured_amplitude_response, analyze_core, analyze as _legacy_analyze)

ANALYSIS_VERSION = "mx-y-r-v1"


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.base_params, self.config = _load_params(self.run_dir)
        if reason := run_format.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items() if key in ANALYSIS_KEYS})
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        config = {key: value for key, value in self.config.items() if key not in ("analysis_status", "analysis_error")}
        self.fingerprint = dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True,
            ensure_ascii=False).encode()).hexdigest(), analysis_version=ANALYSIS_VERSION,
            raw_files={path.name: self._file_signature(path) for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()})
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(self.stages, self.states, "B01")
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != signature for name, signature in record.get("files", {}).items())):
                invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if analysis_parameters:
            if unknown := analysis_parameters.keys() - set(ANALYSIS_KEYS):
                raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
            if any(self.options[key] != value for key, value in analysis_parameters.items()):
                invalidate(self.stages, self.states, "B02")
                self.options.update(analysis_parameters)
        self.params = MxYRFParams.from_external(self.base_params.to_external() | self.options)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in self.options.values()):
            raise ValueError("分析覆盖参数必须为有限数值")
        if errors := self.params.validate():
            # 分析覆盖复用模型约束；安全限值仍从当前项目配置读取。
            raise ValueError("；".join(errors))

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        return [path.stat().st_size, path.stat().st_mtime_ns]

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
            steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, analysis_steps.plain(self.snapshot()))
        # 只更新分析状态，不把历史目录的分析覆盖值写入采集快照。
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"), analysis_error=self.summary.get("error"))
        self.config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        report_runtime_progress("analysis", "离线分析状态已保存", data={"analysis_workflow": analysis_steps.plain(self.snapshot())})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(self.stages, item) for name in stage.artifacts}
        files = [self.results / name for name in names if (self.results / name).is_file()]
        if files:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                path.replace(backup / path.name)

    def execute(self, selected, expected_revision=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary = {"status": "running", "options": dict(self.options)}
            self.persist()
            failure = None
            try:
                if item == "B01":
                    analysis_steps.load_inputs(self.run_dir, self.config, self.params)
                elif item == "B02":
                    self.summary.update(analysis_steps.evaluate(self.run_dir, self.params))
                elif item == "B03":
                    self.summary.update(analysis_steps.plain(analysis_steps.report(self.run_dir, self.params, self.config)))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = [name for name in stage.artifacts if (self.results / name).is_file()]
                self.states[item] = {"status": "completed", "error": "", "artifacts": files,
                    "files": {name: self._file_signature(self.results / name) for name in files}}
                self.summary["status"] = "completed" if item == "B03" else "partial"
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else (
                    "quality_failed" if isinstance(exc, analysis_steps.AnalysisQualityError) else "failed")
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


def analyze(run_dir: Path, *, params_type=MxYRFParams,
        ignore_relative_gamma_uncertainty=False, initial_center=None):
    # 既有实验变体保留其参数模型与拟合选项，不套用本实验身份规则。
    if params_type is not MxYRFParams or ignore_relative_gamma_uncertainty or initial_center is not None:
        return _legacy_analyze(run_dir, params_type=params_type,
            ignore_relative_gamma_uncertainty=ignore_relative_gamma_uncertainty, initial_center=initial_center)
    ctx = AnalysisWorkflow(run_dir)
    return ctx.execute([stage.id for stage in ctx.stages])


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir())


if __name__ == "__main__":
    main()
