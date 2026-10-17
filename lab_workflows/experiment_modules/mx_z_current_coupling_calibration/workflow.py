"""电流耦合完整与分步运行共用的采集编排。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping
from ..noise_spectrum_xy.state_io import write_state
from ..mx_z_field_calibration.workflow import run_calibration
from . import acquisition, preparation, excitation, run_format
from .models import MxZCurrentCouplingCalibrationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID, initial_states, invalidate, invalidated_stages, validate_selection

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


class Workflow:
    """同一设备会话可重复采集，每轮原始数据与参数快照独立。"""
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.pump_parameters = self.params
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
        self.sense_scope = None
        self.demod_mode = "unknown"
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("GS200", "X", "Y RF", "Z", "温控", "Pump")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            geometry={"main_field": "Z", "pump": "Z", "probe": "X", "rf_field": "Y",
                "z_dc_scan": "forward_then_reverse" if self.params.bidirectional_scan else "single_pass_negative_to_positive"},
            analysis_during_acquisition=False, acquisition_signals=["R", "SDS CH3 low-side sense-resistor voltage"],
            r_point_quality={"criterion": "std(R) <= threshold", "std_threshold_v": self.params.r_bad_point_std_threshold_v,
                "max_attempts": self.params.r_point_max_attempts},
            temperature_gating={"scope": "every_z_sense_point_and_every_frequency_point", "off_voltage_v": 0.0, "on_voltage_v": 5.0,
                "combined_settle_time_s": self.params.system_settle_s, "on_lag_s": self.params.temp_switch_on_lag_s})

    def _sense_resistor_snapshot(self):
        return dict(resistance_ohm=float(self.params.sense_resistor_ohm),
            tolerance_percent=float(self.params.sense_resistor_tolerance_percent),
            power_rating_w=float(self.params.sense_resistor_power_rating_w),
            scope_channel=int(self.params.sense_scope_channel))

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
            write_state(self.directory.root / "acquisition_state.json",
                snapshot | {"parameters": deepcopy(self.run_parameters)})
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxZCurrentCouplingCalibrationParams.from_external(old | values), self.root)
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
            for key in ("clock_sources", "device_snapshot", "temperature_control", "actual_rates", "sense_scope_configuration"):
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
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed
                or self.run_parameters != self.params.to_external()
                or any(self.states[item]["status"] != "pending" for item in hardware if item == RUN_ID)):
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
                quality = isinstance(exc, (acquisition.RPointQualityError, acquisition.SensePointQualityError))
                self.states[item] = {"status": "quality_failed" if quality else "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                try:
                    self.save()
                except Exception as save_error:
                    self.state["state_save_error"] = str(save_error)
                if quality:
                    self.quality_error = exc
                    raise
                raise
            finally:
                self.current_step = None
                try:
                    if item == "A10" and failure is None:
                        write_state(self.directory.root / "acquisition_state.json",
                            self.snapshot() | {"status": "completed", "parameters": deepcopy(self.run_parameters)})
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
                    self.devices, self.channels = preparation.connect_devices(self.mapping, self.device_session)
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
            self.outputs.update(GS200="设置主场电流", X="OFF", Z="OFF", **{"温控": "ON"})
        elif item == "A05":
            status = preparation.configure_temperature_control(devices.get("tec"), params.temperature_c, channel=1)
            self.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)
        elif item == "A06":
            excitation.configure_pump(params, devices, channels)
            self.pump_parameters = params
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            self.state["actual_rates"] = {"response_sa_s": excitation.configure_response(params, devices, channels)}
            self.demod_mode = "response"
            self.outputs["Y RF"] = "OFF"
        elif item == "A08":
            self.sense_scope = preparation.configure_sense_scope(params, devices)
            self.state["sense_scope_configuration"] = self.sense_scope[3]
            self.state["sense_resistor"] = self._sense_resistor_snapshot()
        elif item == RUN_ID:
            try:
                preparation.require_rf_mode(devices, channels)
            except Exception:
                invalidate(self.stages, self.states, "A07")
                raise
            try:
                preparation.require_scope_mode(devices, self.state["sense_scope_configuration"])
            except Exception:
                invalidate(self.stages, self.states, "A08")
                raise
            self.state["sense_resistor"] = self._sense_resistor_snapshot()
            try:
                self.state["data_files"] = acquisition._acquire_scan(params, self.directory, devices, channels,
                    self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]),
                    sense_scope=self.sense_scope)
            except BaseException:
                self.outputs.update(Z="未知（采集中断）", **{"Y RF": "未知（采集中断）", "温控": "未知（采集中断）"})
                raise
            self.outputs.update(Z="最近命令电压", **{"Y RF": "OFF", "温控": "ON"})
        elif item == "A10":
            report = preparation.safe_shutdown(devices, channels, self.pump_parameters, disconnect_tec=False)
            self.state.update(safety_shutdown=report.to_dict())
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.outputs.update(GS200="保持本轮主场", X="OFF", Z="OFF", **{"Y RF": "OFF", "温控": "ON"})
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat())
            self.save()
            self.round_sealed = True
            # 收尾把 Y RF 切成零电压 DC，重采前显式恢复正弦与解调准备。
            invalidate(self.stages, self.states, "A07")
        self.save()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        save_failure = None
        if self.devices:
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
            try:
                self.save()
            except Exception as save_error:
                self.state["state_save_error"] = str(save_error)
                save_failure = save_error
        elif errors:
            # 已封存目录不改写采集参数；清理失败仍保留在会话快照并向调用者报告。
            self.state["cleanup_errors"] = list(errors)
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = {"status": "failed" if errors or save_failure else "completed",
            "error": "；".join([*errors, *([str(save_failure)] if save_failure else [])])}
        if self.devices:
            self.outputs.update(GS200="保持本轮主场" if not errors else "未知", X="OFF" if not errors else "未知", Z="OFF" if not errors else "未知",
                **{"Y RF": "OFF" if not errors else "未知", "温控": "ON" if not errors else "未知"})
        try:
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100)
        except Exception:
            if failure is None and not errors and save_failure is None:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))
        if save_failure is not None and failure is None:
            raise save_failure


def run(params: MxZCurrentCouplingCalibrationParams, *, project_root=None, workflow_type=Workflow) -> Path:
    """完整采集只执行同一套采集步骤；分析仍由独立入口执行。"""
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


def main() -> int:
    run(load_runtime_params(MxZCurrentCouplingCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
