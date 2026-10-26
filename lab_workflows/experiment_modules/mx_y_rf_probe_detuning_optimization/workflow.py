"""完整运行与分步会话共用的 Probe/PZT 实验编排。"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping
from lab_workflows.workflow_state import write_state
from ..mx_y_rf_sensitivity import excitation
from ..mx_y_rf_sensitivity.acquisition_steps import RPointQualityError, acquire_mx_y_rf_point
from . import acquisition, preparation, run_format
from .models import MxYRFProbeDetuningOptimizationParams
from .stages import ACQUISITION, FINISH_ID, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"

# 保留旧测试/调用方的薄入口，业务只在 acquisition 中实现。
_set_probe_power = acquisition._set_probe_power
_set_pzt_row = acquisition._set_pzt_row
_read_pzt_point = acquisition._read_pzt_point


def _acquire_grid(*args, **kwargs):
    return acquisition._acquire_grid(*args, **kwargs, point_reader=acquire_mx_y_rf_point,
        set_probe=_set_probe_power, set_pzt_row=_set_pzt_row, read_pzt=_read_pzt_point)


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.power_parameters = self.pump_parameters = self.response_parameters = self.params
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.laser_state = None
        self.closed = self.round_sealed = self.clock_failed = False
        self.needs_preparation = False
        self.demod_mode = "unknown"
        self.state = self._initial_state()
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump", "PZT/扫描")}

    def _initial_state(self):
        return dict(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, completion_status="running",
            scan_mode="nested_scan", scan_order="pzt_outer_probe_inner_serpentine",
            objective="minimize flat_median_ft_per_sqrt_hz", acquisition_signals=["R"],
            linewidth_mode="amplitude_equivalent",
            geometry=dict(main_field="Z", pump="Z", probe="X", rf_field="Y", x_field_output="OFF"),
            detuning_axis=dict(control="DLC pro Laser 1 Scan Offset", unit="V", frequency_calibrated=False),
            compatibility=dict(reference_experiment_id="mx-y-rf-power-optimization", schema_version=self.params.schema_version),
            temperature_control=dict(tec_connected=False, tec_target_temperature_configured=False,
                temperature_stability_waited=False, temperature_switch_controlled=True,
                temperature_switch_restored_on_exit=True))

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
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
            output_source="连接初态来自回读；后续为最近成功模块写入结果，恢复失败时状态未知",
            demod_mode=self.demod_mode, safety_shutdown=self.state.get("safety_shutdown"),
            clock_sources=self.state.get("clock_sources", {}), daq_settings=self.state.get("actual_rates"))

    def publish(self, message="等待选择下一模块；设备连接和硬件锁仍保持"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

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
        params, mapping = preparation.preflight(MxYRFProbeDetuningOptimizationParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块")

    def _new_directory(self):
        previous, inherited = self.directory, deepcopy(self.state)
        if previous and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        suffix = 0
        while True:
            try:
                tag = self.params.run_tag + (f"_{suffix:02d}" if suffix else "")
                self.directory = create_run_directory(DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.round_sealed = False
        self.run_parameters = self.params.to_external()
        self.state = self._initial_state()
        for key in ("device_snapshot", "clock_sources", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        if previous:
            self.state["source_run_id"] = previous.root.name
        self.save()

    def execute(self, selected):
        if self.current_step:
            raise ValueError("已有模块正在执行")
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        if "A07" in selected and self.needs_preparation and "A06" not in selected:
            raise ValueError("PZT/响应模式已恢复，请显式选择 A06 重新准备")
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
                self.publish(next(stage.title for stage in self.stages if stage.id == item))
                self._execute_step(item)
                stage = next(stage for stage in self.stages if stage.id == item)
                artifacts = [path.relative_to(self.directory.root).as_posix()
                    for pattern in stage.artifacts for path in self.directory.root.glob(pattern)
                    if path.is_file()] if self.directory else []
                self.states[item] = dict(status="completed", error="", artifacts=artifacts,
                    parameters=self.params.to_external(), revision=self.revision)
                self.save()
                self.publish(f"{item} 完成")
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
                self.states[item] = dict(status=status, error=str(exc))
                self.state.update(failure_reason=str(exc), completion_status=status)
                raise
            finally:
                self.current_step = None
                try:
                    self.save()
                    self.publish(f"{item} {self.states[item]['status']}")
                    if item == "A08" and failure is None:
                        # 空闲快照保存后再封存，后续草稿不改写本轮记录。
                        self.round_sealed = True
                except Exception as save_error:
                    if failure is None:
                        raise
                    if hasattr(failure, "add_note"):
                        failure.add_note(f"阶段状态保存另失败: {save_error}")
        return self.snapshot()

    def _execute_step(self, item):
        params, devices, channels = self.params, self.devices, self.channels
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not devices:
                self.devices, self.channels, self.laser_state, snapshot = preparation.connect(self.mapping, self.device_session)
                self.state["device_snapshot"] = snapshot
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.rf_preparation._configure_reference_clocks(devices, self.mapping)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            # 提前记录已尝试的设置，部分配置失败时也使用同一安全基线。
            self.power_parameters = params
            preparation.rf_preparation.configure_initial_outputs(params, devices, channels, self.mapping)
            self.outputs.update(X="OFF", Z="OFF", **{"Y RF": "OFF", "温控": "ON"})
        elif item == "A05":
            self.pump_parameters = params
            excitation.configure_pump(params, devices, channels)
            self.outputs["Pump"] = "ON"
        elif item == "A06":
            preparation._prepare_probe_laser_scan(devices["probe_laser_controller"])
            self.response_parameters = params
            self.state.setdefault("actual_rates", {})["response_sa_s"] = excitation.configure_response(params, devices, channels)
            self.demod_mode = "response"
            self.needs_preparation = False
            self.outputs["PZT/扫描"] = "扫描 OFF、幅度 0；PZT 待扫描"
        elif item == "A07":
            self.demod_mode = "noise"
            self.state["grid_summary"] = _acquire_grid(params, self.directory, devices, channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            self.outputs.update(**{"Y RF": "OFF", "温控": "ON", "PZT/扫描": "最近扫描点；扫描 OFF"})
        elif item == "A08":
            errors = self._shutdown()
            if errors:
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=["raw/probe_detuning_grid.npz", "raw/point_manifest.yaml"])

    def _shutdown(self):
        errors = []
        if self.devices:
            if self.demod_mode == "noise":
                try:
                    self.state.setdefault("actual_rates", {})["response_sa_s"] = excitation._restore_response_acquisition_state(
                        self.response_parameters, self.devices, self.channels)
                    self.demod_mode = "response"
                except Exception as exc:
                    errors.append(f"响应模式恢复失败: {exc}")
            params = replace(self.pump_parameters, pump_laser_power_v=self.power_parameters.pump_laser_power_v,
                probe_laser_power_v=self.power_parameters.probe_laser_power_v)
            try:
                report, restore = preparation.shutdown(self.devices, self.channels, params, self.laser_state)
                power_errors = [error for error in report.errors if error.startswith("光功率基准恢复:")]
                power_restore = dict(pump_power_v=params.pump_laser_power_v,
                    probe_power_v=params.probe_laser_power_v, outputs_on=True)
                self.state.update(safety_shutdown=report.to_dict(),
                    probe_laser_restore=restore | {"guard_errors": [] if restore["success"] else [restore["error"]]},
                    power_restore=power_restore,
                    laser_power_restore=power_restore | {"success": not power_errors, "error": "；".join(power_errors) or None},
                    device_disconnect=dict(tec_connected=False, tec_disconnect_attempted=False,
                        other_devices_preserved=True, errors=list(report.disconnect_errors)))
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
        self.needs_preparation = True
        self.outputs.update(X="未知" if errors else "OFF", Z="未知" if errors else "OFF",
            **{"Y RF": "未知" if errors else "OFF", "温控": "未知" if errors else "ON",
               "PZT/扫描": "未知" if errors else "已恢复进入会话前状态"})
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        return errors

    def finish(self, failure=None):
        if self.closed:
            return
        errors = [] if self.round_sealed else self._shutdown()
        if self.clock_failed:
            errors.extend(disconnect_device_mapping(self.devices))
        if not self.round_sealed:
            reason = str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成"
            if errors:
                reason += "；安全恢复失败：" + "；".join(errors)
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                failure_reason=reason, finished_at=datetime.now().isoformat())
        self.closed = True
        self.current_step = None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        try:
            self.save()
            self.publish("会话已结束；安全恢复完成" if not errors else "会话已结束；输出状态未知，请检查仪器")
            report_runtime_progress("finished", "采集会话已结束", 100, data={"estimated_remaining_seconds": None})
        except Exception as save_error:
            if failure is None and not errors:
                raise
            errors.append(f"收尾状态保存失败: {save_error}")
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))
        if errors and hasattr(failure, "add_note"):
            failure.add_note("安全收尾另失败：" + "；".join(errors))


def run(params: MxYRFProbeDetuningOptimizationParams, *, project_root=None) -> Path:
    ctx = Workflow(params, project_root=project_root)
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
    run(load_runtime_params(MxYRFProbeDetuningOptimizationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
