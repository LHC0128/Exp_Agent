"""Probe AM 噪声谱：可持久化的分析依赖图；不连接硬件。"""
import hashlib
import json
import math
import os
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ...plotting import set_plot_style
from ..noise_spectrum_xy.acquisition import save_config
from ..noise_spectrum_xy.state_io import read_state, write_state
from ..noise_spectrum_xy_known_noise.analysis_core import _json_safe, write_json
from . import analysis_steps, stages
from .models import NoiseSpectrumXYProbeAMParams

ANALYSIS_VERSION = "probe-am-staged-v3"
OPTION_KEYS = {"bin_width_hz": "ANALYSIS_BIN_WIDTH_HZ", "frequency_min_hz": "ANALYSIS_FREQUENCY_MIN_HZ",
               "frequency_max_hz": "ANALYSIS_FREQUENCY_MAX_HZ", "fit_half_width_hz": "ANALYSIS_FIT_HALF_WIDTH_HZ"}
SUMMARY_KEYS = {"B02": ("psd",), "B03": ("calibration",), "B04": ("fit_quality", "interpretation")}


class AnalysisWorkflow:
    """持久化四个分析阶段；前置产物必须属于同一输入和参数修订。"""

    def __init__(self, run_dir, analysis_parameters=None, *, use_saved_options=True, project_root=None):
        self.run_dir = Path(run_dir).resolve()
        self.root = Path(project_root).resolve() if project_root else find_project_root(self.run_dir)
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        if self.config.get("experiment_id") != stages.EXPERIMENT_ID:
            raise ValueError("运行目录不是 XY Probe AM 噪声谱实验")
        if self.config.get("completion_status") == "running":
            raise ValueError("本轮扫描仍在写入数据，请等待扫描结束")
        defaults = NoiseSpectrumXYProbeAMParams().to_external()
        saved = self.config.get("parameters", {})
        self.options = {key: float(saved.get(name, defaults[name])) for key, name in OPTION_KEYS.items()}
        previous = read_state(self.state_path) or {}
        if use_saved_options:
            self.options.update({key: value for key, value in previous.get("options", {}).items() if key in OPTION_KEYS})
        self.states = stages.initial_states(stages.ANALYSIS) | previous.get("steps", {})
        self.revision = previous.get("revision", 0)
        self.summary = previous.get("summary") or dict(analysis_version=ANALYSIS_VERSION, warnings=[], status="not_started")
        acquisition_config = {key: value for key, value in self.config.items() if key not in ("analysis_status", "analysis_error")}
        self.fingerprint = dict(config_sha256=hashlib.sha256(json.dumps(acquisition_config, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest(),
            raw_files=[dict(name=str(path.relative_to(self.run_dir / "raw")), signature=self._file_signature(path))
                       for path in sorted((self.run_dir / "raw").rglob("*")) if path.is_file()], analysis_version=ANALYSIS_VERSION)
        if previous and previous.get("fingerprint") != self.fingerprint:
            stages.invalidate(stages.ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in stages.ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running" or record["status"] == "completed" and any(
                    self._file_signature(self.results / name) != signature for name, signature in record.get("files", {}).items()):
                stages.invalidate(stages.ANALYSIS, self.states, stage.id)
                self.revision += 1
        changed = set()
        for name, value in (analysis_parameters or {}).items():
            if name not in OPTION_KEYS.values():
                raise ValueError("不是本实验的分析参数：" + name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("分析数值参数类型无效：" + name)
            value = float(value)
            if value != self.options[next(key for key, external in OPTION_KEYS.items() if external == name)]:
                self.options[next(key for key, external in OPTION_KEYS.items() if external == name)] = value
                changed.add(name)
        values = list(self.options.values())
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
               for value in values) or self.options["bin_width_hz"] <= 0 or self.options["fit_half_width_hz"] <= 0 \
                or self.options["frequency_min_hz"] >= self.options["frequency_max_hz"]:
            raise ValueError("分析参数范围无效")
        for stage in stages.ANALYSIS:
            if changed.intersection(stage.parameters):
                stages.invalidate(stages.ANALYSIS, self.states, stage.id)
        if changed:
            self.revision += 1
        self._refresh_summary()

    @staticmethod
    def _file_signature(path):
        return [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None

    def _refresh_summary(self):
        for stage_id, keys in SUMMARY_KEYS.items():
            if self.states[stage_id]["status"] in ("pending", "stale", "running"):
                for key in keys:
                    self.summary.pop(key, None)
        self.summary["options"] = dict(self.options)
        if self.states["B04"]["status"] == "completed" and "fit_quality" in self.summary:
            provisional = self.summary.get("calibration", {}).get("source") == "configured_K_B"
            valid_bins = int(self.summary["fit_quality"].get("valid_background_bins", 0))
            if provisional:
                self.summary["status"] = "quality_failed"
                self.summary["error"] = "移动峰自动标定未通过；已按运行参数 K/B 生成暂定控制轴，结果仅供暂时分析"
            elif not valid_bins:
                self.summary["status"] = "quality_failed"
                self.summary["error"] = "没有频率点通过 ON/OFF 背景拟合质量验收"
            else:
                self.summary["status"] = "completed"
                self.summary.pop("error", None)
        elif any(state["status"] in ("quality_failed", "failed", "cancelled") for state in self.states.values()):
            self.summary["status"] = next(state["status"] for state in self.states.values()
                if state["status"] in ("quality_failed", "failed", "cancelled"))
        else:
            self.summary["status"] = "partial" if any(s["status"] == "completed" for s in self.states.values()) else "not_started"

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options, steps=self.states,
                    fingerprint=self.fingerprint, summary=_json_safe(self.summary))

    def persist(self):
        self._refresh_summary()
        write_state(self.state_path, self.snapshot())
        write_json(self.results / "analysis_summary.json", self.summary)
        self.config["analysis_status"] = self.summary["status"]
        self.config["analysis_error"] = self.summary.get("error")
        save_config(self.run_dir / "experiment_config.yaml", self.config)
        report_runtime_progress("analysis", f"分析状态：{self.summary['status']}", data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        affected = stages.invalidated_stages(stages.ANALYSIS, item)
        names = {name for stage in affected for name in stage.artifacts}
        files = [path for path in self.results.iterdir() if path.is_file() and path.name in names]
        if files:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                shutil.move(str(path), str(backup / path.name))
            if (self.results / "analysis_summary.json").is_file():
                shutil.copy2(self.results / "analysis_summary.json", backup / "analysis_summary.json")
            self.summary["backup_directory"] = backup.name

    def execute(self, selected):
        selected = stages.validate_selection(stages.ANALYSIS, self.states, selected)
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        set_plot_style("paper")
        for item in selected:
            check_cancelled()
            self._archive_from(item)
            stages.invalidate(stages.ANALYSIS, self.states, item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.pop("error", None)
            failure = None
            try:
                self.persist()
                getattr(analysis_steps, "step_" + item)(self)
                stage = next(stage for stage in stages.ANALYSIS if stage.id == item)
                self.states[item] = {"status": "completed", "error": "", "files": {
                    name: self._file_signature(self.results / name) for name in stage.artifacts}}
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary["error"] = str(exc)
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if failure is None:
                        raise
        return self.summary


# %% 完整分析与分阶段入口
def analyze(run_dir):
    ctx = AnalysisWorkflow(run_dir)
    return ctx.execute([stage.id for stage in stages.ANALYSIS])


def main():
    parameters = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    directory = runtime_run_dir()
    if "step_ids" in parameters:
        ctx = AnalysisWorkflow(directory, parameters.get("analysis_parameters"))
        # 父进程已经校验基线修订；子进程再次拒绝执行期间改变的输入。
        original = AnalysisWorkflow(directory)
        if original.revision != parameters["revision"]:
            raise ValueError("分析修订已改变，请刷新后重试")
        summary = ctx.execute(parameters["step_ids"])
    else:
        summary = analyze(directory)
    return 0 if summary["status"] in ("completed", "partial") else 2


if __name__ == "__main__":
    raise SystemExit(main())
