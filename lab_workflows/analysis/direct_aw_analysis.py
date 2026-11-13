"""DirectAW 家族的分阶段离线分析引擎。

修订、输入指纹、阶段记录、产物归档、B01–B04 分派与运行配置写回在此统一；实验只声明
阶段清单、运行格式、算法模块与分析参数，算法仍留在各自的 ``analysis_steps``。
子控制产物沿用状态文件里的 ``../controls/<版本>/results/<名字>`` 相对键，产物路径、
签名与归档都按运行目录解析；不改骨架 ``staged_workflow.py``。
导入不连接硬件，也不导入 ``experiment_modules``。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ..experiment_runtime import check_cancelled, runtime_run_dir
from ..staged_workflow import CANCELLED, StagedAnalysis
from ..steps.run_directory import write_run_config
from ..workflow_stages import DependencyRules, initial_states
from ..workflow_state import read_state

ANALYSIS_OPTIONS = {"NOISE_NPERSEG": "noise_nperseg", "NOISE_OUTLIER_POLICY": "noise_outlier_policy",
    "SENSITIVITY_BAND_MIN_HZ": "band_min_hz", "SENSITIVITY_BAND_MAX_HZ": "band_max_hz"}
# 只有两个 AW 实验重算异常谱筛选；Scale 实验保持只重算 Welch 段长的历史语义。
NOISE_FILTER_EXPERIMENTS = ("rf-sensitivity-aw", "rf-sensitivity-aw-known-noise")
OPTION_DEFAULTS = {"NOISE_OUTLIER_POLICY": "none", "SENSITIVITY_BAND_MIN_HZ": 3.0, "SENSITIVITY_BAND_MAX_HZ": 150.0}
# 分析诊断与覆盖值不构成输入变化，指纹与子控制配置摘要都排除这三项。
EXCLUDED_CONFIG_KEYS = ("analysis_status", "analysis_error", "analysis_options")
CHILD_PREFIX = "../controls"


def stat_signature(path):
    """按 ``[字节数, 修改时间]`` 识别文件；保持历史 analysis_state.json 的签名格式。"""
    path = Path(path)
    return [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None


class _Parameters:
    """参数视图：只通过 ``to_external()`` 暴露键值，供配置读取与选项解析共用。"""

    __slots__ = ("_values",)

    def __init__(self, values):
        self._values = dict(values)

    def to_external(self) -> dict[str, Any]:
        return dict(self._values)


class _Steps:
    """``StagedAnalysis`` 要求的五函数协议，默认实现留在本引擎。

    子类 ``steps_module`` 提供同名函数时优先，实验只在算法模块里补自己的差异。
    """

    __slots__ = ("analysis",)

    def __init__(self, analysis: "DirectAWAnalysis"):
        self.analysis = analysis

    def output_path(self, run_dir, name):
        override = getattr(self.analysis.steps_module, "output_path", None)
        return override(run_dir, name) if override else Path(run_dir) / name

    def load_config(self, run_dir):
        override = getattr(self.analysis.steps_module, "load_config", None)
        if override:
            return override(run_dir)
        config = yaml.safe_load((Path(run_dir) / "experiment_config.yaml").read_text(encoding="utf-8"))
        # 损坏配置交给运行资格判定报错；这里不提前抛类型错误。
        parameters = config.get("parameters") if isinstance(config, dict) else None
        return config, _Parameters(parameters if isinstance(parameters, dict) else {})

    def resolve_options(self, base_params, values):
        override = getattr(self.analysis.steps_module, "resolve_options", None)
        return override(base_params, values) if override else self.analysis.configured_options(base_params, values)

    def signature(self, path):
        override = getattr(self.analysis.steps_module, "signature", None)
        return override(path) if override else stat_signature(path)

    def fingerprint(self, run_dir):
        override = getattr(self.analysis.steps_module, "fingerprint", None)
        if override:
            return override(run_dir)
        analysis = self.analysis
        config = {key: value for key, value in analysis.config.items() if key not in EXCLUDED_CONFIG_KEYS}
        return dict(
            config_sha256=hashlib.sha256(
                json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            analysis_version=analysis.analysis_version, raw_files=analysis.input_fingerprint())


class DirectAWAnalysis(StagedAnalysis):
    """DirectAW 家族的分阶段离线分析。

    子类声明 stages/identity/analysis_version/steps_module 与自家选项映射，并按需声明
    multi_stages（整轮多控制运行换用的清单）与 child_artifacts（子控制运行产物名）；
    算法由 steps_module 注入，本类只管修订、指纹、分派、归档与状态写回。
    """

    stages: ClassVar[tuple[Any, ...]]
    identity: ClassVar[Any] = None
    analysis_version: ClassVar[str] = ""
    steps_module: ClassVar[Any] = None
    final_id: ClassVar[str] = "B04"
    label: ClassVar[str] = "分析"
    # StagedAnalysis 协议的分析参数键；本家族配置与历史状态都用内部名。
    analysis_keys: ClassVar[tuple[str, ...]] = ("noise_nperseg", "noise_outlier_policy",
        "band_min_hz", "band_max_hz")
    option_defaults: ClassVar[dict[str, Any]] = OPTION_DEFAULTS
    option_map: ClassVar[dict[str, str]] = ANALYSIS_OPTIONS
    # 整轮多控制运行换用的阶段清单；None 表示本实验不做切换。
    multi_stages: ClassVar[tuple[Any, ...] | None] = None
    # 子控制运行按阶段归档的产物名；空表示本实验没有子控制运行。
    child_artifacts: ClassVar[dict[str, tuple[str, ...]]] = {}
    # 重算前置节点时清理的摘要键，避免旧结论残留。
    stage_summary_keys: ClassVar[dict[str, tuple[str, ...]]] = {
        "B02": ("response_fit",),
        "B03": ("noise_psd", "noise_rejection"),
        "B04": ("sensitivity", "frequency_response_correction", "bandwidth_fit", "controls",
                "rf_phase_scan_status", "noise_state", "frequency_axis"),
    }

    # ---- 生命周期 ----
    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.steps = _Steps(self)
        self.results = self.steps.output_path(self.run_dir, "results")
        self.state_path = self.steps.output_path(self.run_dir, "analysis_state.json")
        self.config_path = self.steps.output_path(self.run_dir, "experiment_config.yaml")
        self.config, self.base_params = self.steps.load_config(self.run_dir)
        if self.multi_stages is not None and self.multi_control_run(self.config):
            self.stages = self.multi_stages
        if reason := self.identity.analysis_block_reason(self.config):
            raise ValueError(reason)
        previous = read_state(self.state_path) or {}
        self.rules = DependencyRules(finish_id=None)
        self.option_names = self.identity_options()
        self.states = deepcopy(previous.get("steps") or initial_states(self.stages))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = self.steps.fingerprint(self.run_dir)
        if previous and previous.get("fingerprint") != self.fingerprint:
            self.rules.invalidate(self.stages, self.states, self.first_id)
            self.revision += 1
        self.refresh_products()
        restored = previous.get("options", {})
        requested = dict(analysis_parameters or {})
        if requested.keys() - self.option_names.keys():
            raise ValueError("包含不支持的分析参数")
        configured = {name: self.base_params.to_external().get(external, self.option_defaults.get(external))
                      for external, name in self.option_names.items()}
        configured.update(restored)
        overrides = {self.option_names[external]: value for external, value in requested.items()}
        self.options = self.steps.resolve_options(self.base_params, restored | overrides).to_external()
        for external, value in requested.items():
            if value != configured[self.option_names[external]]:
                self.rules.invalidate(self.stages, self.states, "B03")
        self.config["analysis_options"] = dict(self.options)

    def refresh_products(self):
        """恢复中断或产物漂移的最早节点；一次刷新只增加一次修订。"""
        for stage in self.stages:
            record = self.states[stage.id]
            if record["status"] == "running" or (record["status"] == "completed" and any(
                    self._signature(name) != signature for name, signature in record.get("files", {}).items())):
                self.rules.invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break

    # ---- 子类可覆写的家族差异 ----
    def identity_options(self) -> dict[str, str]:
        """本次运行生效的分析参数映射（外部名 → 内部名）。"""
        if self.identity.EXPERIMENT_ID in NOISE_FILTER_EXPERIMENTS:
            return dict(self.option_map)
        return {"NOISE_NPERSEG": "noise_nperseg"}

    def multi_control_run(self, config: dict) -> bool:
        """整轮多控制运行；运行格式模块未提供判定时按单控制处理。"""
        check = getattr(self.identity, "is_multi_control", None)
        return bool(check and check(config))

    def configured_options(self, base_params, values) -> _Parameters:
        """合并配置值、家族默认值与覆盖值，校验后返回内部名键的选项视图。"""
        options = {name: base_params.to_external().get(external, self.option_defaults.get(external))
                   for external, name in self.option_names.items()}
        options.update(values)
        nperseg = options["noise_nperseg"]
        if (isinstance(nperseg, bool) or not isinstance(nperseg, (int, float))
                or not math.isfinite(nperseg) or nperseg < 2 or int(nperseg) != nperseg):
            raise ValueError("Welch 段长必须是至少 2 的整数")
        options["noise_nperseg"] = int(nperseg)
        if "NOISE_OUTLIER_POLICY" in self.option_names:
            if options["noise_outlier_policy"] not in ("none", "mad_high"):
                raise ValueError("NOISE_OUTLIER_POLICY 必须为 mad_high 或 none")
            low, high = (options[name] for name in ("band_min_hz", "band_max_hz"))
            if not all(type(value) in (int, float) and math.isfinite(value) for value in (low, high)) \
                    or not 0 < low < high:
                raise ValueError("异常谱评分频带必须满足 0 < 下限 < 上限")
        return _Parameters(options)

    # ---- 输入与产物 ----
    def _file_signature(self, path):
        """历史入口：按 ``[字节数, 修改时间]`` 识别文件；子实验的指纹覆写仍调用它。"""
        return self.steps.signature(path)

    def _path(self, name):
        """产物路径；``../controls/<版本>/results/<名字>`` 这类相对键落到子控制运行目录。"""
        return Path(os.path.normpath(self.results / name))

    def _signature(self, name):
        return self.steps.signature(self._path(name))

    def child_inputs(self):
        """子控制运行 ``(版本, 目录, 配置)``；没有该协议或就是本运行目录时为空。"""
        inputs = getattr(self.steps_module, "control_inputs", None)
        return [(name, path, config) for name, path, config in inputs(self.run_dir, self.config)
                if path != self.run_dir] if inputs else []

    def input_fingerprint(self):
        """指纹覆盖本轮 raw 与子控制运行的 raw/config；子控制配置排除分析诊断键。"""
        result = {path.name: self._file_signature(path)
                  for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()}
        for version, child, config in self.child_inputs():
            clean = {key: value for key, value in config.items() if key not in EXCLUDED_CONFIG_KEYS}
            result[version + "/config"] = hashlib.sha256(
                json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for path in sorted((child / "raw").glob("*")):
                if path.is_file():
                    result[version + "/" + path.name] = self._file_signature(path)
        return result

    def persist(self):
        """保存分析状态后再把状态写回 experiment_config.yaml（历史界面读这两个键）。"""
        for version, child, _ in self.child_inputs():
            for step, names in self.child_artifacts.items():
                if self.states[step]["status"] == "completed":
                    files = self.states[step].setdefault("files", {})
                    for name in names:
                        files[f"{CHILD_PREFIX}/{version}/results/{name}"] = self._signature(
                            f"{CHILD_PREFIX}/{version}/results/{name}")
        super().persist()
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        config.update(analysis_status=self.summary.get("status", "not_started"),
                      analysis_error=self.summary.get("error"))
        write_run_config(self.config_path, config)

    def _archive(self, item):
        """归档被失效节点的现有产物；子控制产物在各自 results 下建 before_<时间>。"""
        affected = self.rules.invalidated_stages(self.stages, item)
        paths = {path for stage in affected for path in self._outputs(stage.id)}
        child_names = {name for stage in affected for name in self.child_artifacts.get(stage.id, ())}
        paths.update(self._path(f"{CHILD_PREFIX}/{version}/results/{name}")
                     for version, _, _ in self.child_inputs() for name in child_names)
        for base in {path.parent for path in paths if path.is_file()}:
            backup = base / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in sorted(path for path in paths if path.parent == base and path.is_file()):
                path.replace(backup / path.name)

    def _archive_from(self, item):
        """家族旧钩子：子实验覆写它可在归档后清理自己的摘要键。"""
        self._archive(item)

    # ---- 执行 ----
    def run_step(self, item: str):
        """执行一个分析节点；B01–B04 的分派在家族三个实验里完全相同。"""
        if item == self.first_id:
            self.steps_module.load_inputs(self.run_dir, self.config)
            return None
        self.results.mkdir(exist_ok=True)
        nperseg = self.options["noise_nperseg"]
        if item == "B02":
            return {"response_fit": self.steps_module.response_fit(
                self.run_dir, self.config, component=self.identity.RESPONSE_COMPONENT)}
        if item == "B03":
            return {"noise_psd": self.steps_module.noise_psd(self.run_dir, self.config, nperseg)}
        if item == self.final_id:
            return self.steps_module.sensitivity_and_plots(
                self.run_dir, self.config, nperseg, identity=self.identity)
        return None

    def execute(self, selected, expected_revision=None):
        """按依赖顺序执行所选节点；每次状态变化都落盘并写回运行配置。"""
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析状态已改变，请刷新后重试")
        selected = self.rules.validate_selection(self.stages, self.states, selected)
        self.revision += 1
        for item in selected:
            check_cancelled()
            self.rules.invalidate(self.stages, self.states, item)
            self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            for stage in self.rules.invalidated_stages(self.stages, item):
                for key in self.stage_summary_keys.get(stage.id, ()):
                    self.summary.pop(key, None)
            self.summary.update(status="running", options=dict(self.options))
            self.summary.pop("error", None)
            self.persist()
            try:
                self.summary.update(self.run_step(item) or {})
                stage = self._stage(item)
                self.states[item] = {"status": "completed", "error": "", "artifacts": list(stage.artifacts),
                    "files": {name: self._signature(name) for name in stage.artifacts}}
                self.summary["status"] = "completed" if item == self.final_id else "partial"
            except BaseException as exc:
                status = "cancelled" if isinstance(exc, CANCELLED) else (
                    "quality_failed" if isinstance(exc, ValueError) else "failed")
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


def analyze(run_dir: Path, *, workflow_type) -> dict:
    """按给定分析类执行全部节点；运行资格由该类的构造函数校验。"""
    ctx = workflow_type(run_dir)
    return ctx.execute([stage.id for stage in ctx.stages])


def main(*, workflow_type):
    """子进程分析入口：带 step_ids 时只执行所选节点，否则执行全部分析。"""
    payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    if payload.get("step_ids"):
        workflow_type(runtime_run_dir(), payload.get("analysis_parameters")).execute(
            payload["step_ids"], payload.get("revision"))
    else:
        analyze(runtime_run_dir(), workflow_type=workflow_type)
