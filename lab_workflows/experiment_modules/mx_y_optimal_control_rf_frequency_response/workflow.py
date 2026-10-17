"""完整运行与分步会话共用的最优控制频率响应编排。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory
from ...steps.safety_shutdown import disconnect_device_mapping
from ..noise_spectrum_xy.state_io import write_state
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point, RPointQualityError
from . import acquisition, preparation, run_format
from .models import MxYOptimalControlRFFrequencyResponseParams
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, invalidated_stages, validate_selection
from .acquisition import (ScanProgress, scan_point_counts, build_frequency_axis, build_phase_axis,
    OPTIMAL_CONTROL_DATA_FILE, CONSTANT_CONTROL_DATA_FILE, CONSTANT_CONTROL_PHASE_DEG,
    ACQUISITION_PERCENT_BUDGET, _point_metadata, _configure_constant_control_z, _restore_optimal_control_z)

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


def _scan_optimal_control(*args, **kwargs):
    return acquisition._scan_optimal_control(*args, **kwargs, point_acquirer=_acquire_valid_r_point)


def _scan_constant_control(*args, **kwargs):
    return acquisition._scan_constant_control(*args, **kwargs, point_acquirer=_acquire_valid_r_point)


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.shutdown_parameters = self.params
        self.corrected = self.applied = self.plan = self.executed_source = None
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self.constant_active = False
        self.quality_error = self.progress = None
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump", "共同触发")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            scan_mode="optimal_control_with_constant_control_comparison" if self.params.comparison_enabled else "optimal_control_phase_frequency",
            geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y", "optimal_control": "Z"},
            frequency_axis={"start_hz": self.params.frequency_start_hz, "stop_hz": self.params.frequency_stop_hz, "points": self.params.frequency_points},
            phase_axis={"start_deg": self.params.phase_scan_start_deg, "stop_deg": self.params.phase_scan_stop_deg, "step_deg": self.params.phase_scan_step_deg},
            acquisition_signals=["Demod0 R"], comparison={"enabled": False})

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"], (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请安全结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)
        if self.corrected is not None:
            current, _, _ = preparation.load_sources(self.params, self.root)
            if current.run_name == self.corrected.run_name and current.waveform_sha256 != self.corrected.waveform_sha256:
                invalidate(self.stages, self.states, "A01")
                self.corrected = None
                self.revision += 1
                raise ValueError("闭环冻结文件已改变，请从A01重新预检来源")

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；未经额外回读", demod_mode="response" if self.executed_source else "unknown",
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}),
            daq_settings={"actual_response_rate_sa_s": self.state.get("actual_rates", {}).get("response_sa_s")})

    def publish(self, message="等待选择下一模块"):
        value = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", value)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxYOptimalControlRFFrequencyResponseParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据")
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag if not suffix else f"{self.params.run_tag}_{suffix:02d}"
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(), schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.run_parameters = self.params.to_external()
        self.state = self._initial_state()
        affected = {stage.id for item in selected if item != "A01" for stage in invalidated_stages(self.stages, item)}
        if previous:
            self.state["source_run_id"] = previous.root.name
            for key in ("actual_rates", "clock_sources", "hf2_configuration", "temperature_control", "control_source", "comparison"):
                if key in inherited:
                    self.state[key] = inherited[key]
            # 只继承仍有效的科学前置；重跑的扫描不会混入旧点。
            for path in previous.raw.glob("*"):
                scan = "A04" if path.name.startswith("optimal_") else "A05" if path.name.startswith("constant_") else None
                if path.is_file() and (scan is None or (scan not in affected and self.states[scan]["status"] == "completed")):
                    shutil.copy2(path, self.directory.raw / path.name)
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed or self.run_parameters != self.params.to_external()
                or any(item in ("A04", "A05") and self.states[item]["status"] != "pending" for item in hardware)):
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
                artifacts = [path.relative_to(self.directory.root).as_posix() for pattern in stage.artifacts
                    for path in self.directory.root.glob(pattern) if path.is_file()] if self.directory else []
                self.states[item] = {"status": "completed", "error": "", "artifacts": artifacts}
            except BaseException as exc:
                failure = exc
                if item in ("A04", "A05"):
                    self.outputs["Y RF"] = "未知（本模块未完成）"
                self.states[item] = {"status": "quality_failed" if isinstance(exc, RPointQualityError) else "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                try:
                    self.save()
                except Exception as error:
                    self.state["state_save_error"] = str(error)
                if isinstance(exc, RPointQualityError):
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
        if item == "A01":
            self.corrected, self.applied, self.plan = preparation.load_sources(self.params, self.root)
        elif item == "A02":
            if not self.devices:
                self.devices, self.channels = preparation.connect(self.mapping, self.device_session)
            preparation.snapshot_source(self.directory, self.corrected, self.applied)
            self.state["control_source"] = {"mode": "corrected_run", "run": self.corrected.run_name, "waveform_sha256": self.corrected.waveform_sha256,
                "amplitude_vpp": self.corrected.amplitude_vpp, "offset_v": self.corrected.offset_v}
            self.state["comparison"] = self.plan.to_metadata() if self.plan else {"enabled": False}
        elif item == "A03":
            self.state.update(preparation.configure(self.params, self.devices, self.channels, self.mapping, self.corrected, self.applied))
            self.shutdown_parameters = self.params
            self.executed_source = (self.params, self.corrected, self.applied)
            self.constant_active = False
            self.outputs.update(X="配置值", Z="最优控制ON", **{"Y RF": "OFF", "温控": "ON", "Pump": "ON", "共同触发": "ON"})
        elif item == "A04":
            if self.constant_active:
                preparation.restore_optimal(self.executed_source[0], self.devices, self.channels, *self.executed_source[1:])
                self.constant_active = False
            self.progress = ScanProgress(total=sum(scan_point_counts(self.params)))
            _scan_optimal_control(self.params, self.directory, self.devices, self.channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]), self.progress)
            self.outputs["Y RF"] = "ON"
        elif item == "A05" and self.plan is not None:
            self.constant_active = True
            self.outputs["Z"] = "未知（切换中）"
            _configure_constant_control_z(self.params, self.devices, self.channels, self.plan)
            self.outputs["Z"] = "恒定DC ON"
            optimal, constant = scan_point_counts(self.params)
            if self.progress is None or self.progress.completed != optimal:
                self.progress = ScanProgress(total=optimal + constant, completed=optimal)
            _scan_constant_control(self.params, self.directory, self.devices, self.channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]), self.plan, self.progress)
        elif item == "A06":
            errors = self._shutdown(disconnect_tec=False)
            if errors:
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=[path.relative_to(self.directory.root).as_posix() for path in sorted(self.directory.raw.glob("*")) if path.is_file()])
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A03")
        self.save()

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.constant_active and self.executed_source:
            params, corrected, applied = self.executed_source
            try:
                preparation.restore_optimal(params, self.devices, self.channels, corrected, applied)
                self.constant_active = False
            except Exception as exc:
                errors.append("恢复Z最优控制失败：" + str(exc))
        if self.devices:
            try:
                report = preparation.safe_shutdown(self.devices, self.channels, self.shutdown_parameters, disconnect_tec=disconnect_tec)
                self.state["safety_shutdown"] = report.to_dict()
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        if self.devices:
            self.outputs.update(X="OFF" if not errors else "未知", **{"Y RF": "OFF" if not errors else "未知", "Z": "最优控制ON" if self.executed_source and not errors else "未知", "温控": "ON" if not errors else "未知", "共同触发": "保持ON" if self.executed_source and not errors else "未知"})
        return errors

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(disconnect_device_mapping({"tec": self.devices.get("tec")})) if self.round_sealed else self._shutdown()
        if self.round_sealed and errors:
            self.state["cleanup_errors"] = list(errors)
        if not self.round_sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, WorkflowCancelled) else "failed",
                failure_reason=str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成",
                finished_at=datetime.now().isoformat())
            if errors:
                self.state["failure_reason"] += "；安全恢复失败：" + "；".join(errors)
            try:
                self.save()
            except Exception as exc:
                self.state["state_save_error"] = str(exc)
                if failure is None and not errors:
                    errors.append(str(exc))
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        try:
            self.publish("会话已安全结束")
            report_runtime_progress("finished", "采集会话结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params, *, project_root=None, workflow_type=Workflow):
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
    run(load_runtime_params(MxYOptimalControlRFFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
