"""Mx Y RF 完整采集与串行分步会话共用的编排。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
import numpy as np

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, set_temperature_switch
from ...steps.safety_shutdown import disconnect_device_mapping
from ..noise_spectrum_xy.state_io import write_state
from . import preparation, excitation, acquisition_steps, run_format
from .models import MxYRFParams
from ._helpers import _sleep, _uncancellable_sleep, _RuntimeCancellation
from .acquisition import acquire_r, summarize_r
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, invalidated_stages, validate_selection

# 保留变体和原有回归使用的导出名，业务实现分别归属准备、激励和采集模块。
from .preparation import (_connect_devices, _initial_state_snapshot, _configure_outputs,
    _configure_reference_clocks, _configure_temperature_control, safe_shutdown)
from .excitation import (_set_pump_gate_on, _configure_y_rf_sine_source, _configure_response_demodulator,
    _restore_response_acquisition_state, _set_signed_rf, _set_y_rf_zero_off,
    _set_y_rf_zero_off_with_temperature_sequence, restore_mx_y_rf_laser_powers)
from .acquisition_steps import (RPointQualityError, _save_point, _acquire_frequency_scan,
    _acquire_amplitude_scan, _acquire_noise, acquire_mx_y_rf_point)

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


def _temperature_gated_acquire(*args, **kwargs):
    kwargs.setdefault("switch", set_temperature_switch)
    kwargs.setdefault("wait", _sleep)
    return acquisition_steps._temperature_gated_acquire(*args, **kwargs)


def _acquire_valid_r_point(*args, **kwargs):
    return acquisition_steps._acquire_valid_r_point(*args, **kwargs,
        reader=acquire_r, switch=set_temperature_switch, wait=_sleep,
        gated_acquire=_temperature_gated_acquire)


connect_mx_y_rf_devices = _connect_devices
snapshot_mx_y_rf_state = _initial_state_snapshot
configure_mx_y_rf_outputs = _configure_outputs


class Workflow:
    """同一设备会话可重复采集，每轮原始数据与参数快照独立。"""
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        # 收尾只使用已执行配置，首次配置前沿用会话初始基线。
        self.pump_parameters = self.response_parameters = self.params
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = None
        self.run_parameters = None
        self.current_step = None
        self.closed = self.round_sealed = self.clock_failed = False
        self.quality_error = None
        self.demod_mode = "unknown"
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            compatibility={"stable_data_type": DATA_TYPE, "schema_version": self.params.schema_version},
            linewidth_mode=self.params.linewidth_mode, acquisition_signals=["R"],
            phase_calibration_status="not_applicable", geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y", "x_field_output": "OFF"},
            r_point_quality={"criterion": "std(R) <= threshold", "std_threshold_v": self.params.r_bad_point_std_threshold_v,
                "max_attempts": self.params.r_point_max_attempts})

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
            (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；初态来自回读，后续未额外回读",
            demod_mode=self.demod_mode, safety_shutdown=self.state.get("safety_shutdown"),
            clock_sources=self.state.get("clock_sources", {}),
            daq_settings={"actual_response_rate_sa_s": self.state.get("actual_rates", {}).get("response_sa_s")})

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxYRFParams.from_external(old | values), self.root)
        changed = {name for name, value in params.to_external().items() if value != old[name]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag if not suffix else f"{self.params.run_tag}_{suffix:02d}"
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.state = self._initial_state()
        self.run_parameters = self.params.to_external()
        if previous:
            affected = {stage.id for item in selected if item != "A01"
                for stage in invalidated_stages(self.stages, item)}
            for key in ("clock_sources", "device_snapshot", "temperature_control", "actual_rates", "frequency_gate_fit"):
                if key in inherited:
                    self.state[key] = inherited[key]
            for stage in self.stages:
                if stage.id not in affected and self.states[stage.id]["status"] == "completed":
                    for pattern in stage.artifacts:
                        for source in previous.root.glob(pattern):
                            if source.is_file():
                                target = self.directory.root / source.relative_to(previous.root)
                                target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(source, target)
            self.state["source_run_id"] = previous.root.name
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        if self.demod_mode == "noise" and set(selected).intersection(("A08", "A09")) and "A07" not in selected:
            raise ValueError("当前为噪声模式，请显式选择 A07 重新准备响应解调")
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed
                or self.run_parameters != self.params.to_external()
                or any(self.states[item]["status"] != "pending" for item in hardware if item in ("A08", "A09", "A10"))):
            self._new_directory(selected)
        self.revision += 1
        self.quality_error = None
        for item in selected:
            if item == FINISH_ID:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                invalidate(self.stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            failure = None
            try:
                self.publish(next(stage.title for stage in self.stages if stage.id == item))
                self._execute_step(item)
                stage = next(stage for stage in self.stages if stage.id == item)
                artifacts = [path.relative_to(self.directory.root).as_posix()
                    for pattern in stage.artifacts for path in self.directory.root.glob(pattern)
                    if path.is_file()] if self.directory else []
                self.states[item] = {"status": "completed", "error": "", "artifacts": artifacts}
            except BaseException as exc:
                failure = exc
                quality = isinstance(exc, acquisition_steps.RPointQualityError)
                self.states[item] = {"status": "quality_failed" if quality else "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                try:
                    self.save()
                except Exception as save_error:
                    self.state["state_save_error"] = str(save_error)
                if quality:
                    self.quality_error = exc
                    break
                raise
            finally:
                self.current_step = None
                try:
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not devices:
                try:
                    self.devices, self.channels = preparation._connect_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
            self.state["device_snapshot"] = preparation._initial_state_snapshot(self.devices, self.channels)
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation._configure_reference_clocks(devices, self.mapping)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            preparation.configure_initial_outputs(params, devices, channels, self.mapping)
            self.outputs.update(X="OFF", Z="OFF", **{"Y RF": "OFF", "温控": "ON"})
        elif item == "A05":
            status = preparation.configure_temperature_control(devices.get("tec"), params.temperature_c, channel=1)
            self.state["temperature_control"] = status.to_dict()
        elif item == "A06":
            excitation.configure_pump(params, devices, channels)
            self.pump_parameters = params
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            self.state.setdefault("actual_rates", {})["response_sa_s"] = excitation.configure_response(params, devices, channels)
            self.response_parameters = params
            self.demod_mode = "response"
        elif item == "A08":
            if params.linewidth_mode == "frequency_sweep":
                self.state["frequency_gate_fit"] = acquisition_steps._acquire_frequency_scan(params, self.directory,
                    devices, channels, self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            else:
                self.state["frequency_gate_fit"] = None
        elif item == "A09":
            self.state["actual_rates"]["response_sa_s"] = acquisition_steps._acquire_amplitude_scan(params,
                self.directory, devices, channels, self.state["actual_rates"]["response_sa_s"],
                str(self.mapping["lockin_r"]["device_id"]))
            self.response_parameters = params
            self.demod_mode = "response"
        elif item == "A10":
            self.demod_mode = "noise"
            self.state["actual_rates"]["noise_sa_s"] = acquisition_steps._acquire_noise(params,
                self.directory, devices, channels, str(self.mapping["lockin_r"]["device_id"]))
            self.outputs.update(**{"Y RF": "OFF", "温控": "ON"})
        elif item == "A11":
            self.state["actual_rates"]["response_sa_s"] = excitation._restore_response_acquisition_state(self.response_parameters, devices, channels)
            self.demod_mode = "response"
            report = preparation.safe_shutdown(devices, channels, self.pump_parameters, disconnect_tec=False)
            self.state["safety_shutdown"] = report.to_dict()
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.outputs.update(X="OFF", Z="OFF", **{"Y RF": "OFF", "温控": "ON"})
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat())
            self.state["data_files"] = [path.relative_to(self.directory.root).as_posix() for path in sorted(self.directory.raw.glob("*.npz"))]
            self.save()
            self.round_sealed = True
            # 本轮收尾关闭 Y RF；重新采集必须显式重新准备解调与激励。
            invalidate(self.stages, self.states, "A07")
        self.save()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.devices:
            if self.demod_mode == "noise":
                try:
                    excitation._restore_response_acquisition_state(self.response_parameters, self.devices, self.channels)
                    self.demod_mode = "response"
                except Exception as exc:
                    errors.append("响应模式恢复失败：" + str(exc))
            try:
                if self.round_sealed:
                    errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                else:
                    report = preparation.safe_shutdown(self.devices, self.channels, self.pump_parameters)
                    self.state["safety_shutdown"] = report.to_dict()
                    errors.extend(report.errors)
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        if not self.round_sealed:
            reason = str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成"
            if errors:
                reason += "；安全恢复失败：" + "；".join(errors)
            self.state.update(completion_status="cancelled" if isinstance(failure, WorkflowCancelled) else "failed",
                failure_reason=reason, finished_at=datetime.now().isoformat())
            self.save()
        elif errors:
            # 已封存目录不改写采集参数；清理失败仍保留在会话快照并向调用者报告。
            self.state["cleanup_errors"] = list(errors)
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        self.outputs.update(X="OFF" if not errors else "未知", Z="OFF" if not errors else "未知",
            **{"Y RF": "OFF" if not errors else "未知", "温控": "ON" if not errors else "未知"})
        try:
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params: MxYRFParams, *, project_root=None, workflow_type=Workflow) -> Path:
    ctx = workflow_type(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ctx.stages if stage.id != FINISH_ID])
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(MxYRFParams))


if __name__ == "__main__":
    main()
