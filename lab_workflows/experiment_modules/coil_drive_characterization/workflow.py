"""完整运行与分步会话共用编排；设备、测量与算法各归职责模块。"""
from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from lab_workflows.common import data_root
from lab_workflows.workflow_stages import initial_states
from lab_workflows.workflow_state import write_state

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import DeviceSession, create_run_directory, disconnect_device_mapping
from . import acquisition, preparation, run_format
from .models import CoilDriveCharacterizationParams
from .stages import ACQUISITION, FINISH_ID, invalidate, validate_selection

MEASUREMENTS = {"A03": acquisition.measure_zero_and_gain, "A04": acquisition.measure_transfer,
                "A05": acquisition.measure_steps}


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.states = initial_states(self.stages)
        self.devices = {}
        self.device_session = DeviceSession()
        self.directory = self.run_parameters = self.current_step = None
        self.current_scale = None
        self.closed = self.clock_failed = False
        self.state = run_format.initial_metadata(self.params)
        self.outputs = {key: "未知" for key in ("功放", "Z 通道", "DHO900")}

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise ValueError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
                    status="closed" if self.closed else "running" if self.current_step else "waiting",
                    parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
                    run_id=self.directory.root.name if self.directory else None, outputs=dict(self.outputs),
                    output_source="最近成功写入的命令；1200B 无状态查询，信号源与示波器未额外回读",
                    daq_settings={key: self.state.get(key) for key in ("zero_offset", "gain_check", "transfer_preview")},
                    safety_shutdown=self.state.get("safety_shutdown"), clock_sources=self.state.get("clock_sources", {}))

    def publish(self, message="等待选择下一模块"):
        value = self.snapshot()
        write_state(data_root(self.root) / ".gui_jobs" / self.session_id / "session.json", value)
        if self.directory:
            write_state(self.directory.root / "acquisition_state.json", value)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": value})

    def save(self):
        if self.directory:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("执行中不能应用参数")
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
        self.publish("参数已应用；请显式重跑失效模块，不会自动设置输出")

    def _new_directory(self):
        previous, old_state = self.directory, self.state
        if previous and old_state.get("completion_status") == "running":
            old_state.update(completion_status="failed", failure_reason="本轮被新参数替代，已有数据保留")
            self.save()
        self.directory = create_run_directory(run_format.DATA_TYPE, self.params.run_tag, self.params.to_external(),
                                              schema_version=self.params.schema_version, project_root=self.root)
        self.run_parameters = self.params.to_external()
        self.state = run_format.initial_metadata(self.params)
        if "clock_sources" in old_state:
            self.state["clock_sources"] = old_state["clock_sources"]
        if previous and self.states["A03"]["status"] == "completed":
            # A03 参数未变仍有效：复制其原始帧，使新目录可独立分析；原目录不改动。
            raw = self.directory.root / "raw"
            raw.mkdir(exist_ok=True)
            for name in self.states["A03"].get("artifacts", []):
                shutil.copy2(previous.root / name, raw / Path(name).name)
            self.state.update(zero_offset=old_state.get("zero_offset"), gain_check=old_state.get("gain_check"),
                              data_files=list(self.states["A03"].get("artifacts", [])),
                              zero_inherited_from=previous.root.name)
        self.save()

    def execute(self, selected):
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        hardware = [s for s in selected if s in MEASUREMENTS]
        if hardware and (self.directory is None or self.run_parameters != self.params.to_external()):
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
                if item in MEASUREMENTS:
                    self.state.update(completion_status="running", failure_reason=None)
                    self.save()
                self.publish(next(s.title for s in self.stages if s.id == item))
                self._execute_step(item)
                self.states[item] = dict(status="completed", error="")
                if item in MEASUREMENTS:
                    prefix = {"A03": ("raw/zero_", "raw/gain_"), "A04": ("raw/sine_",), "A05": ("raw/pulse_",)}[item]
                    self.states[item]["artifacts"] = [f for f in self.state["data_files"] if f.startswith(prefix)]
                    # 模块完成即停止写入：可在会话等待时离线分析。
                    self.state.update(completion_status="completed")
            except BaseException as exc:
                failure = exc
                status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else (
                    "quality_failed" if isinstance(exc, preparation.SafetyAbort) else "failed")
                self.states[item] = dict(status=status, error=str(exc))
                self.state.update(completion_status="cancelled" if status == "cancelled" else "failed",
                                  failure_reason=str(exc))
                raise
            finally:
                self.current_step = None
                try:
                    self.save()
                    self.publish(f"{item} {self.states[item]['status']}")
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            preparation.connect(self)
            try:
                self.state["clock_sources"] = preparation.clocks(self)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
            self.outputs["DHO900"] = "已连接"
        else:
            MEASUREMENTS[item](self)

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.devices:
            action_errors, disconnect_errors = preparation.shutdown(self)
            errors = [*action_errors, *disconnect_errors]
            self.state["safety_shutdown"] = dict(completed=not errors, errors=errors)
            self.outputs.update({"功放": "未知" if action_errors else "OFF（已发送 AOF）",
                                 "Z 通道": "未知" if action_errors else "DC 0 V + OFF，同步 OFF",
                                 "DHO900": "未知" if action_errors else "STOP，已断开"})
        else:
            errors = list(disconnect_device_mapping({str(i): d for i, d in enumerate(self.device_session.unique_devices())}))
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        if failure is not None and self.state.get("completion_status") not in ("failed", "cancelled"):
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt))
                              else "failed", failure_reason=str(failure))
        elif errors:
            self.state.update(completion_status="failed", failure_reason="安全收尾失败：" + "；".join(errors))
        elif self.state.get("completion_status") == "running":
            self.state.update(completion_status="failed", failure_reason="会话结束时仍有模块未完成")
        try:
            self.save()
            self.publish("会话已结束；功放 OFF、Z 通道 0 V 并关闭输出")
            report_runtime_progress("finished", "采集会话结束", 100, estimated_remaining_seconds=None)
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全收尾失败：" + "；".join(errors))


def run(params: CoilDriveCharacterizationParams, *, project_root=None) -> Path:
    ctx, failure = Workflow(params, project_root=project_root), None
    try:
        ctx.execute([s.id for s in ACQUISITION if s.id != FINISH_ID])
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(CoilDriveCharacterizationParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
