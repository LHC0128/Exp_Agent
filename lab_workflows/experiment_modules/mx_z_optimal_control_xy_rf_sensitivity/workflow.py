"""XY 网格编排；沿用已验收的会话、修订、取消和安全收尾。"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import shutil
import numpy as np
from ...common import find_project_root
from ...experiment_runtime import load_runtime_params
from ...steps import create_run_directory, snapshot_main_field_state
from ..mx_z_optimal_control_rf_sensitivity.workflow import Workflow as ControlWorkflow, run as run_control
from ..mx_z_optimal_control_rf_sensitivity.workflow import _acquire_phase_scan
from . import preparation, acquisition, run_format
from .models import MxZOptimalControlXYRFSensitivityParams
from .stages import ACQUISITION, FINISH_ID, SOURCES, invalidate, invalidated_stages, validate_selection

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


class Workflow(ControlWorkflow):
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        root = Path(project_root).resolve() if project_root else find_project_root()
        params, _ = preparation.preflight(params, root)
        super().__init__(params, project_root=root, session_id=session_id)
        self.sources = None

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            measurement_mode="xy_bias_grid_rf_sensitivity", raw_format_version=run_format.RAW_FORMAT_VERSION,
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
        params, mapping = preparation.preflight(MxZOptimalControlXYRFSensitivityParams.from_external(old | values), self.root)
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
                    "phase_calibration", "selected_y_rf_phase_deg"):
                if key in inherited:
                    self.state[key] = inherited[key]
            if "A04" not in affected and self.states["A04"]["status"] == "completed":
                for path in previous.raw.glob("phase*.npz"):
                    shutil.copy2(path, self.directory.raw / path.name)
                for name in ("phase_calibration.yaml", "phase_calibration.png"):
                    if (path := previous.results / name).is_file():
                        shutil.copy2(path, self.directory.results / name)
            if "A05" not in affected and self.states["A05"]["status"] == "completed":
                for name in ("point_manifest.yaml", "xy_bias_grid.npz"):
                    shutil.copy2(previous.raw / name, self.directory.raw / name)
                shutil.copytree(previous.raw / "points", self.directory.raw / "points")
                self.state["grid_summary"] = inherited["grid_summary"]
        if self.sources is not None:
            self.state.update(preparation.initial_config(self.params, *self.sources))
            preparation.save_source(self.directory.raw, self.sources)
        self.save()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        center = replace(params, x_dc_field_v=params.phase_calibration_point()[0],
            y_rf_offset_v=params.phase_calibration_point()[1])
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
                center, devices, channels, self.mapping, theory, applied)
            self.shutdown_parameters = self.response_parameters = center
            self.executed_source = self.sources
            self.demod_mode = "response"
            self.state.update(actual_rates={"response_sa_s": rate}, clock_sources=clocks,
                hf2_configuration=snapshot, initial_temperature_c=temperature,
                temperature_control={"target_temperature_c": params.temperature_c, "actual_temperature_c": temperature,
                    "controlled_by_experiment": devices.get("tec") is not None,
                    "control_source": "tec103" if devices.get("tec") is not None else "external_software"})
            self.outputs.update(X="扫描中心", Z="最优控制ON", **{"Y RF": "等待触发", "温控": "ON", "Pump": "ON", "共同触发": "ON"})
        elif item == "A04":
            self.selected_phase, payload = _acquire_phase_scan(center, self.directory, devices, channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            if not np.isfinite(self.selected_phase):
                raise RuntimeError("中心 XY 补偿点 RF 相位校准未得到有效相位")
            self.state.update(phase_calibration=payload, selected_y_rf_phase_deg=float(self.selected_phase))
        elif item == "A05":
            self.demod_mode = "noise"
            self.state["grid_summary"] = acquisition._acquire_grid(params, self.directory, devices, channels,
                str(self.mapping["lockin_r"]["device_id"]), self.state["actual_rates"]["response_sa_s"], self.selected_phase)
        elif item == "A06":
            if errors := self._shutdown(disconnect_tec=False):
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=[path.relative_to(self.directory.root).as_posix() for path in sorted(self.directory.raw.rglob("*")) if path.is_file()])
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A03")
        self.save()


def run(params, *, project_root=None):
    return run_control(params, project_root=project_root, workflow_type=Workflow)


def main():
    run(load_runtime_params(MxZOptimalControlXYRFSensitivityParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
