# %% [markdown]
# # Z 任意波线圈波形核验：完整与分步共用编排
# %% 导入
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ...common import find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, synchronize_connected_clocks
from lab_workflows.workflow_state import write_state
from .models import ZAWWaveformScopeCheckParams
from . import acquisition, excitation, preparation
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states
# 已发布的电流与闭环实验继续从此模块导入这些接口。
from .acquisition import (
    _requested_sample_rate, _scope_config, _sleep_cancellable, _wait_for_scope_trigger,
    _wait_for_scope_stop, _scope_record_duration_s, _capture_frame, _capture_with_auto_range,
    _scope_settings, _save_frame, SCOPE_MEMORY_MANAGEMENT, SCOPE_TRIGGER_MODE,
    SCOPE_TRIGGER_SOURCE, SCOPE_TRIGGER_SLOPE, SCOPE_COUPLING, SCOPE_IMPEDANCE, SCOPE_PROBE,
    SCOPE_FRAME_RETRIES, SCOPE_CAPTURE_GUARD_S, SCOPE_STOP_TIMEOUT_S, SCOPE_SOFT_RESET_SETTLE_S,
)
from .preparation import ControlSource, _connect_devices, safe_shutdown
from .excitation import _configure_outputs

EXECUTION_MODE = "typed_workflow"


