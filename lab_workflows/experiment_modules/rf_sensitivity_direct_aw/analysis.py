"""DirectAW 分阶段离线分析；完整分析与流程图共用算法和产物。"""
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
from . import analysis_steps
from .analysis_steps import dispersive, fit_response_curve
from . import run_format
from .stages import ANALYSIS, initial_states, invalidate, invalidated_stages, validate_selection

ANALYSIS_VERSION = "direct-aw-x-stages-v2-r-bandwidth"


class AnalysisWorkflow:
    stages = ANALYSIS
    identity = run_format
    analysis_version = ANALYSIS_VERSION
    steps_module = analysis_steps

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config_path = self.run_dir / "experiment_config.yaml"
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if reason := self.identity.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {"noise_nperseg": self.config["parameters"]["NOISE_NPERSEG"]}
        self.options.update(previous.get("options", {}))
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        config = {key: value for key, value in self.config.items() if key not in ("analysis_status", "analysis_error")}
        self.fingerprint = dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True,
            ensure_ascii=False).encode()).hexdigest(), analysis_version=self.analysis_version,
            raw_files=self.input_fingerprint())
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
            if analysis_parameters.keys() - {"NOISE_NPERSEG"}:
                raise ValueError("仅支持分析参数 NOISE_NPERSEG")
            value = analysis_parameters.get("NOISE_NPERSEG", self.options["noise_nperseg"])
            if value != self.options["noise_nperseg"]:
                self.options["noise_nperseg"] = value
                invalidate(self.stages, self.states, "B03")
        value = self.options["noise_nperseg"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 2 or int(value) != value:
            raise ValueError("Welch 段长必须是至少 2 的整数")
        self.options["noise_nperseg"] = int(value)

    @staticmethod
    def _file_signature(path):
        return [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None

    def input_fingerprint(self):
        return {path.name: self._file_signature(path) for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()}

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
                    steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"), analysis_error=self.summary.get("error"))
        self.config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        report_runtime_progress("analysis", f"分析状态：{config['analysis_status']}", data={"analysis_workflow": self.snapshot()})

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
            raise ValueError("分析状态已改变，请刷新后重试")
        selected = validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(self.stages, self.states, item)
            self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            summary_keys = {"B02": ("response_fit",), "B03": ("noise_psd",),
                            "B04": ("sensitivity", "frequency_response_correction", "bandwidth_fit")}
            for stage in invalidated_stages(self.stages, item):
                for key in summary_keys.get(stage.id, ()):
                    self.summary.pop(key, None)
            self.summary.update(status="running", options=dict(self.options))
            self.summary.pop("error", None)
            self.persist()
            try:
                nperseg = self.options["noise_nperseg"]
                if item == "B01":
                    self.steps_module.load_inputs(self.run_dir, self.config)
                else:
                    self.results.mkdir(exist_ok=True)
                    if item == "B02":
                        self.summary["response_fit"] = self.steps_module.response_fit(self.run_dir, self.config, component=self.identity.RESPONSE_COMPONENT)
                    elif item == "B03":
                        self.summary["noise_psd"] = self.steps_module.noise_psd(self.run_dir, self.config, nperseg)
                    elif item == "B04":
                        self.summary.update(self.steps_module.sensitivity_and_plots(self.run_dir, self.config, nperseg, identity=self.identity))
                stage = next(stage for stage in self.stages if stage.id == item)
                self.states[item] = {"status": "completed", "error": "", "artifacts": list(stage.artifacts),
                    "files": {name: self._file_signature(self.results / name) for name in stage.artifacts}}
                self.summary["status"] = "completed" if item == "B04" else "partial"
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary


def analyze(run_dir: Path, *, workflow_type=AnalysisWorkflow) -> dict:
    ctx = workflow_type(run_dir)
    return ctx.execute([stage.id for stage in ctx.stages])


def main(*, workflow_type=AnalysisWorkflow):
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        ctx = workflow_type(runtime_run_dir(), payload.get("analysis_parameters"))
        ctx.execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir(), workflow_type=workflow_type)


if __name__ == "__main__":
    main()
