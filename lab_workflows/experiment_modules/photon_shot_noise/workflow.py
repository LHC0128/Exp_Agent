"""完整运行与串行分步会话共用编排；仪器步骤和扫描逻辑不在此复制。"""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from ...common import find_project_root, WorkflowCancelled
from ...experiment_runtime import load_runtime_params, check_cancelled, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, configure_temperature_control, disconnect_device_mapping
from ..noise_spectrum_xy.state_io import write_state
from .models import PhotonShotNoiseParams
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, validate_selection
from . import preparation, acquisition, run_format


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self.clock_failed = self.outputs_started = False
        self.stream_rate = self.plan = self.grid_cols = None
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("主场", "Pump", "Probe", "温控", "锁相")}

    def _initial_state(self):
        return dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
            purpose="shot_noise_calibration", execution_mode="typed_workflow",
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            data_files=[], temperature_gating={"scope": "each_repeat", "off_voltage_v": 0, "on_voltage_v": 5},
            acquisition_signals=["sample.x", "sample.y"])

    def _configuration_revision(self):
        value = instrument_config_snapshot(self.root)
        return (value["device_library_revision"], value["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise ValueError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；锁相准备已回读，其余未经额外回读",
            demod_mode=self.plan.module if self.plan else "unknown", daq_settings=self.state.get("daq_settings"),
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}))

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
        if self.current_step:
            raise ValueError("模块执行中不能应用参数")
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(type(self.params).from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块，不会自动设置输出")

    def _new_directory(self):
        inherited = deepcopy(self.state)
        if self.directory and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        self.directory = create_run_directory(run_format.DATA_TYPE, self.params.run_tag,
            self.params.to_external(), schema_version=self.params.schema_version, project_root=self.root)
        self.run_parameters, self.round_sealed = self.params.to_external(), False
        self.state = self._initial_state()
        for key in ("clock_sources", "temperature_control", "initial_temperature_c", "daq_settings"):
            if key in inherited:
                self.state[key] = inherited[key]
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        if hardware and (self.directory is None or self.round_sealed
                or self.run_parameters != self.params.to_external()
                or ("A07" in selected and self.states["A07"]["status"] != "pending")):
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
                self.publish(next(s.title for s in self.stages if s.id == item))
                self._execute_step(item)
                self.states[item] = dict(status="completed", error="")
                if item == "A07":
                    self.states[item]["artifacts"] = self.state["data_files"]
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
                self.states[item] = dict(status=status, error=str(exc))
                self.state.update(completion_status=status, failure_reason=str(exc))
                try:
                    self.save()
                except Exception as error:
                    self.state["state_save_error"] = str(error)
                raise
            finally:
                self.current_step = None
                try:
                    if item == "A07" and self.round_sealed and failure is None:
                        write_state(self.directory.root / "acquisition_state.json", self.snapshot())
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not self.devices:
                try:
                    preparation.connect(self)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    self.devices.clear()
                    raise
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.clocks(self)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_started = True
            preparation.initial_outputs(self)
            self.outputs.update(主场=f"{self.params.shot_noise_field_ma:g} mA + ON",
                Pump="OFF", Probe=f"{self.params.probe_power_start:g} V DC + ON", 温控="5 V DC + ON")
        elif item == "A05":
            status = configure_temperature_control(self.devices.get("tec"), self.params.temperature,
                channel=int(self.mapping["temperature"].get("channel", 1)))
            self.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)
        elif item == "A06":
            preparation.prepare_demod(self)
        elif item == "A07":
            self.state.update(acquisition.acquire_scan(self))
            errors = self._shutdown(disconnect_tec=False)
            if errors:
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None)
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A06")
        self.save()

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            report = preparation.shutdown(self, disconnect_tec=disconnect_tec)
            self.state["safety_shutdown"] = report.to_dict()
            errors.extend(report.errors)
            self.outputs["温控"] = "未知" if errors else "5 V DC + ON"
            if self.clock_failed or (not self.outputs_started and disconnect_tec):
                errors.extend(disconnect_device_mapping({str(i): device for i, device in
                    enumerate(self.device_session.unique_devices())}))
        return errors

    def finish(self, failure=None):
        if self.closed:
            return
        errors = self._shutdown()
        # 已封存的采集快照不受下一轮草稿影响，断开失败仍必须在会话中明确报告。
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        if not self.round_sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt))
                else "failed", failure_reason=str(failure) if failure else self.state.get("failure_reason", "本轮未完整采集"))
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        try:
            self.save()
            if errors and self.round_sealed:
                self.directory.update_config(completion_status="failed", failure_reason="；".join(errors),
                    safety_shutdown=self.state["safety_shutdown"])
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100, estimated_remaining_seconds=None)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params: PhotonShotNoiseParams, *, project_root=None) -> Path:
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([s.id for s in ACQUISITION if s.id != FINISH_ID])
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(PhotonShotNoiseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
