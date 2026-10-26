"""T2 完整与分步共用的编排；具体设备配置和逐帧采集在职责模块。"""
from copy import deepcopy
from pathlib import Path
import time
from uuid import uuid4

import numpy as np

from ...common import find_project_root, load_mapping, WorkflowCancelled, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot, validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import T2 as REQUIRED_MAPPING_KEYS
from ...steps import (DeviceSession, create_run_directory, set_temperature_switch,
                      synchronize_connected_clocks, disconnect_device_mapping)
from lab_workflows.workflow_state import write_state
from .models import T2CalibrationParams
from . import preparation, acquisition
from .run_metadata import EXPERIMENT_ID, DATA_TYPE, EXECUTION_MODE, initial_metadata
from .stages import ACQUISITION, RUN_STEP_ID, FINISH_ID, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

# 保留原具名注入点，既有故障回归继续经过真实采集与真实写盘。
safe_shutdown = preparation.safe_shutdown
_connect_devices = preparation._connect_devices
_device_snapshot = preparation._device_snapshot
_configure_outputs = preparation._configure_outputs
_AutoRangeState = acquisition._AutoRangeState


def _sleep(seconds):
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _configure_scope(params, devices):
    return preparation._configure_scope(params, devices, wait=_sleep)


def _acquire_fid(*args, **kwargs):
    return acquisition._acquire_fid(*args, **kwargs, wait=_sleep,
        switch=set_temperature_switch, cancel=check_cancelled)


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    mapping = load_mapping(root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preflight(params, self.root)
        self.configuration_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.current_step = self.scope_config = self.auto_range = None
        self.closed = self.acquired = self.clock_failed = False
        self.state = initial_metadata(params)
        self.run_parameters = None
        self.outputs = {key: "未知" for key in ("温控", "X/Y/Z", "RF 门控", "示波器")}

    def _configuration_revision(self):
        value = instrument_config_snapshot(self.root)
        return (value["device_library_revision"], value["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.configuration_revision:
            raise ValueError("设备库、映射或安全限值已改变，请先结束会话")
        self.params, self.mapping = preflight(self.params, self.root)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功写入值；采样率和点数已回读，其余未经额外回读", demod_mode="SDS",
            daq_settings=self.state.get("hardware_configuration", {}).get("scope"),
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}))

    def publish(self, message="等待选择下一模块；硬件锁和当前输出保持"):
        value = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", value)
        if self.directory:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("执行中不能应用参数")
        self.recheck()
        before = self.params.to_external()
        if unknown := values.keys() - before.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preflight(type(self.params).from_external(before | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != before[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        claimed = {key for stage in self.stages for key in stage.parameters}
        if changed - claimed:
            invalidate(self.stages, self.states, "A04")
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("草稿已应用；必须显式重跑失效模块，不自动写入仪器")

    def _new_directory(self):
        if self.directory:
            self.directory.update_config(completion_status="failed", failure_reason="本轮被显式重跑替代；已读数据保留")
        inherited = {k: deepcopy(self.state[k]) for k in
            ("device_snapshot", "clock_sources", "hardware_configuration", "actual_rates") if k in self.state}
        self.directory = create_run_directory(DATA_TYPE, self.params.run_tag, self.params.to_external(),
            schema_version=self.params.schema_version, project_root=self.root)
        self.state = initial_metadata(self.params) | inherited
        self.directory.update_config(**self.state)
        self.run_parameters, self.acquired = self.params.to_external(), False

    def acquire_fid(self, *args, **kwargs):
        return _acquire_fid(*args, **kwargs)

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        if any(item not in ("A01", FINISH_ID) for item in selected) and (
                self.directory is None or self.run_parameters != self.params.to_external()
                or RUN_STEP_ID in selected and self.acquired):
            self._new_directory()
        self.revision += 1
        for item in selected:
            if item == FINISH_ID:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                invalidate(self.stages, self.states, item)
            self.current_step = item
            self.states[item] = dict(status="running", error="")
            failure = None
            try:
                self.publish(next(stage.title for stage in self.stages if stage.id == item))
                self._execute_step(item)
                self.states[item] = dict(status="completed", error="")
            except BaseException as exc:
                failure = exc
                self.states[item] = dict(status="cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed", error=str(exc))
                raise
            finally:
                self.current_step = None
                try:
                    self.publish()
                except Exception as secondary:
                    if failure is None:
                        raise
                    getattr(failure, "add_note", print)(f"状态保存失败：{secondary}")
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not self.devices:
                try:
                    self.devices, self.channels = _connect_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
        elif item == "A03":
            try:
                self.state["clock_sources"] = synchronize_connected_clocks(self.devices, self.mapping, {
                    "x_field": "X_magnetic_field", "y_field": "Y_magnetic_field", "z_field": "Z_magnetic_field",
                    "laser": "Pump_laser_power", "rf_switch": "Pump_modulation", "temp_switch": "Temp_Switch", "hf2": "lockin_xy"})
                for semantic, key in (("x_field", "X_magnetic_field"), ("y_field", "Y_magnetic_field"), ("z_field", "Z_magnetic_field")):
                    validate_safety_limit(key, 0.0)
                    self.devices[semantic].set_output(False, channel=self.channels[semantic])
                set_temperature_switch(self.devices["temp_switch"], True, channel=self.channels["temp_switch"])
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
            self.state["device_snapshot"] = _device_snapshot(self.mapping, self.devices)
            self.outputs.update({"温控": "5 V DC + ON", "X/Y/Z": "OFF"})
        elif item == "A04":
            self.state["hardware_configuration"] = _configure_outputs(self.params, self.devices, self.channels)
            self.outputs["RF 门控"] = "按当前参数输出；Burst 保持"
        elif item == "A05":
            self.scope_config, snapshot = _configure_scope(self.params, self.devices)
            self.state["hardware_configuration"]["scope"] = snapshot
            self.state["actual_rates"] = dict(hf2_demod_Sa_s=self.state["hardware_configuration"]["hf2"]["actual_rate_readback_Sa_s"],
                scope_Sa_s=snapshot["actual_sample_rate_Sa_s"])
            self.auto_range = _AutoRangeState(float(self.scope_config.channels[self.params.scope_pd_channel - 1].scale))
        elif item == RUN_STEP_ID:
            self.state["data_files"] = acquisition.acquire_scan(self)
            self.acquired = True
        self.directory.update_config(**self.state) if self.directory else None

    def finish(self, failure=None):
        if self.closed:
            return
        cleanup_failure = None
        try:
            report = safe_shutdown(self.devices, self.channels, self.params)
            self.state.update(safety_shutdown=report.to_dict(), device_disconnect=dict(
                tec_disconnected=not report.disconnect_errors, other_devices_preserved=True, errors=list(report.disconnect_errors)))
            if report.errors:
                cleanup_failure = RuntimeError("安全关闭失败: " + "；".join(report.errors))
            if self.devices.get("temp_switch") is not None:
                self.outputs["温控"] = "未知" if report.errors else "5 V DC + ON"
            if self.devices.get("scope") is not None:
                self.outputs["示波器"] = "未知" if report.errors else "STOP"
            if self.clock_failed:
                errors = disconnect_device_mapping({str(i): d for i, d in enumerate(self.device_session.unique_devices())})
                if errors and cleanup_failure is None:
                    cleanup_failure = RuntimeError("；".join(errors))
        except BaseException as exc:
            cleanup_failure = exc
        self.closed, self.current_step = True, None
        self.revision += 1
        self.state.update(completion_status="completed" if self.acquired and failure is None and cleanup_failure is None else "failed",
            failure_reason=str(failure or cleanup_failure) if failure or cleanup_failure else None)
        self.states[FINISH_ID] = dict(status="failed" if cleanup_failure else "completed", error=str(cleanup_failure or ""))
        try:
            if self.directory:
                self.directory.update_config(**self.state)
            self.publish("会话已结束；RF 门控 Burst 保持，TEC 已尝试断开；收尾结果见记录")
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = exc
            else:
                getattr(cleanup_failure, "add_note", print)(f"收尾配置保存失败: {exc}")
        if cleanup_failure is not None:
            if failure is None:
                raise cleanup_failure
            getattr(failure, "add_note", print)(f"安全收尾或配置保存失败: {cleanup_failure}")


def run(params: T2CalibrationParams, *, project_root=None) -> Path:
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main() -> int:
    run(load_runtime_params(T2CalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
