"""完整运行和分步会话共用的投影噪声编排，不包含仪器或科学算法。"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps.state import StateGuard
from ...steps import (DeviceSession, ScopeAutoRangeState, create_run_directory,
                      snapshot_main_field_state, restore_main_field_state, disconnect_device_mapping)
from lab_workflows.workflow_state import write_state
from . import preparation, acquisition
from .models import ProjectionNoiseParams
from .run_format import EXPERIMENT_ID, DATA_TYPE
from .stages import ACQUISITION, FINISH_ID, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

EXECUTION_MODE = "typed_workflow"
# 保留旧工作流导入边界；实际业务仍在相应职责模块。
_scope_settings = preparation._scope_settings
safe_shutdown = preparation.safe_shutdown
_save_frame = acquisition._save_frame
_save_acquisition_index = acquisition._save_acquisition_index


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.source_revision = preparation.source_revision(params, self.root)
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.stages)
        self.devices, self.channels = {}, {}
        self.device_session = DeviceSession()
        self.main_field_guard = StateGuard()
        self.main_field_state = None
        self.scope_config = self.auto_range = None
        self.directory = self.run_parameters = self.current_step = None
        self.closed = self.round_sealed = self.clock_failed = False
        self.records = []
        self.state = preparation.initial_state(params, self.root)
        self.outputs = {key: "未知" for key in ("GS200", "X", "Y", "Z", "温控", "SDS", "Pump 光功率", "Probe 光功率")}
        self.outputs.update({"Pump 载波": "保持当前状态（本实验不写入）", "Pump 门控": "保持当前状态（本实验不写入）"})

    def _configuration_revision(self):
        value = instrument_config_snapshot(self.root)
        return (value["device_library_revision"], value["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)
        if preparation.source_revision(self.params, self.root) != self.source_revision:
            raise ValueError("主场标定来源已改变，请结束会话后重新预检")

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
            output_source="最近成功模块的写入结果；SDS 配置另有实际回读，Pump 载波/门控未读取也未修改",
            demod_mode="scope" if self.scope_config is not None else "unknown",
            safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}),
            daq_settings=self.state.get("scope_configuration"))

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
        self.source_revision = preparation.source_revision(params, self.root)
        self.revision += 1
        self.publish("参数已应用；请显式重跑受影响模块，不会自动设置输出")

    def _new_directory(self):
        inherited = self.state
        if self.directory and not self.round_sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        self.directory = create_run_directory(DATA_TYPE, self.params.run_tag, self.params.to_external(),
                                             schema_version=self.params.schema_version, project_root=self.root)
        self.run_parameters = self.params.to_external()
        self.round_sealed = False
        self.records = []
        self.state = preparation.initial_state(self.params, self.root)
        for key in ("device_snapshot", "clock_sources", "temperature_control", "initial_temperature_c",
                    "scope_configuration", "actual_rates"):
            if key in inherited:
                self.state[key] = deepcopy(inherited[key])
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [item for item in selected if item not in ("A01", "A08", FINISH_ID)]
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
                artifacts = [p.relative_to(self.directory.root).as_posix() for pattern in stage.artifacts
                    for p in self.directory.root.glob(pattern) if p.is_file()] if self.directory else []
                self.states[item] = dict(status="completed", error="", artifacts=artifacts)
            except BaseException as exc:
                failure = exc
                status = ("cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else
                          "quality_failed" if isinstance(exc, ValueError) else "failed")
                self.states[item] = dict(status=status, error=str(exc))
                self.state.update(completion_status=status, failure_reason=str(exc),
                    data_files=[record["file"] for record in self.records], completed_frames=len(self.records))
                if notes := getattr(exc, "__notes__", None):
                    self.state["acquisition_cleanup_notes"] = list(notes)
                try:
                    self.save()
                except Exception as error:
                    self.state["state_save_error"] = str(error)
                raise
            finally:
                self.current_step = None
                try:
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def _register_main_field_restore(self):
        self.main_field_guard = StateGuard()
        self.main_field_guard.add("恢复 GS200 运行前状态",
            lambda: restore_main_field_state(self.devices["gs200"], self.main_field_state))

    def _execute_step(self, item):
        p, d, c = self.params, self.devices, self.channels
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not d:
                try:
                    self.devices, self.channels = preparation._connect_devices(self.mapping, self.device_session)
                except BaseException:
                    self.device_session.cleanup_connection_failure()
                    raise
                d, c = self.devices, self.channels
                self.main_field_state = snapshot_main_field_state(d["gs200"])
                self._register_main_field_restore()
            self.state["device_snapshot"] = preparation._device_snapshot(self.mapping, self.main_field_state)
        elif item == "A03":
            try:
                self.state["clock_sources"] = preparation.configure_clocks(d, self.mapping)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self._register_main_field_restore()
            preparation.configure_outputs(p, d, c, self.mapping)
            self.outputs.update(GS200="OFF", X="OFF", Y="OFF", Z="OFF", **{"温控": "ON",
                "Pump 光功率": f"{p.pump_laser_power_v:g} V DC", "Probe 光功率": f"{p.probe_laser_power_v:g} V DC"})
        elif item == "A05":
            status = preparation.configure_temperature_control(d.get("tec"), p.temperature_c,
                                                               channel=int(self.mapping["temperature"].get("channel", 1)))
            self.state.update(temperature_control=status.to_dict(), initial_temperature_c=status.actual_temperature_c)
        elif item == "A06":
            self.scope_config, snapshot = preparation.configure_scope(p, d)
            self.state.update(scope_configuration=snapshot, actual_rates={"scope_sa_s": snapshot["actual_sample_rate_sa_s"]})
            self.auto_range = ScopeAutoRangeState(snapshot["actual_initial_scale_v_div"], snapshot["actual_initial_offset_v"])
            self.outputs["SDS"] = "FSRate / DC / AUTO"
        elif item == "A07":
            self.state["data_files"] = acquisition.acquire_scan(p, self.directory, d, c, self.scope_config,
                self.auto_range, self.records, self.state["main_field_calibration"]["target_current_ma"])
            self.state.update(completed_frames=len(self.records), final_scope_scale_v_div=self.auto_range.scale_v_div,
                              final_scope_offset_v=self.auto_range.offset_v)
            self.outputs.update(GS200="目标主场 ON", **{"温控": "ON", "SDS": "STOP"})
        elif item == "A08":
            if errors := self._shutdown(disconnect_tec=False):
                raise RuntimeError("安全收尾失败：" + "；".join(errors))
            self.state.update(completion_status="completed", failure_reason=None, finished_at=datetime.now().isoformat())
            self.save()
            self.round_sealed = True
            invalidate(self.stages, self.states, "A04")
        self.save()

    def _shutdown(self, *, disconnect_tec=True):
        errors = []
        if self.devices:
            try:
                report = preparation.safe_shutdown(self.devices, self.channels, self.main_field_guard,
                                                   disconnect_tec=disconnect_tec)
                self.state.update(safety_shutdown=report.to_dict(), main_field_restored=not self.main_field_guard.restore_errors)
                errors.extend(report.errors)
            except Exception as exc:
                errors.append(str(exc))
            if self.clock_failed:
                errors.extend(disconnect_device_mapping(self.devices))
            self.outputs.update({key: "未知" if errors else value for key, value in
                {"GS200": "运行前状态", "X": "OFF", "Y": "OFF", "Z": "OFF", "温控": "ON", "SDS": "STOP"}.items()})
        if errors:
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        return errors

    def finish(self, failure=None):
        if self.closed:
            return
        errors = list(disconnect_device_mapping({"tec": self.devices.get("tec")})) if self.round_sealed else self._shutdown()
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        if not self.round_sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                failure_reason=str(failure) if failure else self.state.get("failure_reason") or "本轮采集未完整完成",
                finished_at=datetime.now().isoformat())
        if errors:
            self.state["cleanup_errors"] = errors
            self.state["safety_shutdown"] = dict(self.state.get("safety_shutdown") or {}, completed=False, errors=errors)
        try:
            self.save()
            self.publish("会话已结束")
            report_runtime_progress("finished", "采集会话已结束", 100)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params: ProjectionNoiseParams, *, project_root=None, workflow_type=Workflow) -> Path:
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
    run(load_runtime_params(ProjectionNoiseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
