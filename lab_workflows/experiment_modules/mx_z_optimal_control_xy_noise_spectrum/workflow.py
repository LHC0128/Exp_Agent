"""XY 噪声网格编排；复用会话、修订、取消与收尾协议。"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import shutil
from ...common import find_project_root
from ...experiment_runtime import load_runtime_params
from ...steps import (create_run_directory, snapshot_main_field_state, DeviceSession, restore_main_field_state)
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    Workflow as ControlWorkflow, run as run_control, _configure_response_demodulator)
from . import preparation, acquisition, run_format
from .models import MxZOptimalControlXYNoiseSpectrumParams
from .stages import ACQUISITION, FINISH_ID, SOURCES, initial_states, invalidate, invalidated_stages, validate_selection

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


class Workflow(ControlWorkflow):
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        root = Path(project_root).resolve() if project_root else find_project_root()
        self.root = root
        self.params, self.mapping = preparation.preflight(params, root)
        self.shutdown_parameters = self.response_parameters = params
        self.sources = self.executed_source = self.main_field_state = None
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
            measurement_mode="xy_bias_noise_spectrum", raw_format_version=run_format.RAW_FORMAT_VERSION,
            completion_status="running")

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请安全结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)
        if self.sources is not None:
            current = preparation._load_control_sources(self.params, self.root)
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
        params, mapping = preparation.preflight(MxZOptimalControlXYNoiseSpectrumParams.from_external(old | values), self.root)
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
            for key in ("actual_rates", "clock_sources", "hf2_configuration", "temperature_control", "device_snapshot",
                    "noise_metric"):
                if key in inherited:
                    self.state[key] = inherited[key]
            if "A04" not in affected and self.states["A04"]["status"] == "completed":
                for name in ("point_manifest.yaml", "xy_bias_grid.npz"):
                    shutil.copy2(previous.raw / name, self.directory.raw / name)
            if "A05" not in affected and self.states["A05"]["status"] == "completed":
                shutil.copytree(previous.raw / "points", self.directory.raw / "points")
                self.state["grid_summary"] = inherited["grid_summary"]
        if self.sources is not None:
            self.state.update(preparation.initial_config(self.params, *self.sources))
            preparation.save_source(self.directory.raw, self.sources)
        self.save()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        common = replace(params, x_dc_field_v=0., y_rf_offset_v=0., phase_cal_rf_amplitude_vpp=0.)
        if item == "A01":
            self.sources = preparation._load_control_sources(params, self.root)
        elif item == "A02":
            self.state.update(preparation.initial_config(params, *self.sources))
            preparation.save_source(self.directory.raw, self.sources)
            if not devices:
                try:
                    self.devices, self.channels = preparation._connect_control_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
                self.main_field_state = snapshot_main_field_state(self.devices["gs200"])
                self.state["device_snapshot"] = preparation._initial_state_snapshot(self.devices, self.channels)
                self.state["device_snapshot"]["main_field_before_configuration"] = asdict(self.main_field_state)
        elif item == "A03":
            corrected, theory, calibration, applied = self.sources
            rate, temperature, clocks, snapshot = preparation._configure_common_outputs(
                common, devices, channels, self.mapping, theory, applied)
            self.shutdown_parameters = self.response_parameters = common
            self.executed_source = self.sources
            self.demod_mode = "response"
            self.state.update(actual_rates={"response_sa_s": rate}, clock_sources=clocks,
                hf2_configuration=snapshot, initial_temperature_c=temperature,
                temperature_control={"target_temperature_c": params.temperature_c, "actual_temperature_c": temperature,
                    "controlled_by_experiment": devices.get("tec") is not None,
                    "control_source": "tec103" if devices.get("tec") is not None else "external_software"})
            self.outputs.update(X="0 V OFF", Z="最优控制ON", **{"Y RF": "纯 DC", "温控": "ON", "Pump": "ON", "共同触发": "ON"})
        elif item == "A04":
            acquisition.initialize_grid(params, self.directory)
        elif item == "A05":
            self.demod_mode = "noise"
            self.state["grid_summary"] = acquisition._acquire_grid(params, self.directory, devices, channels,
                str(self.mapping["lockin_r"]["device_id"]))
            self.outputs.update(X="最后网格点", **{"Y RF": "最后网格点纯 DC", "温控": "ON"})
        elif item == "A06":
            if errors := self._shutdown(disconnect_tec=False):
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=[path.relative_to(self.directory.root).as_posix() for path in sorted(self.directory.raw.rglob("*")) if path.is_file()])
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A03")
        self.save()


    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            if self.demod_mode == "noise":
                try:
                    rate = _configure_response_demodulator(self.response_parameters, self.devices["hf2"])
                    self.state.setdefault("actual_rates", {})["restored_response_sa_s"] = rate
                    self.demod_mode = "response"
                except Exception as exc:
                    errors.append("恢复响应解调失败：" + str(exc))
            try:
                report = preparation.safe_shutdown(self.devices, self.channels, self.shutdown_parameters,
                    disconnect_tec=disconnect_tec)
                self.state["safety_shutdown"] = report.to_dict()
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
            main_errors = []
            if self.main_field_state is not None:
                try:
                    restore_main_field_state(self.devices["gs200"], self.main_field_state)
                except Exception as exc:
                    main_errors.append(str(exc))
            self.state["main_field_restore"] = dict(attempted=self.main_field_state is not None,
                success=not main_errors, errors=main_errors)
            errors.extend(main_errors)
            self.outputs.update(X="OFF" if not errors else "未知", **{"Y RF": "OFF" if not errors else "未知",
                "Z": "保持当前最优控制" if self.executed_source else "未知", "温控": "ON" if not errors else "未知",
                "共同触发": "保持当前状态"})
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        return errors


def run(params, *, project_root=None):
    return run_control(params, project_root=project_root, workflow_type=Workflow)


def main():
    run(load_runtime_params(MxZOptimalControlXYNoiseSpectrumParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
