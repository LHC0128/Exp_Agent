# %% [markdown]
# # RF 灵敏度（DirectAW）：完整运行与分步会话共用编排

# %% 导入
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
import time
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import create_run_directory, synchronize_connected_clocks
from ...steps.safety_shutdown import disconnect_device_mapping
from ..noise_spectrum_xy.state_io import write_state
from . import preparation, excitation, phase, acquisition, run_format
from ._helpers import _RuntimeCancellation, hf2_snapshot, wait
from .models import RFSensitivityDirectAWParams
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, invalidated_stages, validate_selection


class Workflow:
    """一个设备会话可重复采集；每轮参数和原始数据独立保存。"""
    stages = ACQUISITION
    params_type = RFSensitivityDirectAWParams
    identity = run_format

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping, self.envelope = self.preflight(params)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices = {}
        self.directory = None
        self.run_parameters = None
        self.state = preparation.initial_state(params, identity=self.identity)
        self.cancellation = _RuntimeCancellation()
        self.current_step = None
        self.closed = self.clock_failed = self.outputs_safe = False
        self.demod_mode = "unknown"
        self.d0_config = self.phase0 = self.xy_phase = self.response_config = None
        self.z_positive_phase = self.quality_error = None
        self.outputs = {name: "未知" for name in ("X", "Y", "Z", "温控", "Pump", "共用触发")}
        self.started = time.monotonic()

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def preflight(self, params):
        return preparation.preflight(params, self.root, experiment_id=self.identity.EXPERIMENT_ID)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；未经额外回读", demod_mode=self.demod_mode,
            xy_phase_calibration=self.state["xy_phase_calibration"], rf_phase_calibration=self.state["rf_phase_calibration"],
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sync", {}),
            demod_phase_deg=self.state["calibration"].get("demod0", {}).get("phase_shift_deg"),
            daq_settings=self.state.get("response_acquisition"))

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def save(self):
        if self.directory:
            self.directory.update_config(**{key: value for key, value in self.state.items()
                if key not in ("analysis_status", "analysis_error")})

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping, self.envelope = self.preflight(self.params)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        unknown = values.keys() - old.keys()
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping, envelope = self.preflight(self.params_type.from_external(old | values))
        changed = {name for name, value in params.to_external().items() if value != old[name]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping, self.envelope = params, mapping, envelope
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

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
                    schema_version=2, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.state = preparation.initial_state(self.params, identity=self.identity)
        self.run_parameters = self.params.to_external()
        # 校准副本明确记录来源，旧目录和原始数据保持原样。
        if previous:
            affected = {stage.id for item in selected if item != "A01"
                        for stage in invalidated_stages(self.stages, item)}
            for key in ("calibration", "actual_rates", "clock_sync", "temperature_control", "optional_connection_errors",
                        "xy_aw_output", "hf2_response_snapshot", "response_output_snapshot", "response_acquisition",
                        "stage_frequencies", "hf2_calibration_snapshot", "calibration_output_snapshot"):
                if key in inherited:
                    self.state[key] = inherited[key]
            for stage_id, name in (("A13", "response"), ("A12", "calibration"), ("A14", "noise")):
                if stage_id in affected:
                    self.state.get("stage_frequencies", {}).pop(name, None)
                    self.state.pop(f"hf2_{name}_snapshot", None)
                    self.state.pop(f"{name}_output_snapshot", None)
            for stage_id, key in (("A09", "xy_phase_calibration"), ("A11", "rf_phase_calibration"), ("A13", "response_points_completed")):
                if stage_id in self.states and stage_id not in affected and self.states[stage_id]["status"] == "completed":
                    self.state[key] = inherited[key]
                    stage = next(item for item in self.stages if item.id == stage_id)
                    for name in stage.artifacts:
                        source = previous.root / name
                        if source.is_file() and name.startswith(("raw/", "results/")):
                            shutil.copy2(source, self.directory.root / name)
                            if name.startswith("raw/"):
                                self.state["data_files"].append(name)
            if "A12" not in affected and self.states["A12"]["status"] == "completed":
                self.state["phase_calibration_status"] = "completed"
            self.state["calibration_source_run_id"] = previous.root.name
            self.state["calibration"]["z_v_to_nT"] = self.params.z_v_to_nt
        self.directory.update_config(run_tag=self.params.run_tag, **self.state)

    def progress(self, stage, completed):
        total = self.params.rf_amp_points + self.params.noise_n_avg
        report_runtime_progress(stage, f"已完成 {completed}/{total} 个采集单元", 25 + 65 * completed / total,
            estimated_remaining_seconds=(time.monotonic() - self.started) / completed * (total - completed))

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", FINISH_ID)]
        order = [stage.id for stage in self.stages]
        if hardware and (self.directory is None or (
                any(order.index("A04") <= order.index(item) <= order.index("A13") for item in hardware)
                and self.state["response_points_completed"] > 0)
                or (any(order.index(item) <= order.index("A14") for item in hardware) and self.run_parameters != self.params.to_external())
                or any(self.states[item]["status"] in ("completed", "quality_failed", "failed", "stale")
                       for item in hardware if item in ("A09", "A11", "A14"))):
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
                artifacts = [str(path.relative_to(self.directory.root)).replace(chr(92), "/")
                    for name in stage.artifacts for path in self.directory.root.glob(name)] if self.directory else []
                self.states[item] = {"status": "completed", "error": "", "artifacts": artifacts}
            except BaseException as exc:
                failure = exc
                quality = isinstance(exc, phase.CalibrationQualityError) or (item == "A11" and isinstance(exc, ValueError))
                self.states[item] = {"status": "quality_failed" if quality else "failed", "error": str(exc)}
                if item in ("A08", "A09", "A11", "A12"):
                    self.state["phase_calibration_status"] = "failed"
                try:
                    self.save()
                except Exception as error:
                    self.state["state_save_error"] = str(error)
                self.outputs = {key: "未知" for key in self.outputs}
                if quality:
                    self.quality_error = exc
                    preparation.restore_temperature(self)
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
            self.recheck()
            return
        elif item == "A02":
            if not self.devices:
                self.devices = preparation.connect_devices(self.mapping)
        elif item == "A03":
            try:
                self.state["clock_sync"] = synchronize_connected_clocks(self.devices, self.mapping,
                    {"signals": "X_magnetic_field", "hf2": "lockin_r"}, settle_wait=wait)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_safe = False
            preparation.configure_initial_outputs(self)
            self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON", 共用触发="OFF")
        elif item == "A05":
            preparation.stabilize_temperature(self)
        elif item == "A06":
            excitation.configure_pump(self)
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            excitation.configure_trigger(self)
            self.outputs["共用触发"] = "ON"
        elif item == "A08":
            self.state["phase_calibration_status"] = "running"
            phase.calibrate_demod0(self)
            self.demod_mode = "phase"
        elif item == "A09":
            self.outputs_safe = False
            phase.calibrate_xy(self)
            self.outputs.update(X="ON", Y="ON", 温控="ON")
        elif item == "A10":
            phase.configure_loopback(self)
            self.demod_mode = "response"
            self.outputs["Z"] = "ON"
            acquisition.record_response_settings(self)
        elif item == "A11":
            acquisition.record_response_settings(self)
            phase.calibrate_rf(self)
        elif item == "A12":
            phase.calibrate_demod3(self)
        elif item == "A13":
            acquisition.record_response_settings(self)
            self.started = time.monotonic()
            acquisition.acquire_response(self)
        elif item == "A14":
            if self.identity.RESPONSE_COMPONENT == "X":
                acquisition.record_response_settings(self)
            acquisition.acquire_noise(self)
            self.outputs.update(Z="OFF", 温控="ON")
        elif item == "A15":
            acquisition.restore_response(self)
            report_runtime_progress("shutdown", "结束本轮采集，关闭 XYZ 并保持设备会话", 95)
            report = preparation.shutdown(self.devices, disconnect_tec=False)
            self.state["safety_shutdown"] = report.to_dict()
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.outputs_safe = True
            self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
            self.state["hf2_final_snapshot"] = hf2_snapshot(self.devices["hf2"])
            self.state.update(completion_status="completed", finished_at=datetime.now().isoformat())
            invalidate(self.stages, self.states, "A09")
        self.save()

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        sealed = self.outputs_safe and self.state["completion_status"] == "completed"
        try:
            report_runtime_progress("shutdown", "恢复响应配置并执行安全收尾", 95)
        except Exception as exc:
            self.state["progress_reporting_error"] = str(exc)
        if self.demod_mode == "noise":
            try:
                acquisition.restore_response(self)
            except Exception as exc:
                self.state["hf2_restore_error"] = str(exc)
                errors.append(str(exc))
        if self.devices:
            try:
                if self.outputs_safe:
                    errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                    if errors:
                        self.state["safety_shutdown"].update(completed=False, errors=list(errors))
                else:
                    report = preparation.shutdown(self.devices)
                    self.state["safety_shutdown"] = report.to_dict()
                    errors.extend(report.errors)
                    if report.completed:
                        self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            except Exception as exc:
                errors.append(str(exc))
                self.state["safety_shutdown"] = dict(completed=False, errors=list(errors))
            if not sealed:
                try:
                    self.state["hf2_final_snapshot"] = hf2_snapshot(self.devices["hf2"])
                except Exception as exc:
                    self.state["hf2_snapshot_error"] = str(exc)
        if failure is not None and not sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, WorkflowCancelled) else "failed",
                              failure_reason=str(failure))
            if self.state["phase_calibration_status"] == "running":
                self.state["phase_calibration_status"] = "failed"
        elif errors and not sealed:
            self.state.update(completion_status="failed", failure_reason="安全收尾失败：" + "；".join(errors))
        elif self.states["A15"]["status"] == "completed":
            self.state["completion_status"] = "completed"
        elif self.directory:
            self.state.update(completion_status="failed", failure_reason="本轮采集未完整完成，仅可查看诊断")
        if not sealed:
            self.state["finished_at"] = datetime.now().isoformat()
        self.closed = True
        self.revision += 1
        self.current_step = None
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        try:
            # 已完成轮次保持快照不变，结束会话不覆盖并行离线分析的记录。
            if not sealed:
                self.save()
            self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            report_runtime_progress("finished", f"采集{self.state['completion_status']}", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


# %% 完整采集与子进程入口
def run(params: RFSensitivityDirectAWParams, *, project_root=None, workflow_type=Workflow) -> Path:
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
    run(load_runtime_params(RFSensitivityDirectAWParams))


if __name__ == "__main__":
    main()
