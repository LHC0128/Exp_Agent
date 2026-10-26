"""完整与分步共用真实 DirectAW 业务步骤；不在导入时读取参数或连接硬件。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import time

from ...common import find_project_root, load_mapping, load_safety_limits, validate_safety_limit, WorkflowCancelled
from ...devices import signal_generator_max_arb_points
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot, validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import DIRECT_AW as REQUIRED_MAPPING_KEYS
from ...steps import (DeviceSession, create_run_directory, set_temperature_switch, run_safety_shutdown,
    DGChannelShutdown, TemperatureSwitchRestore, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS,
    disconnect_device_mapping)
from lab_workflows.workflow_state import write_state
from .models import XYDirectAWDCCalibrationParams
from . import preparation, excitation, phase, acquisition
from .stages import ACQUISITION, RUN_STEP_ID, FINISH_ID, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

EXPERIMENT_ID, DATA_TYPE = "xy-direct-aw-dc-calibration", "XY_DirectAW_DC_Calibration"


def preflight(params, root):
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    limits = load_safety_limits(root)
    ctx = SimpleNamespace(params=params, project_root=Path(root), MAPPING=mapping, LIMITS=limits,
                          MAX_ARB_POINTS=signal_generator_max_arb_points(mapping["X_magnetic_field"]))
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field", "X_magnetic_field_AM", "Y_magnetic_field_AM", "Time_sequence_2", "Temp_Switch"):
        if any(limits.get(key, {}).get(bound) is None for bound in ("min", "max")):
            raise ValueError(f"{key} 缺少明确安全上下限")
        validate_safety_limit(key, 0.0, limits)
    validate_safety_limit("Temp_Switch", 5.0, limits)
    preparation.settings(ctx)
    if True:
        key = f"{mapping['lockin_r']['device_library_id']}_aux_output_{ctx.AUXOUT_INDEX}"
        if any(limits.get(key, {}).get(bound) is None for bound in ("min", "max")):
            raise ValueError(f"{key} 缺少明确安全上下限")
        validate_safety_limit(key, ctx.AUXOUT_OFFSET_V, limits)
    if params.z_rf_freq_start_hz <= 0:
        raise ValueError("Z 射频扫描起始频率必须大于零")
    for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
        for value in (ctx.XY_TRIGGER_OFFSET_V - ctx.XY_TRIGGER_AMPLITUDE_Vpp/2, ctx.XY_TRIGGER_OFFSET_V + ctx.XY_TRIGGER_AMPLITUDE_Vpp/2):
            validate_safety_limit(key, value, limits)
    wave = excitation.build_direct_aw(ctx, ctx.XY_PHASE_CAL_ENVELOPE_V, ctx.XY_CTRL_PHASE_INITIAL_deg)
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        for value in (float(wave["x_waveform_V"].min()), float(wave["x_waveform_V"].max()), -wave["aw_vpp"]/2, wave["aw_vpp"]/2):
            validate_safety_limit(key, value, limits)
    return params, mapping


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = self.project_root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.MAPPING = preflight(params, self.root)
        self.configuration_revision = self._configuration_revision()
        self.LIMITS = load_safety_limits(self.root)
        self.MAX_ARB_POINTS = signal_generator_max_arb_points(self.MAPPING["X_magnetic_field"])
        preparation.settings(self)
        self.PARAMS = self.params
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.session = {}, DeviceSession()
        self.directory = self.current_step = self.run_parameters = None
        self.closed = self.acquired = self.connection_failed = False
        self.config = {}
        self.clock_sources = self.clock_verification = {}
        self.outputs = {key: "未知" for key in ("温控", "X/Y DirectAW", "Z RF", "外触发")}

    def _configuration_revision(self):
        value = instrument_config_snapshot(self.root)
        return (value["device_library_revision"], value["physical_mapping_revision"],
                (self.root/"params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.configuration_revision:
            raise ValueError("设备库、映射或安全限值已改变，请先结束会话")
        self.params, self.MAPPING = preflight(self.params, self.root)

    def wait(self, seconds):
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline-time.monotonic())))

    def switch(self, enabled):
        set_temperature_switch(self.dg_temp, enabled, channel=2)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功业务步骤的写入结果；未经额外回读的输出不作为实测值", demod_mode="DirectAW",
            safety_shutdown=self.config.get("safety_shutdown"), clock_sources=self.clock_sources or self.clock_verification)

    def publish(self, message="等待下一模块；当前输出和硬件锁保持"):
        value = self.snapshot()
        write_state(self.root/"data/.gui_jobs"/self.session_id/"session.json", value)
        if self.directory:
            write_state(self.directory.root/"acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("执行中不能应用参数")
        self.recheck()
        before = self.params.to_external()
        if unknown := values.keys()-before.keys():
            raise ValueError("未知参数："+"、".join(sorted(unknown)))
        params, mapping = preflight(type(self.params).from_external(before | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != before[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.PARAMS, self.MAPPING = params, params, mapping
        preparation.settings(self)
        self.revision += 1
        self.publish("草稿已应用；只失效相关模块，不自动配置仪器或重跑")

    def _new_directory(self):
        if self.directory:
            self.directory.update_config(completion_status="failed", failure_reason="被显式重跑替代；原始数据保留")
        self.directory = create_run_directory(DATA_TYPE, self.params.run_tag, self.params.to_external(),
            schema_version=self.params.schema_version, project_root=self.root)
        self.run_dir, self.raw_dir, self.results_dir = self.directory.root, self.directory.raw, self.directory.results
        self.config_path = self.directory.config_path
        (self.raw_dir/"recovery").mkdir()
        self.config = deepcopy(self.config) | dict(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE,
            parameters=self.params.to_external(), schema_version=self.params.schema_version,
            completion_status="running", data_files=[])
        inherited = {key: deepcopy(self.config[key]) for key in ("calibration", "hf2_actual_config", "actual_rates") if key in self.config}
        if hasattr(self, "actual_rate_d0_meas"):
            preparation.write_configuration(self)
            self.config.update(inherited)
            self.config.update(completion_status="running", data_files=[])
        self.directory.update_config(**self.config)
        self.run_parameters, self.acquired = self.params.to_external(), False

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
                self.publish(next(s.title for s in self.stages if s.id == item))
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
                    preparation.connect(self)
                except BaseException:
                    self.connection_failed = True
                    raise
            else:
                try:
                    clocks = preparation.synchronize_connected_clocks(self.devices, self.MAPPING, {
                        "dg_comp": "X_magnetic_field", "dg_trigger": "X_magnetic_field_AM",
                        "dg_sweep": "Z_magnetic_field", "dg_mod": "Pump_modulation",
                        "dg_laser": "Pump_laser_power", "dg_temp": "Temp_Switch", "hf2": "lockin_r"})
                    self.clock_sources = self.clock_verification = clocks
                except BaseException:
                    self.connection_failed = True
                    raise
        elif item == "A03":
            preparation.initial_outputs(self)
            pass
            self.outputs["温控"] = "5 V DC + ON"
        elif item == "A04":
            phase.configure_lockin(self)
        elif item == "A05":
            phase.calibrate_xy(self)
            preparation.write_configuration(self)
            self.outputs["X/Y DirectAW"] = "按已记录校相结果输出"
            self.outputs["外触发"] = "连续方波 ON"
        elif item == RUN_STEP_ID:
            acquisition.acquire_scan(self)
            self.acquired = True
            self.config["data_files"] = sorted(p.relative_to(self.run_dir).as_posix() for p in self.raw_dir.rglob("*")
                if p.is_file() and "recovery" not in p.relative_to(self.raw_dir).parts)
        if self.directory:
            if hasattr(self, "temperature_status"):
                self.config["temperature_control"] = dict(target_C=self.FIXED_PARAMS["temperature"],
                    actual_C=self.temperature_status.actual_temperature_c,
                    controlled_by_experiment=self.temperature_status.controlled_by_experiment,
                    control_source=self.temperature_status.control_source)
            self.config.update(completion_status="running", parameters=self.run_parameters)
            self.directory.update_config(**self.config)

    def finish(self, failure=None):
        if self.closed:
            return
        cleanup_failure = None
        try:
            report = self._shutdown()
            self.config["safety_shutdown"] = report.to_dict()
            if report.errors:
                cleanup_failure = RuntimeError("安全关闭失败："+"；".join(report.errors))
            for key, name, value in (("温控", "dg_temp", "5 V DC + ON"),
                    ("X/Y DirectAW", "dg_comp", "0 V + OFF"), ("Z RF", "dg_sweep", "0 V + OFF"),
                    ("外触发", "dg_trigger", "0 V + OFF")):
                if self.devices.get(name, getattr(self, name, None)) is not None:
                    self.outputs[key] = "未知" if report.errors else value
            if self.connection_failed:
                errors = disconnect_device_mapping({str(i): device for i, device in enumerate(self.session.unique_devices())})
                if errors and cleanup_failure is None:
                    cleanup_failure = RuntimeError("；".join(errors))
        except BaseException as exc:
            cleanup_failure = exc
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if cleanup_failure else "completed", error=str(cleanup_failure or ""))
        self.config.update(completion_status="completed" if self.acquired and failure is None and cleanup_failure is None else "failed",
            failure_reason=str(failure or cleanup_failure) if failure or cleanup_failure else None)
        try:
            if self.directory:
                self.directory.update_config(**self.config)
            self.publish("会话已结束；已尝试安全收尾，错误及保留输出见记录")
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = exc
            else:
                getattr(cleanup_failure, "add_note", print)(f"收尾配置保存失败：{exc}")
        if cleanup_failure is not None:
            if failure is None:
                raise cleanup_failure
            getattr(failure, "add_note", print)(f"安全收尾亦失败：{cleanup_failure}")

    def _shutdown(self):
        channels = []
        for name, channel, key in [["dg_sweep",1,"Z_magnetic_field"],["dg_sweep",2,"Time_sequence_2"],["dg_comp",1,"X_magnetic_field"],["dg_comp",2,"Y_magnetic_field"],["dg_trigger",1,"X_magnetic_field_AM"],["dg_trigger",2,"Y_magnetic_field_AM"],["dg_mod",2,"Time_sequence"]]:
            if (device := self.devices.get(name, getattr(self, name, None))) is not None:
                channels.append(DGChannelShutdown(device, channel, key, key))
        temperature = self.devices.get("dg_temp", getattr(self, "dg_temp", None))
        return run_safety_shutdown(dg_channels=channels,
            temperature_switch=TemperatureSwitchRestore(temperature, 2) if temperature is not None else None,
            disconnect_targets=(DisconnectTarget("TEC", self.devices.get("tec")),),
            preserved_outputs=STANDARD_PRESERVED_OUTPUTS)


def run(params: XYDirectAWDCCalibrationParams, *, project_root=None) -> Path:
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
    run(load_runtime_params(XYDirectAWDCCalibrationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
