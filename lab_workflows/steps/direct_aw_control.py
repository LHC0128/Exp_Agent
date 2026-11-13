"""DirectAW 家族的单控制采集引擎。

会话状态、修订、快照、发布、保存、运行目录轮换、执行循环与结束都在此统一；
实验侧只声明阶段清单与运行格式、实现节点动作和安全收尾。导入不连接硬件。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4
import shutil

from ..common import WorkflowCancelled, data_root, find_project_root
from ..experiment_runtime import report_runtime_progress
from ..staged_workflow import StagedWorkflow
from ..workflow_stages import DependencyRules, initial_states
from ..workflow_state import write_state
from . import direct_aw_state
from .direct_aw_phase import CalibrationQualityError
from .run_directory import create_run_directory

# 新目录沿用仍有效的前置状态；分析键不在此列，避免把旧结论带进新轮次。
_INHERITED_STATE_KEYS = ("calibration", "actual_rates", "clock_sync", "temperature_control", "optional_connection_errors",
    "xy_aw_output", "hf2_response_snapshot", "response_output_snapshot", "response_acquisition",
    "stage_frequencies", "hf2_calibration_snapshot", "calibration_output_snapshot",
    "hf2_noise_snapshot", "noise_output_snapshot", "noise_acquisition",
    "hf2_bandwidth_snapshot", "bandwidth_output_snapshot", "bandwidth_acquisition")
# 校相阶段失败后本轮不得再声称校相通过。
_PHASE_STAGES = ("A08", "A09", "A11", "A12")


class DirectAWControlRun(StagedWorkflow):
    """一个设备会话可重复采集；每轮参数和原始数据独立保存。

    子类声明 stages/finish_id/run_id/identity/output_keys，实现 preflight、
    _execute_step 与 shutdown；identity 为运行格式模块（EXPERIMENT_ID 等常量）。
    """

    identity: ClassVar[Any] = None
    output_keys: ClassVar[tuple[str, ...]] = ("X", "Y", "Z", "温控", "Pump", "共用触发")
    quality_errors: ClassVar[tuple[type[BaseException], ...]] = (CalibrationQualityError,)
    # 取消只认显式取消异常；KeyboardInterrupt 仍落 failed。
    cancelled_errors: ClassVar[tuple[type[BaseException], ...]] = (WorkflowCancelled,)
    # 封存后不再回写失败状态：已完成轮次的运行配置保持原样。
    sealed_failure_write_back: ClassVar[bool] = False
    # 分析会写回这两个键，采集保存不得把它们冲回初值。
    state_write_exclusions: ClassVar[tuple[str, ...]] = ("analysis_status", "analysis_error")
    phase_stage_ids: ClassVar[tuple[str, ...]] = _PHASE_STAGES

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = self._preflight_state(params)
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.rules = DependencyRules(finish_id=self.finish_id)
        self.states = initial_states(self.stages)
        self.devices: dict[str, Any] = {}
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self._just_sealed = False
        self.outputs_safe = False
        self.outputs = {key: "未知" for key in self.output_keys}
        # self.state 由实验在会话开始时按 identity 写入 direct_aw_state.initial_state；
        # 目录轮换时由本模块按同一入口重建。
        self.quality_error = None
        # 校相与响应状态由实验节点写入，控制子运行执行时按阶段共享。
        self.demod_mode = "unknown"
        self.d0_config = self.phase0 = self.xy_phase = self.response_config = None
        self.z_positive_phase = None

    # ---- 子类实现 ----
    def _execute_step(self, item: str) -> None:
        """执行一个采集节点（不含 preflight_id 与 finish_id）。"""
        raise NotImplementedError

    def shutdown(self, failure: BaseException | None) -> list[str]:
        """结束会话的安全收尾与断开；返回全部错误，不抛出。"""
        raise NotImplementedError

    # ---- 可选钩子 ----
    def on_quality_failure(self) -> None:
        """质量失败后在原会话内恢复可重试的物理状态；默认无需动作。"""

    def _preflight_state(self, params):
        """骨架只使用参数与映射；预检第 3 项 envelope 保留在实例上供节点使用。"""
        current, mapping, envelope = self.preflight(params)
        self.envelope = envelope
        return current, mapping

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = self._preflight_state(self.params)

    # ---- 共用实现 ----
    @property
    def sealed(self) -> bool:
        """本轮已封存：输出安全且采集完成；封存后不再写运行配置。"""
        return bool(self.outputs_safe and self.state.get("completion_status") == "completed")

    def snapshot(self):
        value = super().snapshot()
        # 本家族的时钟源状态键名为 clock_sync；GUI 键集钉死 clock_sources。
        value["clock_sources"] = self.state.get("clock_sync", {})
        return value

    def extra_snapshot(self):
        return dict(demod_mode=self.demod_mode,
            xy_phase_calibration=self.state["xy_phase_calibration"],
            rf_phase_calibration=self.state["rf_phase_calibration"],
            demod_phase_deg=self.state["calibration"].get("demod0", {}).get("phase_shift_deg"),
            daq_settings=self.state.get("response_acquisition"))

    def publish(self, message="等待选择下一模块", level="info"):
        """发布会话快照；封存后 acquisition_state.json 仍按当前状态刷新。"""
        value = self.snapshot()
        write_state(data_root(self.root) / ".gui_jobs" / self.session_id / "session.json", value)
        if self.directory:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": value, "level": level})

    def save(self):
        """采集保存运行配置；分析写回的状态键不参与覆盖。"""
        if self.directory:
            self.directory.update_config(**{key: value for key, value in self.state.items()
                if key not in self.state_write_exclusions})

    def update_parameters(self, values):
        if normalize := getattr(self.params_type, "normalize_control_selection", None):
            values = normalize(values)
        super().update_parameters(values)

    def _needs_new_directory(self, selected):
        hardware = [item for item in selected if item not in (self.preflight_id, self.finish_id)]
        if not hardware:
            return False
        order = [stage.id for stage in self.stages]
        return bool(
            self.directory is None
            or (any(order.index("A04") <= order.index(item) <= order.index("A13") for item in hardware)
                and self.state["response_points_completed"] > 0)
            or (any(item != self.run_id for item in hardware) and self.run_parameters != self.params.to_external())
            or any(self.states[item]["status"] in ("completed", "quality_failed", "failed", "stale")
                   for item in hardware if item in ("A09", "A11", "A13B", "A14")))

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and self.state["completion_status"] in ("running", "acquired"):
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据",
                              finished_at=datetime.now().isoformat())
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag if not suffix else f"{self.params.run_tag}_{suffix:02d}"
                self.directory = create_run_directory(self.identity.DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.state = direct_aw_state.initial_state(self.params, identity=self.identity)
        self.run_parameters = self.params.to_external()
        # 校准副本明确记录来源，旧目录和原始数据保持原样。
        if previous:
            affected = {stage.id for item in selected if item != self.preflight_id
                        for stage in self.rules.invalidated_stages(self.stages, item)}
            for key in _INHERITED_STATE_KEYS:
                if key in inherited:
                    self.state[key] = inherited[key]
            for stage_id, name in (("A13", "response"), ("A12", "calibration"), ("A14", "noise"), ("A13B", "bandwidth")):
                if stage_id in affected:
                    self.state.get("stage_frequencies", {}).pop(name, None)
                    self.state.pop(f"hf2_{name}_snapshot", None)
                    self.state.pop(f"{name}_output_snapshot", None)
                    self.state.pop(f"{name}_acquisition", None)
            for stage_id, key in (("A09", "xy_phase_calibration"), ("A11", "rf_phase_calibration"), ("A13", "response_points_completed"), ("A14", "noise_records_completed"), ("A13B", "bandwidth_points_completed")):
                if stage_id in self.states and stage_id not in affected and self.states[stage_id]["status"] == "completed":
                    self.state[key] = inherited[key]
                    stage = next(item for item in self.stages if item.id == stage_id)
                    for name in stage.artifacts:
                        if name.startswith(("raw/", "results/")):
                            for source in previous.root.glob(name):
                                if source.is_file():
                                    relative = source.relative_to(previous.root)
                                    shutil.copy2(source, self.directory.root / relative)
                                    if name.startswith("raw/"):
                                        self.state["data_files"].append(relative.as_posix())
            if "A12" not in affected and self.states["A12"]["status"] == "completed":
                self.state["phase_calibration_status"] = "completed"
            self.state["calibration_source_run_id"] = previous.root.name
            self.state["calibration"]["z_v_to_nT"] = self.params.z_v_to_nt
        # 新目录沿用实验初始键集（含 analysis_status 初值），不套用采集保存的键过滤。
        self.directory.update_config(run_tag=self.params.run_tag, **self.state)

    def run_step(self, item):
        """执行一个采集节点；节点动作由实验的 _execute_step 实现。"""
        try:
            self._execute_step(item)
        except BaseException as exc:
            self._record_step_failure(item, exc)
            if not self._is_quality_failure(item, exc):
                raise
            self.quality_error = exc
            self.on_quality_failure()

    def _is_quality_failure(self, item, exc):
        return isinstance(exc, self.quality_errors)

    def _record_step_failure(self, item, exc):
        """节点失败：记录终态与校相状态、把输出显示置为未知，并尽力保存诊断。"""
        quality = self._is_quality_failure(item, exc)
        self.states[item] = dict(status="quality_failed" if quality else "failed", error=str(exc))
        if item in self.phase_stage_ids:
            self.state["phase_calibration_status"] = "failed"
        try:
            self.save()
        except Exception as error:
            self.state["state_save_error"] = str(error)
        self.outputs = {key: "未知" for key in self.outputs}

    def before_step(self, item):
        super().before_step(item)
        # 质量失败在本轮后不再重抛；下次开始执行时清空上一轮的待重抛异常。
        self.quality_error = None

    def after_step(self, item):
        super().after_step(item)
        # 节点产物按声明清单在完成时登记，供会话与历史界面直接显示。
        if self.states[item]["status"] == "completed" and self.directory:
            stage = next(stage for stage in self.stages if stage.id == item)
            self.states[item]["artifacts"] = [
                str(path.relative_to(self.directory.root)).replace("\\", "/")
                for name in stage.artifacts for path in self.directory.root.glob(name)]

    def seal_round(self, errors: list[str]) -> None:
        """本轮收尾成功后封存：只置封存标记，不再额外写快照。"""
        if errors:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))
        self.state.update(completion_status="completed", failure_reason=None)
        self.save()
        self.round_sealed = True

    def final_status(self, failure, errors):
        sealed = self.sealed
        if failure is not None and not sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, self.cancelled_errors) else "failed",
                              failure_reason=str(failure))
            if self.state["phase_calibration_status"] == "running":
                self.state["phase_calibration_status"] = "failed"
        elif errors and not sealed:
            self.state.update(completion_status="failed", failure_reason="安全收尾失败：" + "；".join(errors))
        elif self.states[self.run_id]["status"] == "completed":
            self.state["completion_status"] = "completed"
        elif self.directory:
            self.state.update(completion_status="failed", failure_reason="本轮采集未完整完成，仅可查看诊断")
        if not sealed:
            self.state["finished_at"] = datetime.now().isoformat()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(self.shutdown(failure))
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[self.finish_id] = dict(status="failed" if errors else "completed", error="；".join(errors))
        self.final_status(failure, errors)
        try:
            # 已完成轮次保持快照不变，结束会话不覆盖并行离线分析的记录。
            if not self.sealed:
                self.save()
            if errors and self.sealed and self.sealed_failure_write_back:
                self.directory.update_config(completion_status="failed", failure_reason="；".join(errors),
                                             safety_shutdown=self.state.get("safety_shutdown"))
            self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            report_runtime_progress("finished", f"采集{self.state['completion_status']}", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))

    @classmethod
    def run(cls, params, *, project_root=None):
        """完整采集：质量失败在安全收尾之后重抛，保证本轮仍写入诊断。"""
        ctx, failure = cls(params, project_root=project_root), None
        try:
            ctx.execute([stage.id for stage in cls.stages if stage.id != cls.finish_id])
            if ctx.quality_error is not None:
                raise ctx.quality_error
            return ctx.directory.root
        except BaseException as exc:
            failure = exc
            raise
        finally:
            ctx.finish(failure)
