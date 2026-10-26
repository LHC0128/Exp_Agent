"""Bell Bloom Z 完整与分步运行只编排同一组职责函数。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping, set_temperature_switch
from lab_workflows.workflow_state import write_state
from . import acquisition, preparation, excitation, run_format
from .models import BellBloomZFieldCalibrationParams
from .stages import ACQUISITION, FINISH_ID, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states
# 旧导入和测试注入边界继续委派实际业务。
from ._helpers import RuntimeCancellation, sleep
from .preparation import connect_devices, configure_outputs, safe_shutdown
from .excitation import set_gate_frequency, set_demod_frequency
from .acquisition import acquire_r, summarize_r

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
DG_KEYS, OFF_KEYS = run_format.DG_KEYS, run_format.OFF_KEYS
EXECUTION_MODE = "typed_workflow"


def acquire_point(*args, **kwargs):
    return acquisition.acquire_point(*args, **kwargs, switch=set_temperature_switch, wait=sleep, reader=acquire_r)


def acquire_scan(*args, **kwargs):
    return acquisition.acquire_scan(*args, **kwargs, point_reader=acquire_point)


class Workflow:
    """同一设备会话可重复采集，每轮原始数据与参数快照独立。"""
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.pump_parameters = self.response_parameters = None
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
        self.outputs = {key: "未知" for key in ("GS200", "X", "Pump 脉冲", "Z", "温控", "Pump")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            started_at=datetime.now().astimezone().isoformat(), analysis_status="not_started",
            geometry={"main_field": "Z", "pump": "Z", "probe": "X", "pump_modulation": "external_gated",
                "z_dc_scan": "single_pass_negative_to_positive"},
            analysis_during_acquisition=False, acquisition_signals=["R"],
            r_point_quality={"criterion": "std(R) <= threshold", "std_threshold_v": self.params.r_bad_point_std_threshold_v,
                "max_attempts": self.params.r_point_max_attempts},
            temperature_gating={"scope": "every_frequency_point", "off_voltage_v": 0.0, "on_voltage_v": 5.0,
                "combined_settle_time_s": self.params.system_settle_s, "on_lag_s": self.params.temp_switch_on_lag_s})

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
            output_source="最近成功模块的写入结果；未配置输出为未知，后续未额外回读",
            demod_mode=self.demod_mode, safety_shutdown=self.state.get("safety_shutdown"),
            clock_sources=self.state.get("clock_sources", {}),
            daq_settings={"actual_response_rate_sa_s": self.state.get("actual_rates", {}).get("response_sa_s")})

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot | {"parameters": self.run_parameters})
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(BellBloomZFieldCalibrationParams.from_external(old | values), self.root)
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
            for key in ("clock_sources", "temperature_control", "actual_rates", "hf2_configuration", "response_config", "optional_connection_errors"):
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
        hardware = [item for item in selected if item not in ("A01", "A09", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed
                or self.run_parameters != self.params.to_external()
                or any(self.states[item]["status"] != "pending" for item in hardware if item == "A08")):
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
                if item == "A09":
                    write_state(self.directory.root / "acquisition_state.json", self.snapshot() | {"parameters": self.run_parameters, "current_step": None, "status": "waiting"})
            except BaseException as exc:
                failure = exc
                quality = isinstance(exc, acquisition.RPointQualityError)
                self.states[item] = {"status": "quality_failed" if quality else "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                if self.directory:
                    self.state["data_files"] = [path.relative_to(self.directory.root).as_posix() for path in self.directory.raw.rglob("*.npz")]
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
                    self.devices = preparation.connect_devices(self.device_session, self.mapping)
                    self.channels = {key: int(self.mapping[key]["channel"]) for key in run_format.DG_KEYS}
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
            self.state["optional_connection_errors"] = self.device_session.optional_connection_errors()
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.configure_clocks(devices, self.mapping)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            preparation.configure_initial_outputs(params, devices, channels, self.mapping)
            self.outputs.update(GS200="设置主场电流", X="OFF", Z="OFF", **{"温控": "ON"})
        elif item == "A05":
            status = preparation.configure_temperature_control(devices.get("tec"), params.temperature_c, channel=self.mapping["temperature"]["channel"])
            self.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)
        elif item == "A06":
            excitation.configure_pump(params, devices, channels)
            self.pump_parameters = params
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            actual_rate, response_config = excitation.configure_response(params, devices)
            self.response_parameters = params
            self.state.update(actual_rates={"response_sa_s": actual_rate}, hf2_configuration=response_config)
            self.state["response_config"] = {"parameters": params.to_external(), "actual_rate_sa_s": actual_rate,
                "demod_index": 0, "oscillator_index": 0, "acquisition_signals": ["R"]}
            self.demod_mode = "response"
            self.outputs["Pump 脉冲"] = "ON"
        elif item == "A08":
            self.state["data_files"] = acquisition.acquire_scan(params, devices, channels, self.directory,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            self.outputs.update(Z="最近命令电压", **{"Pump 脉冲": "ON", "温控": "ON"})
        elif item == "A09":
            report = self.shutdown(disconnect_tec=False)
            self.state.update(safety_shutdown=report.to_dict())
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.outputs.update(GS200="保持本轮主场", X="OFF", Z="OFF", **{"Pump 脉冲": "参考脉冲 ON", "温控": "ON"})
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat())
            self.save()
            self.round_sealed = True
            # 收尾恢复零偏参考；重扫需显式恢复本轮首频点与解调准备。
            invalidate(self.stages, self.states, "A07")
        self.save()

    def shutdown(self, *, disconnect_tec=True):
        return preparation.safe_shutdown(self.pump_parameters or self.params, self.devices, self.channels,
            demod_params=self.response_parameters, disconnect_tec=disconnect_tec,
            restore_pump=self.pump_parameters is not None, restore_demod=self.response_parameters is not None)

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.devices:
            try:
                if self.round_sealed:
                    errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                else:
                    report = self.shutdown()
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
                if failure is None and not errors:
                    raise
        elif errors:
            # 已封存目录不改写采集参数；清理失败仍保留在会话快照并向调用者报告。
            self.state["cleanup_errors"] = list(errors)
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        if self.devices:
            self.outputs.update(GS200="保持本轮主场" if not errors else "未知", X="OFF" if not errors else "未知", Z="OFF" if not errors else "未知",
                **{"Pump 脉冲": "参考脉冲 ON" if not errors and self.pump_parameters else "未知", "温控": "ON" if not errors else "未知"})
        try:
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))



def run(params: BellBloomZFieldCalibrationParams, *, project_root=None, workflow_type=Workflow) -> Path:
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
    run(load_runtime_params(BellBloomZFieldCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