class Workflow:
    """保持同一硬件会话；采集重跑生成新目录，空闲等待仍占用硬件锁。"""

    acquisition_stages = ACQUISITION
    finish_id = "A06"
    preflight = staticmethod(preparation.preflight)

    def __init__(self, params, *, project_root=None, session_id=None,
                 experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, measurement_mode="coil_voltage",
                 current_calibration=None, theory_override=None, applied_override=None,
                 source_snapshot_writer=None, target_control_scale=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping, self.limits = self.preflight(params, self.root)
        self.experiment_id, self.data_type, self.measurement_mode = experiment_id, data_type, measurement_mode
        self.source_options = dict(measurement_mode=measurement_mode, current_calibration=current_calibration,
            theory_override=theory_override, applied_override=applied_override,
            source_snapshot_writer=source_snapshot_writer, target_control_scale=target_control_scale)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.acquisition_stages)
        self.connection = DeviceSession()
        self.devices, self.channels, self.clock_sources = {}, {}, {}
        self.source = self.scope_config = self.scope_snapshot = self.auto_range = None
        self.run_dir = self.run_directory = self.current_step = None
        self.closed = self.outputs_started = self.clock_failed = False
        self.outputs = {"Z 任意波": "未知", "共同触发": "未知", "Scope": "未知"}
        self.shutdown = None
        self.persistence_errors = []

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source="最近成功模块的写入结果；Scope 配置另有实际回读",
            demod_mode="scope" if self.scope_config is not None else "unknown",
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources,
            daq_settings=self.scope_snapshot)

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.run_dir:
            write_state(self.run_dir / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def _check_configuration(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")

    def recheck(self):
        self._check_configuration()
        self.params, self.mapping, self.limits = self.preflight(self.params, self.root)

    def update_parameters(self, values):
        self._check_configuration()
        previous = self.params.to_external()
        unknown = set(values) - set(previous)
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping, limits = self.preflight(type(self.params).from_external(previous | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != previous[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.limits = params, mapping, limits
        self.revision += 1
        self.publish("参数已应用；请显式重跑已失效的步骤")

    def execute(self, selected):
        selected = validate_selection(self.acquisition_stages, self.states, selected)
        if selected != [self.finish_id]:
            self.recheck()
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                invalidate(self.acquisition_stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            try:
                self.publish(next(stage.title for stage in self.acquisition_stages if stage.id == item))
                self._execute_step(item)
                self.states[item]["status"] = "completed"
            except BaseException as exc:
                self.states[item] = {"status": "failed", "error": str(exc)}
                try:
                    self.publish(str(exc), "error")
                except Exception:
                    pass
                raise
            finally:
                self.current_step = None
            self.publish(f"{item} 已完成")
        return self.snapshot()

    def prepare_control_source(self) -> ControlSource:
        """仅返回已校验的准备结果；源状态提交和仪器设置仍由 A03 负责。"""
        return preparation.prepare_control(self.params, self.root, **self.source_options)

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not self.devices:
                try:
                    self.devices, self.channels = _connect_devices(self.mapping, self.connection)
                except BaseException:
                    self.connection.cleanup_connection_failure()
                    raise
            self.clock_failed = False
            try:
                self.clock_sources = synchronize_connected_clocks({"z_control": self.devices["z_control"]},
                    self.mapping, {"z_control": "Z_magnetic_field"}, settle_s=0.2)
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A03":
            self.source = self.prepare_control_source()
            self.outputs_started = True
            _configure_outputs(self.params, self.devices, self.channels, self.source.theory, self.source.applied)
            self.outputs.update({"Z 任意波": "外部下降沿 Burst ON", "共同触发": "OFF"})
        elif item == "A04":
            self.scope_config, self.scope_snapshot, self.auto_range = acquisition.prepare_scope(
                self.params, self.devices, self.source.theory)
            # Scope 已就绪后才开启共同触发，保持原有首帧协议。
            self.devices["z_control"].set_output(True, channel=self.channels["trigger"])
            self.outputs.update({"共同触发": "ON", "Scope": "C4 下降沿单次采集已配置"})
        elif item == "A05":
            if self.scope_config is None or self.source is None:
                raise ValueError("请先完成任意波和 Scope 配置")
            scope = self.devices["scope"]
            if (not str(scope.get_trigger_mode()).strip().upper().startswith("SING") or
                    str(scope.get_trigger_source()).strip().upper() != "C4"):
                invalidate(self.acquisition_stages, self.states, "A04")
                raise ValueError("Scope 模式已改变，请重新执行 A04 Scope 配置")
            self.states[item]["artifacts"] = acquisition.acquire_run(self)
            self.outputs["Scope"] = "STOP；本轮原始帧已保存"

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(self.persistence_errors)
        try:
            if self.devices and (not self.clock_failed or self.outputs_started):
                report = safe_shutdown(self.devices, self.channels)
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                if report.completed:
                    self.outputs.update({"Z 任意波": "DC 0 V / OFF", "共同触发": "保持最后状态", "Scope": "STOP"})
                else:
                    self.outputs.update({key: "未知" for key in self.outputs})
            elif self.devices:
                self.connection.cleanup_connection_failure()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.closed, self.current_step = True, None
            self.revision += 1
            if self.run_directory is not None:
                try:
                    updates = dict(safety_shutdown=self.shutdown)
                    if errors:
                        updates.update(completion_status="failed", failure_reason="；".join(
                            ([str(failure)] if failure else []) + errors))
                    self.run_directory.update_config(**updates)
                except Exception as exc:
                    errors.append(f"保存安全收尾记录失败：{exc}")
            self.states[self.finish_id] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
            try:
                self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            except Exception:
                if failure is None:
                    raise
        if errors and failure is None:
            raise RuntimeError("安全收尾未全部完成：" + "；".join(errors))


# %% 完整运行与历史兼容入口
def run_scope_check(params: ZAWWaveformScopeCheckParams, *, experiment_id=EXPERIMENT_ID,
                    data_type=DATA_TYPE, measurement_mode="coil_voltage", current_calibration=None,
                    theory_override=None, applied_override=None,
                    source_snapshot_writer: Callable[[Path], list[str]] | None = None,
                    target_control_scale=None, project_root=None) -> Path:
    """保持实际电流实验的旧调用契约，完整运行使用同一组业务步骤。"""
    ctx = Workflow(params, experiment_id=experiment_id, data_type=data_type,
        measurement_mode=measurement_mode, current_calibration=current_calibration,
        theory_override=theory_override, applied_override=applied_override,
        source_snapshot_writer=source_snapshot_writer, target_control_scale=target_control_scale,
        project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ctx.acquisition_stages if stage.id != ctx.finish_id])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def run(params: ZAWWaveformScopeCheckParams, *, project_root=None) -> Path:
    return run_scope_check(params, project_root=project_root)


def main() -> int:
    run(load_runtime_params(ZAWWaveformScopeCheckParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
