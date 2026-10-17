# %% [markdown]
# # Z 线圈电感效应频率响应：完整与分步共用编排
# %% 导入
from __future__ import annotations
from pathlib import Path
import sys
from typing import Any, Callable
from uuid import uuid4
import numpy as np
from sds_acquisition import SDSAcquisition, SDSInstrument
from ...common import WorkflowCancelled, find_project_root, load_mapping, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, ScopeAutoRangeState, configure_optimal_control_trigger, synchronize_connected_clocks
from ..noise_spectrum_xy.state_io import write_state
from .models import ZCoilInductanceFrequencyResponseParams
from . import acquisition, preparation
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, validate_selection
# 已发布电流频响仍使用这些导出；采集编排通过 operations 保留替换入口。
from .acquisition import (
    build_frequency_axis, sine_reference, _reference_channel, _scope_config, _sample_rate_for_frequency,
    _scope_settings, _wait_for_scope_trigger, _wait_for_scope_stop, _scope_record_duration_s,
    _capture_frame, _capture_with_auto_range, _falling_edge_time, _save_capture, _sleep_cancellable,
    SCOPE_MEMORY_MANAGEMENT, SCOPE_TRIGGER_MODE, SCOPE_TRIGGER_SOURCE, SCOPE_TRIGGER_SLOPE,
    SCOPE_COUPLING, SCOPE_IMPEDANCE, SCOPE_PROBE, SCOPE_FRAME_RETRIES, SCOPE_CAPTURE_GUARD_S,
    SCOPE_STOP_TIMEOUT_S, SCOPE_SOFT_RESET_SETTLE_S, SCOPE_TRIGGER_COVERAGE_CYCLES,
)
from .excitation import _configure_sine, Z_BURST_TRIGGER_SLOPE
from .preparation import _connect_devices, safe_shutdown

EXECUTION_MODE = "typed_workflow"


class Workflow:
    """同一硬件会话中重复采集；每轮目录独立，等待期间仍占用硬件锁。"""
    acquisition_stages = ACQUISITION
    finish_id = FINISH_ID
    preflight = staticmethod(preparation.preflight)

    def __init__(self, params, *, project_root=None, session_id=None, experiment_id=EXPERIMENT_ID,
            data_type=DATA_TYPE, acquisition_signals=None, physical_limit=None,
            completion_label="Z 线圈电感效应频率响应", capture_validator=None,
            capture_guard_s=None, drive_metadata=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping, self.limits = self.preflight(params, self.root)
        self.experiment_id, self.data_type = experiment_id, data_type
        self.acquisition_signals = acquisition_signals or ["SDS CH3 Z coil voltage", "SDS CH4 trigger reference"]
        self.physical_limit = physical_limit or "仅观察线圈端电压频率响应；理论值为 DG 设定值，不能单独反演唯一 L。"
        self.completion_label, self.capture_validator = completion_label, capture_validator
        self.capture_guard_s = (float(getattr(params, "scope_capture_guard_s", SCOPE_CAPTURE_GUARD_S))
            if capture_guard_s is None else float(capture_guard_s))
        if not np.isfinite(self.capture_guard_s) or self.capture_guard_s < 0:
            raise ValueError("capture_guard_s 必须是有限非负秒数")
        self.drive_metadata = drive_metadata or {}
        self.operations = sys.modules[__name__]
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.acquisition_stages)
        self.connection = DeviceSession()
        self.devices, self.channels, self.clock_sources = {}, {}, {}
        self.scope_config = self.scope_snapshot = self.run_dir = self.run_directory = self.current_step = None
        self.closed = self.outputs_started = self.clock_failed = False
        self.outputs = {"Z 正弦": "未知", "共同触发": "未知", "Scope": "未知"}
        self.shutdown, self.persistence_errors = None, []

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
            (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source="最近成功模块的写入结果；Scope 设置另有实际回读",
            demod_mode="scope" if self.scope_config is not None else "unknown",
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources, daq_settings=self.scope_snapshot)

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
        if unknown := values.keys() - previous.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping, limits = self.preflight(type(self.params).from_external(previous | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != previous[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.limits = params, mapping, limits
        self.revision += 1
        self.publish("参数已应用；请显式重跑失效步骤")

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
        elif item == "A03":
            self.clock_failed = False
            try:
                self.clock_sources = synchronize_connected_clocks({"z_control": self.devices["z_control"]},
                    self.mapping, {"z_control": "Z_magnetic_field"}, settle_s=0.2)
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_started = True
            configure_optimal_control_trigger(self.params, self.devices["z_control"], self.channels["trigger"], output=False)
            _configure_sine(self.devices["z_control"], self.channels["z_field"], self.params,
                float(build_frequency_axis(self.params)[0]), output=False)
            self.outputs.update({"Z 正弦": "外部下降沿 Burst 已配置 / OFF", "共同触发": "OFF"})
        elif item == "A05":
            self.scope_config, self.scope_snapshot = acquisition.prepare_scope(self.params, self.devices,
                float(build_frequency_axis(self.params)[0]), ScopeAutoRangeState(self.params.scope_initial_scale_v_div, 0.0),
                operations=self.operations)
            self.outputs["Scope"] = "C4 下降沿单次采集已配置"
        elif item == "A06":
            scope = self.devices["scope"]
            if (not str(scope.get_trigger_mode()).strip().upper().startswith("SING")
                    or str(scope.get_trigger_source()).strip().upper() != "C4"):
                invalidate(self.acquisition_stages, self.states, "A05")
                raise ValueError("Scope 模式已改变，请重新执行 A05 Scope 配置")
            self.states[item]["artifacts"] = acquisition.acquire_run(self)

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(self.persistence_errors)
        try:
            if self.devices:
                report = safe_shutdown(self.devices, self.channels)
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                self.outputs.update({"Z 正弦": "DC 0 V / OFF", "共同触发": "保持最后状态", "Scope": "STOP"}
                    if report.completed else {key: "未知" for key in self.outputs})
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


# %% 完整运行与兼容入口
def run_frequency_response(params: ZCoilInductanceFrequencyResponseParams, *, experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE, acquisition_signals=None, physical_limit=None,
        completion_label="Z 线圈电感效应频率响应", capture_validator: Callable[[dict[str, Any]], None] | None=None,
        capture_guard_s=None, drive_metadata=None, project_root=None) -> Path:
    """保留电流频响的自定义轴、逐帧校验和相干重触发注入契约。"""
    ctx = Workflow(params, project_root=project_root, experiment_id=experiment_id, data_type=data_type,
        acquisition_signals=acquisition_signals, physical_limit=physical_limit, completion_label=completion_label,
        capture_validator=capture_validator, capture_guard_s=capture_guard_s, drive_metadata=drive_metadata)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def run(params: ZCoilInductanceFrequencyResponseParams, *, project_root=None) -> Path:
    return run_frequency_response(params, project_root=project_root)


def main() -> int:
    run(load_runtime_params(ZCoilInductanceFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
