"""光功率优化完整/分步共用编排；业务采集与安全恢复各有明确归属。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping
from lab_workflows.workflow_state import write_state
from . import preparation, acquisition, run_format
from .models import MxYRFPowerOptimizationParams
from .stages import ACQUISITION, FINISH_ID, RUN_ID, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states
# 兼容已有直接导入；实际业务已移到采集模块。
from .acquisition import _acquire_grid, _set_power_point, _initial_manifest, _write_manifest, acquire_mx_y_rf_point

EXPERIMENT_ID = run_format.EXPERIMENT_ID
DATA_TYPE = run_format.DATA_TYPE
EXECUTION_MODE = "typed_workflow"


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.pump_parameters = self.power_parameters = params
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self.clock_failed = False
        self.demod_mode = "unknown"
        self.state = preparation.initial_config(params)
        self.outputs = {key: "未知" for key in ("X", "Y RF", "Z", "温控", "Pump", "Pump/Probe 光功率")}

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
            (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请安全结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="初态单独回读；输出为最近成功模块的写入结果，未经额外回读",
            demod_mode=self.demod_mode, safety_shutdown=self.state.get("safety_shutdown"),
            clock_sources=self.state.get("clock_sources", {}),
            daq_settings={"actual_response_rate_sa_s": self.state.get("actual_rates", {}).get("response_sa_s")})

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.round_sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def save(self):
        if self.directory and not self.round_sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        self.recheck()
        old = self.params.to_external()
        if unknown := values.keys() - old.keys():
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping = preparation.preflight(MxYRFPowerOptimizationParams.from_external(old | values), self.root)
        for stage in self.stages:
            if any(old[key] != params.to_external()[key] for key in stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块，尚未执行的草稿不改变收尾基线")

    def _new_directory(self):
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
        self.run_parameters = self.params.to_external()
        self.state = preparation.initial_config(self.params)
        for key in ("clock_sources", "device_snapshot", "temperature_control", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        if previous:
            self.state["source_run_id"] = previous.root.name
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = set(selected) - {"A01", "A09", FINISH_ID}
        if hardware and (self.directory is None or self.round_sealed
                or self.run_parameters != self.params.to_external()
                or (RUN_ID in selected and self.states[RUN_ID]["status"] != "pending")):
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
            self.states[item] = {"status": "running", "error": ""}
            failure = None
            try:
                self.publish(next(stage.title for stage in self.stages if stage.id == item))
                self._execute_step(item)
                stage = next(stage for stage in self.stages if stage.id == item)
                artifacts = [p.relative_to(self.directory.root).as_posix()
                    for pattern in stage.artifacts for p in self.directory.root.glob(pattern) if p.is_file()] if self.directory else []
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
                    if item == "A09" and failure is None:
                        self.round_sealed = True
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
                    self.devices, self.channels = preparation.connect_mx_y_rf_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
            self.state["device_snapshot"] = preparation.snapshot_mx_y_rf_state(self.devices, self.channels)
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.synchronize_clocks(devices, self.mapping)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            preparation.configure_initial_outputs(params, devices, channels, self.mapping)
            self.power_parameters = params
            self.outputs.update(X="OFF", Z="OFF", **{"Y RF": "OFF", "温控": "ON", "Pump/Probe 光功率": "基准 ON"})
        elif item == "A05":
            self.state["temperature_control"] = preparation.configure_temperature_control(
                devices.get("tec"), params.temperature_c, channel=1).to_dict()
        elif item == "A06":
            preparation.configure_pump(params, devices, channels)
            self.pump_parameters = params
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            self.state.setdefault("actual_rates", {})["response_sa_s"] = preparation.configure_response(params, devices, channels)
            self.demod_mode = "response"
        elif item == RUN_ID:
            self.demod_mode = "unknown"
            self.state["grid_summary"] = acquisition._acquire_grid(params, self.directory, devices, channels,
                self.state["actual_rates"]["response_sa_s"], str(self.mapping["lockin_r"]["device_id"]))
            self.demod_mode = "noise"
            self.outputs.update(**{"Y RF": "OFF", "温控": "ON", "Pump/Probe 光功率": "末个扫描点 ON"})
        elif item == "A09":
            self._shutdown(disconnect_tec=False)
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat(),
                data_files=["raw/power_grid.npz", "raw/point_manifest.yaml"])
            # 本轮已关闭 RF；再次采集必须显式重新准备，不覆盖旧网格。
            invalidate(self.stages, self.states, "A07")

    def _shutdown(self, *, disconnect_tec=True):
        self.state.update(preparation.shutdown(self.devices, self.channels,
            self.pump_parameters, self.power_parameters, disconnect_tec=disconnect_tec))
        success = self.state["safety_shutdown"]["completed"]
        self.outputs.update({key: "OFF" if success else "未知" for key in ("X", "Y RF", "Z")})
        self.outputs.update({"温控": "ON" if success else "未知",
            "Pump/Probe 光功率": "基准 ON" if self.state["laser_power_restore"]["success"] else "未知"})
        if not success:
            raise RuntimeError("安全收尾失败：" + "；".join(self.state["safety_shutdown"]["errors"]))

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        try:
            if self.devices:
                try:
                    if self.round_sealed:
                        errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                    else:
                        self._shutdown()
                except Exception as exc:
                    errors.append(str(exc))
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            if not self.round_sealed:
                reason = str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成"
                self.state.update(completion_status="cancelled" if isinstance(failure, WorkflowCancelled) else "failed",
                    failure_reason=reason, finished_at=datetime.now().isoformat())
                self.save()
        except Exception as exc:
            errors.append(str(exc))
        self.closed, self.current_step = True, None
        self.revision += 1
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        self.states[FINISH_ID] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
        try:
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100, data={"estimated_remaining_seconds": None})
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params, *, project_root=None):
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


def main():
    run(load_runtime_params(MxYRFPowerOptimizationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
