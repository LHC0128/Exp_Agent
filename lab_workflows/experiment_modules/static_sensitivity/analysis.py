"""静磁场分阶段离线分析；完整分析复用相同算法与产物。"""
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
from lab_workflows.workflow_state import read_state, write_state
from . import analysis_steps
from .stages import ANALYSIS, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states
from .run_format import format_block_reason

ANALYSIS_VERSION = "static-dc-stages-v1"


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") == "running":
        return "本轮扫描仍在写入数据，不能分析"
    if not isinstance(config.get("parameters"), dict):
        return "运行配置缺少参数快照"
    return None


class AnalysisWorkflow:
    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        if reason := analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.options = {"z_v_to_nt": self.config["parameters"].get("z_v_to_nt")}
        self.options.update(previous.get("options", {}))
        self.states = deepcopy(previous.get("steps") or initial_states(ANALYSIS))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        raw = self.run_dir / "raw"
        waveforms = sorted((raw / "noise_raw").glob("gs200_range_*/noise_*.npy"))
        files = [raw / "dispersion_scan.npz", *waveforms,
                 *(path.with_name(path.stem + "_acquisition.npz") for path in waveforms)]
        self.fingerprint = dict(analysis_version=ANALYSIS_VERSION,
            config_sha256=hashlib.sha256(json.dumps({key: value for key, value in self.config.items()
                if key not in ("analysis_status", "analysis_error")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            raw_files={path.relative_to(raw).as_posix(): self._file_signature(path) for path in files})
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != record.get("files", {}).get(name)
                    for name in stage.artifacts)):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
        if analysis_parameters is not None:
            if analysis_parameters.keys() - {"z_v_to_nt"}:
                raise ValueError("历史分析仅支持覆盖 z_v_to_nt")
            value = analysis_parameters.get("z_v_to_nt", self.options["z_v_to_nt"])
            if value != self.options["z_v_to_nt"]:
                self.options["z_v_to_nt"] = value
                invalidate(ANALYSIS, self.states, "B02")
        value = self.options["z_v_to_nt"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Z 场换算系数必须为有限正数")
        self.options["z_v_to_nt"] = float(value)
        if self.states["B04"]["status"] != "completed":
            self.summary["success"] = False
            for key in ("sensitivity_flat_fT_per_sqrt_Hz", "flat_frequency_min_Hz", "flat_frequency_max_Hz"):
                self.summary.pop(key, None)

    @staticmethod
    def _file_signature(path):
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = path.stat()
        return [stat.st_size, stat.st_mtime_ns, *digest.digest()]

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
                    steps=deepcopy(self.states), fingerprint=self.fingerprint, summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        self.results.mkdir(exist_ok=True)
        # 分析状态独立保存，采集参数和快照永不被分析覆盖。
        analysis_steps._save_summary(self.results, self.summary)
        report_runtime_progress("analysis", f"分析状态：{self.summary.get('status', 'not_started')}",
                                data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        names = {name for stage in invalidated_stages(ANALYSIS, item) for name in stage.artifacts}
        files = [self.results / name for name in names if (self.results / name).is_file()]
        if files:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                path.replace(backup / path.name)

    def execute(self, selected, expected_revision=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析状态已改变，请刷新后重试")
        selected = validate_selection(ANALYSIS, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            invalidate(ANALYSIS, self.states, item)
            self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            if item in ("B01", "B02"):
                for key in list(self.summary):
                    if key.startswith("fit_"):
                        self.summary.pop(key)
            self.summary.update(status="running", success=False, options=dict(self.options))
            self.summary.pop("error", None)
            for key in ("sensitivity_flat_fT_per_sqrt_Hz", "flat_frequency_min_Hz", "flat_frequency_max_Hz"):
                self.summary.pop(key, None)
            self.persist()
            try:
                if item == "B01":
                    result = analysis_steps.load_inputs(self.run_dir, self.config)
                elif item == "B02":
                    result = analysis_steps.dispersion_fit(self.run_dir, self.config, self.options["z_v_to_nt"])
                elif item == "B03":
                    result = analysis_steps.noise_psd(self.run_dir, self.config)
                else:
                    result = analysis_steps.sensitivity_and_plots(self.run_dir, self.config, self.options["z_v_to_nt"])
                self.summary.update(result, status="completed" if item == "B04" else "partial")
                stage = next(stage for stage in ANALYSIS if stage.id == item)
                self.states[item] = {"status": "completed", "error": "", "artifacts": list(stage.artifacts)}
                self.persist()
                self.states[item]["files"] = {name: self._file_signature(self.results / name) for name in stage.artifacts}
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                diagnostic = read_state(self.results / "analysis.json") or {}
                self.summary.update(diagnostic, status=status, success=False, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary


def analyze(run_dir):
    return AnalysisWorkflow(run_dir).execute([stage.id for stage in ANALYSIS])


def main() -> int:
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        ctx = AnalysisWorkflow(runtime_run_dir(), payload.get("analysis_parameters"))
        ctx.execute(payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
