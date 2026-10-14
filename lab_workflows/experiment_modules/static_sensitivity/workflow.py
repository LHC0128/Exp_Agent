# %% [markdown]
# # 静磁场灵敏度：完整采集与分步会话共用编排

# %% 导入
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
from . import preparation, excitation, phase, acquisition
from .models import StaticSensitivityParams
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, invalidated_stages, validate_selection

EXPERIMENT_ID = preparation.EXPERIMENT_ID
DATA_TYPE = preparation.DATA_TYPE


class Workflow:
    """保留连接与有效前置；每轮原始数据和采集快照独立保存。"""

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(ACQUISITION)
        self.device_session = DeviceSession()
        self.devices, self.channels = {}, {}
        self.directory = self.run_parameters = None
        self.state = preparation.initial_state()
        self.current_step = None
        self.closed = self.sealed = self.clock_failed = False
        self.calibrated_phase = self.dispersion_rate = self.noise_rate = None
        self.dispersion_params = None
        self.demod_mode = "unknown"
        self.outputs = {key: "未知" for key in ("Z", "温控", "Pump", "时序 2")}
        self.outputs["时序 2"] = "保持当前状态"

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；未经额外回读", demod_mode=self.demod_mode,
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}),
            demod_phase_deg=self.calibrated_phase, daq_settings=self.state.get("dispersion_acquisition"))

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message, estimated_remaining_seconds=None,
                                data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.sealed:
            self.state["data_files"] = sorted(path.relative_to(self.directory.root).as_posix()
                for path in self.directory.raw.rglob("*") if path.is_file())
            self.directory.update_config(**self.state)

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(StaticSensitivityParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in ACQUISITION:
            if changed.intersection(stage.parameters):
                invalidate(ACQUISITION, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据",
                              finished_at=datetime.now().astimezone().isoformat())
            self.save()
            self.publish("本轮被重跑替代")
        suffix = 0
        while True:
            tag = self.params.run_tag if not suffix else f"{self.params.run_tag}_{suffix:02d}"
            try:
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.sealed = False
        self.run_parameters = self.params.to_external()
        self.state = preparation.initial_state()
        for key in ("phase_calibration", "hf2_configuration", "hf2_noise_configuration", "dispersion_acquisition", "actual_rates",
                    "temperature_control", "clock_sources", "optional_connection_errors"):
            if key in inherited:
                self.state[key] = inherited[key]
        affected = {stage.id for item in selected if item != "A01"
                    for stage in invalidated_stages(ACQUISITION, item)}
        if previous and "A09" not in affected and self.states["A09"]["status"] == "completed":
            source = previous.raw / "dispersion_scan.npz"
            if not source.is_file():
                raise FileNotFoundError("有效色散前置缺少原始文件，请重新执行 A09")
            shutil.copy2(source, self.directory.raw / source.name)
            points = previous.raw / "dispersion_steps"
            if any(not (points / f"point_{index:04d}.npz").is_file() for index in range(self.params.dispersion_steps)):
                raise FileNotFoundError("有效色散前置缺少完整点波形，请重新执行 A09")
            shutil.copytree(points, self.directory.raw / points.name)
            self.state["dispersion_source_run_id"] = previous.root.name
        self.save()

    def execute(self, selected):
        selected = validate_selection(ACQUISITION, self.states, selected)
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", "A12", FINISH_ID)]
        has_raw = self.directory and any(self.directory.raw.rglob("*.npz"))
        has_noise = self.directory and any(self.directory.raw.rglob("*.npy"))
        rewrites_dispersion = any(item in {stage.id for stage in ACQUISITION[:9]} for item in hardware)
        if hardware and (self.directory is None or self.sealed
                or (has_raw and rewrites_dispersion) or (has_noise and "A11" in hardware)
                or (self.run_parameters != self.params.to_external())):
            self._new_directory(selected)
        self.revision += 1
        for item in selected:
            if item == FINISH_ID:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                invalidate(ACQUISITION, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            failure = None
            try:
                stage = next(stage for stage in ACQUISITION if stage.id == item)
                self.publish(stage.title)
                self._execute_step(item)
                artifacts = [path.relative_to(self.directory.root).as_posix()
                    for name in stage.artifacts for path in self.directory.root.glob(name)] if self.directory else []
                self.states[item] = {"status": "completed", "error": "", "artifacts": artifacts}
            except BaseException as exc:
                failure = exc
                self.states[item] = {"status": "failed", "error": str(exc)}
                self.state["failure_reason"] = str(exc)
                raise
            finally:
                self.current_step = None
                try:
                    self.save()
                    self.publish(f"{item} {self.states[item]['status']}")
                    if item == "A12" and failure is None:
                        self.sealed = True
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
                    self.devices, self.channels = preparation.connect_devices(self.device_session, self.mapping)
                    self.state["optional_connection_errors"] = self.device_session.optional_connection_errors()
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
        elif item == "A03":
            try:
                preparation.synchronize_clocks(self)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            preparation.configure_initial_outputs(self)
            self.outputs.update(Z="OFF", 温控="ON")
        elif item == "A06":
            excitation.configure_pump(self)
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            phase.configure_dispersion(self)
            self.calibrated_phase = None
        elif item == "A08":
            phase.calibrate(self)
        elif item == "A09":
            phase.require_mode(self, "dispersion")
            acquisition.acquire_dispersion(self.params, self.devices["dg_sweep"], self.channels["Z_magnetic_field"],
                self.devices["dg_temp"], self.channels["Temp_Switch"], self.devices["hf2"],
                self.directory.raw / "dispersion_scan.npz")
            self.outputs.update(Z="OFF", 温控="ON")
        elif item == "A10":
            acquisition.prepare_noise(self)
            self.outputs["Z"] = "OFF"
        elif item == "A11":
            phase.require_mode(self, "noise")
            acquisition.acquire_noise(self)
            self.outputs["温控"] = "ON"
        elif item == "A12":
            report = preparation.safe_shutdown(self.dispersion_params, self.devices, self.channels,
                self.calibrated_phase, disconnect_tec=False)
            self.state["safety_shutdown"] = report.to_dict()
            if report.errors:
                raise RuntimeError("安全结束失败：" + "；".join(report.errors))
            self.demod_mode = "dispersion"
            self.outputs.update(Z="OFF", 温控="ON")
            self.state["hf2_final_configuration"] = phase.hf2_snapshot(self.devices["hf2"], self.params.hf2_demod_idx)
            self.state.update(completion_status="completed", finished_at=datetime.now().astimezone().isoformat())
            invalidate(ACQUISITION, self.states, "A10")

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.devices:
            try:
                restore_phase = self.calibrated_phase if self.calibrated_phase is not None else 0.0 if self.dispersion_params else None
                report = preparation.safe_shutdown(self.dispersion_params or self.params, self.devices, self.channels, restore_phase)
                self.state["safety_shutdown"] = report.to_dict()
                errors.extend(report.errors)
                if not errors:
                    self.demod_mode = "dispersion" if restore_phase is not None else self.demod_mode
                    self.outputs.update(Z="OFF", 温控="ON")
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            except Exception as exc:
                errors.append(str(exc))
                self.state["safety_shutdown"] = dict(completed=False, errors=list(errors))
        if not self.sealed:
            if failure is not None:
                self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                                  failure_reason=str(failure))
            elif errors:
                self.state.update(completion_status="failed", failure_reason="安全结束失败：" + "；".join(errors))
            elif self.directory:
                self.state.update(completion_status="failed", failure_reason="本轮采集未完整完成，仅可查看诊断")
            self.state["finished_at"] = datetime.now().astimezone().isoformat()
        self.closed = True
        self.revision += 1
        self.current_step = None
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        try:
            self.save()
            self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全结束失败：" + "；".join(errors))


# %% 完整采集与子进程入口
def run(params: StaticSensitivityParams) -> Path:
    ctx = Workflow(params)
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
    run(load_runtime_params(StaticSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
