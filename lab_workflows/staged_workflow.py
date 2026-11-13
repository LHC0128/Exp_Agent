"""分步实验共用骨架：采集会话编排、离线分析编排与串行会话入口。

实验子类只声明清单与类型，并实现节点动作和安全收尾；修订、快照、发布、参数失效、
运行目录轮换、执行循环、分析指纹与归档在此统一。导入不连接硬件。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar
from uuid import uuid4

from .common import WorkflowCancelled, data_root, find_project_root
from .experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress, runtime_run_dir
from .instrument_config import instrument_config_snapshot
from .steps import create_run_directory
from .workflow_stages import DependencyRules, Stage, initial_states
from .workflow_state import read_state, write_state

CANCELLED = (WorkflowCancelled, KeyboardInterrupt)


class StagedWorkflow:
    """采集会话。子类必填：stages、finish_id、run_id、run_format、output_keys，实现 preflight、run_step、shutdown。

    封存型（seal_after_run=True）：run_id 完成后由 after_step 调用 seal_round()，本轮目录不再写入；
    模块型（seal_after_run=False）：每个测量模块完成即把 completion_status 写为 completed。
    """

    stages: ClassVar[tuple[Stage, ...]]
    finish_id: ClassVar[str]
    run_id: ClassVar[str | None] = None
    run_format: ClassVar[Any]
    output_keys: ClassVar[tuple[str, ...]] = ()
    preflight_id: ClassVar[str] = "A01"
    hardware_free: ClassVar[tuple[str, ...]] = ("A01",)
    seal_after_run: ClassVar[bool] = True
    new_directory_on_rerun: ClassVar[bool] = True
    inherited_state_keys: ClassVar[tuple[str, ...]] = ("clock_sources",)
    quality_errors: ClassVar[tuple[type[BaseException], ...]] = ()
    output_source: ClassVar[str] = "最近成功模块的写入结果；未经额外回读"
    finished_message: ClassVar[str] = "会话已结束"

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = self.preflight(params)
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.rules = DependencyRules(finish_id=self.finish_id)
        self.states = initial_states(self.stages)
        self.devices: dict[str, Any] = {}
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self._just_sealed = False
        self.state = self.run_format.initial_metadata(self.params)
        self.outputs = {key: "未知" for key in self.output_keys}

    # ---- 子类实现 ----
    def preflight(self, params):
        """无硬件预检，返回 (params, mapping)。"""
        raise NotImplementedError

    def run_step(self, item: str) -> None:
        """执行一个采集节点（不含 preflight_id 与 finish_id）。"""
        raise NotImplementedError

    def shutdown(self, failure: BaseException | None) -> list[str]:
        """结束会话的安全收尾与断开；返回全部错误，不抛出。"""
        raise NotImplementedError

    # ---- 可选钩子 ----
    def extra_snapshot(self) -> dict[str, Any]:
        return {}

    def validate_update(self, params) -> None:
        """应用参数前的额外拒绝条件（如已连接设备时不可切换的开关）。"""

    def before_step(self, item: str) -> None:
        if not self.seal_after_run and item not in self.hardware_free:
            self.state.update(completion_status="running", failure_reason=None)
            self.save()

    def after_step(self, item: str) -> None:
        if not self.seal_after_run and item not in self.hardware_free:
            self.state.update(completion_status="completed")

    def on_new_directory(self, previous, old_state: dict[str, Any]) -> None:
        """新目录建立后、首次保存前调用；可复制仍有效的前置数据。"""

    def final_status(self, failure: BaseException | None, errors: list[str]) -> None:
        """结束时写入运行目录的 completion_status。"""
        if self.seal_after_run:
            if not self.round_sealed:
                self.state.update(completion_status="cancelled" if isinstance(failure, CANCELLED) else "failed",
                                  failure_reason=str(failure) if failure else self.state.get("failure_reason", "本轮尚未完整采集"))
            return
        if failure is not None and self.state.get("completion_status") not in ("failed", "cancelled"):
            self.state.update(completion_status="cancelled" if isinstance(failure, CANCELLED) else "failed",
                              failure_reason=str(failure))
        elif errors:
            self.state.update(completion_status="failed", failure_reason="安全收尾失败：" + "；".join(errors))
        elif self.state.get("completion_status") == "running":
            self.state.update(completion_status="failed", failure_reason="会话结束时仍有模块未完成")

    # ---- 共用实现 ----
    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise ValueError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = self.preflight(self.params)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
                    status="closed" if self.closed else "running" if self.current_step else "waiting",
                    parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
                    run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
                    output_source=self.output_source, safety_shutdown=self.state.get("safety_shutdown"),
                    clock_sources=self.state.get("clock_sources", {}), **self.extra_snapshot())

    def publish(self, message="等待选择下一模块"):
        value = self.snapshot()
        write_state(data_root(self.root) / ".gui_jobs" / self.session_id / "session.json", value)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("执行中不能应用参数")
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = self.preflight(type(self.params).from_external(old | values))
        self.validate_update(params)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                self.rules.invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑失效模块，不会自动设置输出")

    def _needs_new_directory(self, selected):
        if not [s for s in selected if s not in (*self.hardware_free, self.finish_id)]:
            return False
        rerun = (self.new_directory_on_rerun and self.run_id in selected
                 and self.states[self.run_id]["status"] != "pending")
        return (self.directory is None or self.round_sealed or rerun
                or self.run_parameters != self.params.to_external())

    def _new_directory(self):
        previous, old_state = self.directory, self.state
        if previous and not self.round_sealed and old_state.get("completion_status") == "running":
            old_state.update(completion_status="failed", failure_reason="本轮被重跑替代，已有数据保留")
            self.save()
        self.directory = create_run_directory(self.run_format.DATA_TYPE, self.params.run_tag, self.params.to_external(),
                                              schema_version=self.params.schema_version, project_root=self.root)
        self.run_parameters, self.round_sealed = self.params.to_external(), False
        self.state = self.run_format.initial_metadata(self.params)
        for key in self.inherited_state_keys:
            if key in old_state:
                self.state[key] = old_state[key]
        self.on_new_directory(previous, old_state)
        self.save()

    def seal_round(self, errors: list[str]) -> None:
        """封存型实验在运行节点收尾后调用：收尾失败抛出，成功则本轮目录不再写入。"""
        if errors:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))
        self.state.update(completion_status="completed", failure_reason=None)
        self.save()
        self.round_sealed = self._just_sealed = True

    def validate(self, selected):
        selected = self.rules.validate_selection(self.stages, self.states, selected)
        if self.finish_id in selected and selected[-1] != self.finish_id:
            raise ValueError("安全结束必须最后执行")
        return selected

    def execute(self, selected):
        selected = self.validate(selected)
        if selected != [self.finish_id]:
            self.recheck()
        if self._needs_new_directory(selected):
            self._new_directory()
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != self.preflight_id:
                self.rules.invalidate(self.stages, self.states, item)
            self.current_step = item
            self.states[item] = dict(status="running", error="")
            failure = None
            try:
                self.before_step(item)
                self.publish(next(s.title for s in self.stages if s.id == item))
                if item == self.preflight_id:
                    self.recheck()
                else:
                    self.run_step(item)
                self.states[item] = dict(status="completed", error="")
                self.after_step(item)
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, CANCELLED) else (
                    "quality_failed" if isinstance(exc, self.quality_errors) else "failed")
                self.states[item] = dict(status=status, error=str(exc))
                self.state.update(completion_status="cancelled" if status == "cancelled" else "failed",
                                  failure_reason=str(exc))
                try:
                    self.save()
                except Exception as save_error:
                    self.state["state_save_error"] = str(save_error)
                raise
            finally:
                self.current_step = None
                try:
                    if self._just_sealed and failure is None:
                        write_state(self.directory.root / "acquisition_state.json", self.snapshot())
                    self._just_sealed = False
                    self.save()
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(self.shutdown(failure))
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[self.finish_id] = dict(status="failed" if errors else "completed", error="；".join(errors))
        self.final_status(failure, errors)
        try:
            self.save()
            if errors and self.round_sealed:
                self.directory.update_config(completion_status="failed", failure_reason="；".join(errors),
                                             safety_shutdown=self.state.get("safety_shutdown"))
            self.publish(self.finished_message)
            report_runtime_progress("finished", "采集会话结束", 100, estimated_remaining_seconds=None)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))

    # ---- 入口 ----
    @classmethod
    def run(cls, params, *, project_root=None) -> Path:
        ctx, failure = cls(params, project_root=project_root), None
        try:
            ctx.execute([s.id for s in cls.stages if s.id != cls.finish_id])
            return ctx.directory.root
        except BaseException as exc:
            failure = exc
            raise
        finally:
            ctx.finish(failure)


class StagedAnalysis:
    """离线分析。子类必填：stages、analysis_keys、steps（含 output_path/load_config/resolve_options/
    signature/fingerprint）、final_id、label，实现 run_step；可选 prepare、extra_outputs。"""

    stages: ClassVar[tuple[Stage, ...]]
    analysis_keys: ClassVar[tuple[str, ...]]
    steps: ClassVar[Any]
    final_id: ClassVar[str]
    label: ClassVar[str] = "分析"
    first_id: ClassVar[str] = "B01"

    def __init__(self, run_dir, analysis_parameters=None):
        self.rules = DependencyRules(finish_id=None)
        self.run_dir = Path(run_dir).resolve()
        self.results = self.steps.output_path(self.run_dir, "results")
        self.state_path = self.steps.output_path(self.run_dir, "analysis_state.json")
        self.config, self.base_params = self.steps.load_config(self.run_dir)
        previous = read_state(self.state_path) or {}
        self.states = initial_states(self.stages) | deepcopy(previous.get("steps", {}))
        self.revision = previous.get("revision", 0)
        self.summary = deepcopy(previous.get("summary", {}))
        self.fingerprint = previous.get("fingerprint") or self.steps.fingerprint(self.run_dir)
        self.options = {key: self.base_params.to_external()[key] for key in self.analysis_keys}
        self.options.update({k: v for k, v in previous.get("options", {}).items() if k in self.analysis_keys})
        before = dict(self.options)
        self.params = self.steps.resolve_options(self.base_params, self.options | (analysis_parameters or {}))
        self.options = {key: self.params.to_external()[key] for key in self.analysis_keys}
        for stage in self.stages:
            if any(self.options[key] != before[key] for key in stage.parameters):
                self.rules.invalidate(self.stages, self.states, stage.id)
        self._refresh()

    # ---- 子类实现 ----
    def run_step(self, item: str) -> dict[str, Any]:
        raise NotImplementedError

    def prepare(self, selected: list[str]) -> None:
        """执行前读取并核对输入；默认不预读。"""

    def extra_outputs(self, item: str) -> set[str]:
        """清单外、由该节点产生且重算时需要归档的结果文件名。"""
        return set()

    # ---- 共用实现 ----
    def _stage(self, item):
        return next(s for s in self.stages if s.id == item)

    def _path(self, name):
        return self.steps.output_path(self.run_dir, "results/" + name)

    def _signature(self, name):
        return self.steps.signature(self._path(name))

    def _refresh(self):
        current = self.steps.fingerprint(self.run_dir)
        if current != self.fingerprint:
            self.fingerprint = current
            self.rules.invalidate(self.stages, self.states, self.first_id)
            self.revision += 1
        for stage in self.stages:
            record = self.states[stage.id]
            names = record.get("files", {})
            if record["status"] == "running" or (record["status"] == "completed" and (
                    not set(stage.artifacts).issubset(names) or any(self._signature(n) != s for n, s in names.items()))):
                self.rules.invalidate(self.stages, self.states, stage.id)
                self.revision += 1
                break
        if any(record["status"] == "stale" for record in self.states.values()):
            self.summary.update(status="stale")
        elif not self.summary:
            self.summary.update(status="not_started")

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=dict(self.options),
                    steps=deepcopy(self.states), fingerprint=deepcopy(self.fingerprint), summary=deepcopy(self.summary))

    def persist(self):
        write_state(self.state_path, self.snapshot())
        report_runtime_progress("analysis", f"{self.label}状态已保存", data={"analysis_workflow": self.snapshot()})

    def _outputs(self, item):
        names = set(self._stage(item).artifacts) | set(self.states[item].get("files", {})) | self.extra_outputs(item)
        return [path for name in sorted(names) if (path := self._path(name)).is_file()]

    def _archive(self, item):
        paths = sorted({p for stage in self.rules.invalidated_stages(self.stages, item) for p in self._outputs(stage.id)})
        if paths:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in paths:
                path.replace(backup / path.name)
            self.summary["backup_directory"] = backup.name

    def execute(self, selected, expected_revision=None):
        self._refresh()
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析修订已改变，请刷新后重试")
        selected = self.rules.validate_selection(self.stages, self.states, selected)
        self.revision += 1
        item = selected[0]
        try:
            self.prepare(selected)
            if self.steps.fingerprint(self.run_dir) != self.fingerprint:
                raise ValueError(f"分析输入读取期间发生变化，请重新执行 {self.first_id}")
            self.results.mkdir(exist_ok=True)
            for item in selected:
                check_cancelled()
                self._archive(item)
                self.rules.invalidate(self.stages, self.states, item)
                self.states[item] = dict(status="running", error="")
                self.summary.update(status="running")
                self.summary.pop("error", None)
                self.persist()
                result = self.run_step(item)
                if self.steps.fingerprint(self.run_dir) != self.fingerprint:
                    raise ValueError(f"分析输入计算期间发生变化，请重新执行 {self.first_id}")
                stage = self._stage(item)
                required = set(stage.artifacts) | self.extra_outputs(item)
                artifacts = [p.name for p in self._outputs(item)]
                if not required.issubset(artifacts):
                    raise RuntimeError(f"{item} 缺少必需分析产物")
                parameters = {key: self.options[key] for key in stage.parameters}
                self.states[item] = dict(status="completed", error="", artifacts=artifacts,
                    files={name: self._signature(name) for name in artifacts}, parameters=parameters,
                    parameter_fingerprint=hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest(),
                    revision=self.revision, input_fingerprint=deepcopy(self.fingerprint))
                self.summary.update(result or {}, status="completed" if item == self.final_id else "partial")
                self.persist()
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, CANCELLED) else (
                "quality_failed" if isinstance(exc, ValueError) else "failed")
            self.rules.invalidate(self.stages, self.states, item)
            self.states[item] = dict(status=status, error=str(exc))
            self.summary.update(status=status, error=str(exc))
            if status == "quality_failed":
                self.summary["quality_passed"] = False
            try:
                self.persist()
            except Exception as save_error:
                exc.add_note(f"分析诊断保存亦失败：{save_error}")
            raise
        return deepcopy(self.summary)

    @classmethod
    def analyze(cls, run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
        ctx = cls(run_dir, analysis_parameters)
        return ctx.execute([s.id for s in cls.stages] if step_ids is None else step_ids, expected_revision)

    @classmethod
    def main(cls):
        payload = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
        cls.analyze(runtime_run_dir(), step_ids=payload.get("step_ids"),
                    analysis_parameters=payload.get("analysis_parameters"), expected_revision=payload.get("revision"))
        return 0


def serve_session(workflow_type: type[StagedWorkflow], params_type, stream=None,
                  factory: Callable[..., StagedWorkflow] | None = None) -> None:
    """GUI 分步会话子进程入口：读取参数与会话号，按清单串行执行命令。"""
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id")
    os.environ["LAB_TYPED_PARAMETERS"] = json.dumps(payload)
    serve_workflow((factory or workflow_type)(load_runtime_params(params_type), session_id=session_id), stream or sys.stdin)


def serve_workflow(ctx: StagedWorkflow, stream) -> None:
    """对已构造的会话执行串行命令协议（测试子进程可直接传入假仪器会话）。"""
    from .workflow_session import serve

    serve(ctx, stream, stages=ctx.stages, validate=lambda stages, states, selected: ctx.validate(selected),
          finish_id=ctx.finish_id)
