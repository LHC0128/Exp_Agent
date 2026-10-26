# %% [markdown]
# # XY 已知噪声谱：可持久化的分析依赖图

# %% 导入
import hashlib
import json
import math
import os
import shutil
from datetime import datetime
from pathlib import Path
import yaml
from ...common import find_project_root, WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, runtime_run_dir
from ...plotting import set_plot_style
from lab_workflows.workflow_state import read_state, write_state
from ..noise_spectrum_xy.acquisition import save_config
from .models import NoiseSpectrumXYKnownNoiseParams
from .analysis_core import ANALYSIS_VERSION, write_json, _json_safe, psd_input_config
from . import stages, analysis_steps
from .calibration_source import source_fingerprint, file_fingerprint

OPTION_KEYS = {"bin_width_hz": "ANALYSIS_BIN_WIDTH_HZ", "frequency_min_hz": "ANALYSIS_FREQUENCY_MIN_HZ",
    "frequency_max_hz": "ANALYSIS_FREQUENCY_MAX_HZ", "fit_half_width_hz": "ANALYSIS_FIT_HALF_WIDTH_HZ",
    "dispersion_source_run": "ANALYSIS_DISPERSION_SOURCE_RUN", "ratio_pass_low": "RATIO_PASS_LOW", "ratio_pass_high": "RATIO_PASS_HIGH"}
SUMMARY_KEYS = {"B02": ("psd",), "B03": ("calibration",), "B04": ("fit_quality",),
                "B05": ("dispersion",), "B06": ("comparison", "truth_chain")}


def require_analysis_identity(config):
    """完整和分步分析共用实验身份与运行中保护。"""
    if not isinstance(config, dict) or config.get("experiment_id") != stages.EXPERIMENT_ID:
        raise ValueError("运行目录不是 XY 已知可控噪声谱实验")
    if config.get("completion_status") == "running":
        raise ValueError("本轮扫描仍在写入数据，请等待扫描结束")


def analysis_block_reason(config):
    """通用历史与旧重分析共用身份和采集配置资格。"""
    try:
        require_analysis_identity(config)
        psd_input_config(config)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return str(exc)
    return None


