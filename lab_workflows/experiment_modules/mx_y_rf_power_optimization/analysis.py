"""双斜率网格分析的分阶段编排、内容指纹与独立覆盖值。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import os
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ..noise_spectrum_xy.state_io import read_state, write_state
from . import analysis_steps
from .analysis_steps import GRID_SPEC, AnalysisQualityError, plain
from .models import MxYRFPowerOptimizationParams
from .stages import ANALYSIS, ANALYSIS_KEYS, initial_states, invalidate, invalidated_stages, validate_selection

ANALYSIS_VERSION = "mx-y-power-grid-v1"


class AnalysisWorkflow:
    stages = ANALYSIS

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results, self.state_path = self.run_dir / "results", self.run_dir / "analysis_state.json"
        self.config = analysis_steps.load_config(self.run_dir)
        self.base_params = MxYRFPowerOptimizationParams.from_external(
            self.config["parameters"], schema_version=self.config.get("schema_version", 1))
        previous = read_state(self.state_path) or {}
        self.options = {key: self.base_params.to_external()[key] for key in ANALYSIS_KEYS}
        self.options.update({key: value for key, value in previous.get("options", {}).items() if key in ANALYSIS_KEYS})
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps") or {})
        self.revision, self.summary = previous.get("revision", 0), deepcopy(previous.get("summary", {}))
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
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in self.options.values()):
            raise ValueError("分析覆盖参数必须为有限数值")
        self.params = MxYRFPowerOptimizationParams.from_external(self.base_params.to_external() | self.options)
        if errors := self.params.validate():
            raise ValueError("；".join(errors))

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
        config = {key: value for key, value in self.config.items() if key not in ("analysis_status", "analysis_error")}
        return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
            default=str).encode()).hexdigest(), analysis_version=ANALYSIS_VERSION,
            raw_files={p.relative_to(self.run_dir).as_posix(): self._file_signature(p)
                for p in sorted((self.run_dir / "raw").rglob("*")) if p.is_file()})

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
        # 采集快照完全只读；分析覆盖及状态仅写独立记录。
        write_state(self.state_path, plain(self.snapshot()))
        report_runtime_progress("analysis", "离线分析状态已保存", data={"analysis_workflow": plain(self.snapshot())})

    def product_files(self, stage):
        files = []
        for name in stage.artifacts:
            path = self.results / name
            if path.is_file():
                files.append(name)
            elif path.is_dir():
                files.extend(p.relative_to(self.results).as_posix() for p in sorted(path.rglob("*")) if p.is_file())
        return files

    def _archive_from(self, item):
        paths = [self.results / name for name in dict.fromkeys(
            name for stage in invalidated_stages(self.stages, item) for name in stage.artifacts)
            if (self.results / name).exists()]
        if paths:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in paths:
                path.replace(backup / path.name)

    def execute(self, selected, expected_revision=None):
        self.config = analysis_steps.load_config(self.run_dir)
        fingerprint = self.input_fingerprint()
        if self.fingerprint != fingerprint:
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
            failure = None
            try:
                # 资格检查在归档之前，坏输入不能移动旧的科学产物。
                analysis_steps.load_inputs(self.run_dir)
                self._archive_from(item)
                if item == "B02":
                    self.summary.update(plain(analysis_steps.evaluate(self.run_dir, self.params)))
                elif item == "B03":
                    self.summary.update(analysis_steps.write_report(self.run_dir, self.options))
                stage = next(stage for stage in self.stages if stage.id == item)
                files = self.product_files(stage)
                required = {"optimization.npz", "optimization.yaml", "optimization.json", "sensitivity_heatmap.png"} if item == "B02" else set(stage.artifacts)
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
                stage = next(stage for stage in self.stages if stage.id == item)
                files = self.product_files(stage)
                self.states[item] = {"status": status, "error": str(exc), "artifacts": files,
                    "files": {name: self._file_signature(self.results / name) for name in files},
                    "input_fingerprint": deepcopy(self.fingerprint), "parameters": dict(self.options),
                    "parameter_fingerprint": hashlib.sha256(json.dumps(self.options, sort_keys=True).encode()).hexdigest(),
                    "revision": self.revision}
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
    return AnalysisWorkflow(run_dir).execute([stage.id for stage in ANALYSIS])


def main():
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters")).execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
