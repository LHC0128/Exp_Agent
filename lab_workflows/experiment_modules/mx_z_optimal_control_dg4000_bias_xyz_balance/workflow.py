"""DG4000 Z 偏置完整运行与分步会话共用的编排。"""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil

from ...common import find_project_root
from ...experiment_runtime import load_runtime_params
from ...steps import (
    DeviceSession,
    create_run_directory,
)
from ...steps.dg_channel_state import restore_dg_channel, snapshot_dg_channel
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    Workflow as ControlWorkflow,
    run as run_control,
)
from . import acquisition, preparation, run_format
from .excitation import _XYZBiasController
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams
from .stages import ACQUISITION, FINISH_ID, SOURCES, invalidate, invalidated_stages, validate_selection
from lab_workflows.workflow_stages import initial_states

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


class Workflow(ControlWorkflow):
    """复用已验收的串行执行、修订、取消、发布与 EOF 收尾协议。"""

    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.shutdown_parameters = self.response_parameters = params
        self.sources = self.executed_source = None
        self.channel_states: dict = {}
        self.demod_mode = "unknown"
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = False
        self.quality_error = self.progress = None
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump", "共同触发")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running")

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请安全结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)
        if self.sources is not None:
            current = preparation.load_source(self.params, self.root)
            if preparation.source_fingerprint(current) != preparation.source_fingerprint(self.sources):
                invalidate(self.stages, self.states, "A01")
                self.sources = None
                self.revision += 1
                raise ValueError("控制来源文件已改变，请从 A01 重新预检冻结")

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(
            MxZOptimalControlDG4000BiasXYZBalanceParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        if changed.intersection(SOURCES):
            self.sources = None
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
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.run_parameters = self.params.to_external()
        self.state = self._initial_state()
        affected = {stage.id for item in selected if item != "A01" for stage in invalidated_stages(self.stages, item)}
        if previous:
            self.state["source_run_id"] = previous.root.name
            for key in ("actual_rates", "clock_sources", "hf2_configuration", "temperature_control", "device_snapshot"):
                if key in inherited:
                    self.state[key] = inherited[key]
            if "A04" not in affected and self.states["A04"]["status"] == "completed":
                for path in previous.raw.glob("*.npz"):
                    if path.name.startswith("grid_") or path.name == "xyz_balance_scan.npz":
                        shutil.copy2(path, self.directory.raw / path.name)
                for key in ("measured_grid_minimum",):
                    if key in inherited:
                        self.state[key] = inherited[key]
        if self.sources is not None:
            self.state.update(preparation.initial_config(self.params, *self.sources))
            preparation.save_source(self.directory.raw, self.sources)
        self.save()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        if item == "A01":
            self.sources = preparation.load_source(params, self.root)
        elif item == "A02":
            self.state.update(preparation.initial_config(params, *self.sources))
            preparation.save_source(self.directory.raw, self.sources)
            if not devices:
                try:
                    self.devices, self.channels = preparation._connect_control_devices(
                        self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
                self.channel_states = {
                    name: snapshot_dg_channel(self.devices[device], self.channels[name])
                    for name, device in (("z_field", "z_field"), ("trigger", "z_field"),
                        ("x_field", "xy_field"), ("y_rf", "xy_field"))}
                self.state["device_snapshot"] = preparation._initial_state_snapshot(self.devices, self.channels)
                self.state["device_snapshot"]["gs200"] = {"used": False, "physically_disconnected": True}
                self.state["device_snapshot"]["channels_before_configuration"] = {
                    name: asdict(state) for name, state in self.channel_states.items()}
        elif item == "A03":
            _, theory, _, applied = self.sources
            rate, temperature, clocks, snapshot = preparation.configure_workpoint(
                params, devices, channels, self.mapping, theory, applied)
            self.shutdown_parameters = self.response_parameters = params
            self.executed_source = self.sources
            self.demod_mode = "response"
            self.state.update(actual_rates={"response_sa_s": rate}, clock_sources=clocks,
                hf2_configuration=snapshot, initial_temperature_c=temperature,
                temperature_control={"target_temperature_c": params.temperature_c, "actual_temperature_c": temperature,
                    "controlled_by_experiment": devices.get("tec") is not None,
                    "control_source": "tec103" if devices.get("tec") is not None else "external_software"})
            self.outputs.update(X="0 V OFF", Z="最优控制ON", **{
                "Y RF": "Y 0 V OFF", "温控": "ON", "Pump": "ON", "共同触发": "ON"})
        elif item == "A04":
            _, _, calibration, applied = self.sources
            controller = _XYZBiasController(devices["xy_field"], devices["z_field"], channels,
                applied, applied.amplitude_vpp)
            _, best = acquisition._acquire_grid(params, self.directory, devices, channels, controller,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]),
                calibration, applied)
            self.state["measured_grid_minimum"] = {key: best[key] for key in (
                "z_index", "x_index", "y_index", "z_bias_v", "x_field_v", "y_field_v",
                "r_mean_v", "r_std_v", "acquisition_index")}
            self.state["measured_grid_minimum"]["remeasured"] = False
            self.state["data_files"] = [
                path.relative_to(self.directory.root).as_posix()
                for path in sorted(self.directory.raw.rglob("*")) if path.is_file()]
            self.outputs.update(X="最后网格点", Z="最后偏置与最优控制", **{"Y RF": "Y 最后网格点", "温控": "ON"})
        elif item == "A05":
            if errors := self._shutdown(disconnect_tec=False):
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None,
                finished_at=datetime.now().isoformat(),
                data_files=[path.relative_to(self.directory.root).as_posix()
                    for path in sorted(self.directory.raw.rglob("*")) if path.is_file()])
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A03")
        self.save()

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            try:
                report = preparation.safe_shutdown(self.devices, self.channels,
                    self.shutdown_parameters, disconnect_tec=disconnect_tec)
                self.state["safety_shutdown"] = report.to_dict()
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
            channel_errors = {}
            for name, device in (("y_rf", "xy_field"), ("x_field", "xy_field"),
                                 ("z_field", "z_field"), ("trigger", "z_field")):
                if name not in self.channel_states:
                    continue
                try:
                    restore_dg_channel(self.devices[device], self.channels[name], self.channel_states[name])
                except Exception as exc:
                    channel_errors[name] = f"{name}: {exc}"
            for key, names in (("xyz_restore", ("x_field", "y_rf")),
                               ("z_channel_restore", ("z_field",)),
                               ("trigger_channel_restore", ("trigger",))):
                failed = [channel_errors[n] for n in names if n in channel_errors]
                self.state[key] = dict(attempted=any(n in self.channel_states for n in names),
                    success=not failed, errors=failed)
            errors.extend(channel_errors.values())
            self.state["device_disconnect"] = dict(gs200_used=False,
                tec_disconnected=disconnect_tec and not errors, errors=list(errors))
            self.outputs.update(X="运行前原态" if not errors else "未知", **{
                "Y RF": "Y 运行前原态" if not errors else "未知",
                "Z": "运行前原态" if not errors else "未知",
                "温控": "ON" if not errors else "未知", "共同触发": "运行前原态" if not errors else "未知"})
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {},
                completed=False, errors=errors)
        return errors


def run(params, *, project_root=None):
    return run_control(params, project_root=project_root, workflow_type=Workflow)


def main():
    run(load_runtime_params(MxZOptimalControlDG4000BiasXYZBalanceParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