class AnalysisWorkflow:
    def __init__(self, run_dir, analysis_parameters=None, *, use_saved_options=True, project_root=None):
        self.run_dir = Path(run_dir).resolve()
        self.root = Path(project_root).resolve() if project_root else find_project_root(self.run_dir)
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        require_analysis_identity(self.config)
        self.legacy_dispersion = bool(self.config.get("dispersion")) and int(self.config.get("schema_version", 1)) < 3
        defaults = NoiseSpectrumXYKnownNoiseParams().to_external()
        saved = self.config.get("parameters", {})
        self.options = {key: saved.get(name, defaults[name]) for key, name in OPTION_KEYS.items()}
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
            key = next(key for key, external in OPTION_KEYS.items() if name == external)
            if key == "dispersion_source_run":
                if not isinstance(value, str):
                    raise ValueError("标定来源必须为字符串")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("分析数值参数类型无效：" + name)
                value = float(value)
            if value != self.options[key]:
                self.options[key] = value
                changed.add(name)
        numeric = [value for key, value in self.options.items() if key != "dispersion_source_run"]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in numeric) \
                or self.options["bin_width_hz"] <= 0 or self.options["fit_half_width_hz"] <= 0 \
                or self.options["frequency_min_hz"] >= self.options["frequency_max_hz"] \
                or not 0 < self.options["ratio_pass_low"] < self.options["ratio_pass_high"] \
                or not isinstance(self.options["dispersion_source_run"], str):
            raise ValueError("分析参数范围无效")
        for stage in stages.ANALYSIS:
            if changed.intersection(stage.parameters):
                stages.invalidate(stages.ANALYSIS, self.states, stage.id)
        if changed:
            self.revision += 1
        self.source_signature = None
        if self.options["dispersion_source_run"]:
            try:
                self.source_signature = source_fingerprint(self.root, self.options["dispersion_source_run"])
            except (ValueError, OSError):
                self.source_signature = {"invalid_source": self.options["dispersion_source_run"]}
        if self.states["B05"]["status"] == "completed" and previous.get("source_signature") != self.source_signature:
            stages.invalidate(stages.ANALYSIS, self.states, "B05")
            self.revision += 1
        self._refresh_summary()

    @staticmethod
    def _file_signature(path):
        return [path.stat().st_size, path.stat().st_mtime_ns, file_fingerprint(path)] if path.is_file() else None

    def _refresh_summary(self):
        for stage_id, keys in SUMMARY_KEYS.items():
            if self.states[stage_id]["status"] in ("pending", "stale", "waiting_calibration", "running"):
                for key in keys:
                    self.summary.pop(key, None)
        self.summary["options"] = dict(self.options)
        if self.states["B06"]["status"] == "completed":
            self.summary["status"] = "completed"
        elif any(state["status"] in ("quality_failed", "failed", "cancelled") for state in self.states.values()):
            self.summary["status"] = next(state["status"] for state in self.states.values() if state["status"] in ("quality_failed", "failed", "cancelled"))
        elif self.states["B04"]["status"] == "completed" and not self.options["dispersion_source_run"] and not self.legacy_dispersion:
            self.summary["status"] = "waiting_calibration"
        else:
            self.summary["status"] = "partial" if any(s["status"] == "completed" for s in self.states.values()) else "not_started"
        for item in ("B05", "B06"):
            if not self.options["dispersion_source_run"] and not self.legacy_dispersion:
                if self.states[item]["status"] in ("pending", "stale", "waiting_calibration"):
                    self.states[item] = {"status": "waiting_calibration", "error": "待选择标定曲线"}
            elif self.states[item]["status"] == "waiting_calibration":
                self.states[item] = {"status": "pending", "error": ""}

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options, steps=self.states,
                    fingerprint=self.fingerprint, source_signature=self.source_signature, summary=_json_safe(self.summary))

    def persist(self, *, write_results=True):
        self._refresh_summary()
        write_state(self.state_path, self.snapshot())
        if write_results:
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
            failure = None
            validated = False
            try:
                # 输入资格先通过，才能归档或覆盖已有科学结果。
                if item in ("B01", "B02"):
                    analysis_steps.step_B01(self)
                elif item == "B05":
                    gain = analysis_steps.resolve_detection_gain(self)
                validated = True
                self._archive_from(item)
                stages.invalidate(stages.ANALYSIS, self.states, item)
                self.states[item] = {"status": "running", "error": ""}
                self.summary.pop("error", None)
                self.persist()
                if item == "B05":
                    analysis_steps.step_B05(self, gain)
                elif item != "B01":
                    getattr(analysis_steps, "step_" + item)(self)
                stage = next(stage for stage in stages.ANALYSIS if stage.id == item)
                self.states[item] = {"status": "completed", "error": "", "files": {
                    name: self._file_signature(self.results / name) for name in stage.artifacts}}
            except BaseException as exc:
                failure = exc
                stages.invalidate(stages.ANALYSIS, self.states, item)
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary["error"] = str(exc)
                raise
            finally:
                try:
                    self.persist(write_results=validated)
                except Exception:
                    if failure is None:
                        raise
        return self.summary


# %% 完整分析与分阶段入口
def analyze(run_dir):
    ctx = AnalysisWorkflow(run_dir)
    selected = stages.ANALYSIS if ctx.options["dispersion_source_run"] or ctx.legacy_dispersion else stages.ANALYSIS[:4]
    return ctx.execute([stage.id for stage in selected])


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
    return 0 if summary["status"] in ("completed", "partial", "waiting_calibration") else 2


if __name__ == "__main__":
    raise SystemExit(main())
