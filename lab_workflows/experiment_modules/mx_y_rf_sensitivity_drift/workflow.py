"""长飘完整与分步采集共用的编排；单轮采集复用标准 Mx Y RF。"""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import time
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping
from ..noise_spectrum_xy.state_io import read_state, write_state
from ..mx_y_rf_sensitivity.analysis import analyze as analyze_single
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError, acquire_mx_y_rf_point, configure_mx_y_rf_outputs,
    connect_mx_y_rf_devices, safe_shutdown, snapshot_mx_y_rf_state,
)
from . import acquisition, preparation, run_format, scheduling
from .analysis import refresh_trend
from .models import MxYRFDriftParams
from .scheduling import _planned_records, _child_parameters, _set_record_error, _utc_now, _write_manifest
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, validate_selection

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
CHILD_DATA_TYPE = run_format.CHILD_DATA_TYPE
EXECUTION_MODE = "typed_workflow"


def _sleep_until(deadline):
    return scheduling._sleep_until(deadline, clock=time.monotonic, sleep=time.sleep)


def _refresh_safely(parent_root):
    try:
        refresh_trend(parent_root)
    except Exception as exc:
        print(f"长飘趋势更新失败，后续轮次继续: {exc}")


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.pump_parameters = self.response_parameters = self.params
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self.clock_failed = False
        self.response_attempted = False
        self.demod_mode = "unknown"
        self.records = []
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump")}

    @property
    def manifest_path(self):
        return self.directory.root / run_format.MANIFEST

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, scan_mode="repeated_measurement",
            completion_status="running", child_experiment_id=run_format.CHILD_EXPERIMENT_ID,
            manifest=run_format.MANIFEST, interval_s=self.params.drift_interval_s,
            duration_s=self.params.drift_duration_s, planned_cycle_count=self.params.planned_cycle_count)

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
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
            clock_sources=self.state.get("clock_sources", {}), daq_settings=self.state.get("actual_rates"),
            drift_records=deepcopy(self.records))

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        elif self.directory and self.closed:
            path = self.directory.root / "acquisition_state.json"
            sealed = read_state(path)
            if sealed is None:
                raise RuntimeError("封存运行缺少已执行阶段快照")
            # 结束仅补充收尾状态；已封存参数和执行记录不随下一轮草稿改写。
            sealed.update(status="closed", current_step=None, revision=self.revision,
                outputs=dict(self.outputs), safety_shutdown=self.state.get("safety_shutdown"))
            sealed["steps"][FINISH_ID] = deepcopy(self.states[FINISH_ID])
            write_state(path, sealed)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("模块执行中不能应用参数")
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxYRFDriftParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

    def _new_directory(self):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag + (f"_{suffix:02d}" if suffix else "")
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.run_parameters = self.params.to_external()
        self.state = self._initial_state()
        for key in ("device_snapshot", "clock_sources", "temperature_control", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        if previous:
            self.state["source_run_id"] = previous.root.name
        self.records = _planned_records(self.params)
        _write_manifest(self.manifest_path, self.records)
        self.save()

    def execute(self, selected):
        if self.current_step:
            raise ValueError("已有模块正在执行")
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", "A10", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed or self.run_parameters != self.params.to_external()
                or "A09" in hardware and self.states["A09"]["status"] != "pending"):
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
                stage = next(stage for stage in self.stages if stage.id == item)
                artifacts = [path.relative_to(self.directory.root).as_posix() for pattern in stage.artifacts
                    for path in self.directory.root.glob(pattern) if path.is_file()] if self.directory else []
                self.states[item] = dict(status="completed", error="", artifacts=artifacts)
            except BaseException as exc:
                failure = exc
                self.states[item] = dict(status="failed", error=str(exc))
                self.state["failure_reason"] = str(exc)
                try:
                    self.save()
                except Exception as save_error:
                    self.state["state_save_error"] = str(save_error)
                raise
            finally:
                self.current_step = None
                try:
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
                if item == "A10" and failure is None:
                    self.round_sealed = True
        return self.snapshot()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not devices:
                try:
                    self.devices, self.channels = connect_mx_y_rf_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
            self.state["device_snapshot"] = snapshot_mx_y_rf_state(self.devices, self.channels)
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
            preparation.configure_pump(params, devices, channels)
            self.pump_parameters = params
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            self.demod_mode = "unknown"
            self.response_attempted = True
            rate = preparation.configure_response(params, devices, channels)
            self.state.setdefault("actual_rates", {})["response_sa_s"] = rate
            self.response_parameters = params
            self.demod_mode = "response"
        elif item == "A08":
            self.records = _planned_records(params)
            self.state.update(interval_s=params.drift_interval_s, duration_s=params.drift_duration_s,
                planned_cycle_count=len(self.records))
            _write_manifest(self.manifest_path, self.records)
        elif item == "A09":
            acquisition.run_cycles(self, acquire=acquire_mx_y_rf_point, analyze=analyze_single,
                refresh=_refresh_safely, write_manifest=_write_manifest, create_directory=create_run_directory,
                clock=time.monotonic, utc_now=_utc_now, wait_until=_sleep_until)
            self.outputs.update(**{"Y RF": "OFF", "温控": "ON"})
        elif item == "A10":
            self.state["actual_rates"]["response_sa_s"] = preparation._restore_response_acquisition_state(
                self.response_parameters, devices, channels)
            self.demod_mode = "response"
            report = safe_shutdown(devices, channels, self.pump_parameters, disconnect_tec=False)
            self.state["safety_shutdown"] = report.to_dict()
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.state.update(completion_status="completed", failure_reason=None, completed_utc=_utc_now())
            invalidate(self.stages, self.states, "A07")
        self.save()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.devices:
            if self.response_attempted and self.demod_mode != "response":
                try:
                    preparation._restore_response_acquisition_state(self.response_parameters, self.devices, self.channels)
                    self.demod_mode = "response"
                except Exception as exc:
                    errors.append("响应模式恢复失败：" + str(exc))
            try:
                if self.round_sealed:
                    errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                else:
                    report = safe_shutdown(self.devices, self.channels, self.pump_parameters)
                    self.state["safety_shutdown"] = report.to_dict()
                    errors.extend(report.errors)
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        if not self.round_sealed and self.directory:
            reason = str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成"
            if errors:
                reason += "；安全恢复失败：" + "；".join(errors)
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                failure_reason=reason, completed_utc=_utc_now())
            try:
                self.save()
                _write_manifest(self.manifest_path, self.records)
                _refresh_safely(self.directory.root)
            except Exception as exc:
                errors.append("运行状态保存失败：" + str(exc))
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        if self.devices or errors:
            self.outputs.update(X="未知" if errors else "OFF", Z="未知" if errors else "OFF",
                **{"Y RF": "未知" if errors else "OFF", "温控": "未知" if errors else "ON"})
        try:
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params: MxYRFDriftParams, *, project_root=None, workflow_type=Workflow) -> Path:
    ctx = workflow_type(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ctx.stages if stage.id != FINISH_ID])
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main() -> int:
    run(load_runtime_params(MxYRFDriftParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
