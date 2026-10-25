"""局部优化完整与分步编排；通过公开 Workflow 接口组合 DC 单点测量。"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...steps import create_run_directory
from ..noise_spectrum_xy.state_io import read_state, write_state
from ..static_sensitivity.workflow import Workflow as PointWorkflow
from . import acquisition, preparation, run_format
from .models import StaticSensitivityOptimizeLocalParams
from .stages import ACQUISITION, FINISH_ID, initial_states, invalidate, validate_selection


class Workflow:
    stages = ACQUISITION

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.session_id, self.revision = session_id or uuid4().hex, 0
        self.config_revision = self._configuration_revision()
        self.states = initial_states(self.stages)
        self.directory = self.run_parameters = self.current_step = self.point = None
        self.closed = self.sealed = False
        self.records = []
        self.state = self._initial_state()

    def _initial_state(self):
        return dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
            raw_format_version=run_format.RAW_FORMAT_VERSION, execution_mode="typed_workflow",
            acquisition_method="dc_step_local", completion_status="running", analysis_status="not_started",
            child_experiment_id="static-sensitivity", started_at=datetime.now().astimezone().isoformat())

    def _configuration_revision(self):
        config = instrument_config_snapshot(self.root)
        return (config["device_library_revision"], config["physical_mapping_revision"],
            (self.root / "params/safety_limits.yaml").read_bytes())

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise ValueError("设备库、映射或安全限值已改变，请安全结束后重新连接")
        self.params, self.mapping = preparation.preflight(self.params, self.root)

    def snapshot(self):
        point = self.point.snapshot() if self.point else {}
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=deepcopy(self.states), current_step=self.current_step,
            run_id=self.directory.root.name if self.directory else None,
            outputs=point.get("outputs", {}), output_source=point.get("output_source", "尚未连接"),
            demod_mode=point.get("demod_mode", "unknown"), safety_shutdown=self.state.get("safety_shutdown"),
            clock_sources=point.get("clock_sources", {}), demod_phase_deg=point.get("demod_phase_deg"),
            daq_settings=dict(child_run_id=point.get("run_id"), child_step=point.get("current_step")))

    def publish(self, message="等待选择下一模块"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.directory and not self.sealed:
            write_state(self.directory.root / "acquisition_state.json", snapshot)
        elif self.directory and self.closed:
            previous = read_state(self.directory.root / "acquisition_state.json") or {}
            previous.update(status="closed", current_step=None, safety_shutdown=self.state.get("safety_shutdown"))
            previous.setdefault("steps", {})[FINISH_ID] = deepcopy(self.states[FINISH_ID])
            write_state(self.directory.root / "acquisition_state.json", previous)
        report_runtime_progress(self.current_step or "waiting", message, data={"workflow_session": snapshot})

    def _point_progress(self, snapshot, message):
        # 子节点不冒充父节点；GUI 与全局会话始终接收父 ID、修订和清单。
        self.publish("静磁场子运行：" + message)

    def save(self):
        if self.directory and not self.sealed:
            self.directory.update_config(**self.state)

    def update_parameters(self, values):
        if self.current_step:
            raise ValueError("模块执行中不能应用参数")
        self.recheck()
        old = self.params.to_external()
        if values.keys() - old.keys():
            raise ValueError("存在未知参数")
        params, mapping = preparation.preflight(StaticSensitivityOptimizeLocalParams.from_external(old | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != old[key]}
        for stage in self.stages:
            if changed.intersection(stage.parameters):
                invalidate(self.stages, self.states, stage.id)
        self.params, self.mapping = params, mapping
        self.revision += 1
        self.publish("参数已应用，请显式重跑受影响模块")

    def _new_directory(self):
        if self.directory and not self.sealed:
            self.state.update(completion_status="failed", failure_reason="本轮被重跑替代，保留已有数据")
            self.save()
        suffix = 0
        while True:
            tag = self.params.run_tag + (f"_{suffix:02d}" if suffix else "")
            try:
                self.directory = create_run_directory(run_format.DATA_TYPE, tag, self.params.to_external(),
                    schema_version=self.params.schema_version, project_root=self.root)
                break
            except FileExistsError:
                suffix += 1
        self.sealed = False
        self.run_parameters = self.params.to_external()
        self.records = preparation.planned_records(self.params)
        self.state = self._initial_state()
        acquisition.save_manifest(self)
        self.save()

    def execute(self, selected):
        if self.current_step:
            raise ValueError("已有模块正在执行")
        if self.closed:
            raise ValueError("会话已经结束")
        selected = validate_selection(self.stages, self.states, selected)
        if selected != [FINISH_ID]:
            self.recheck()
        if any(item in ("A02", "A03", "A04") for item in selected) and (
                self.directory is None or self.sealed or "A02" in selected or
                "A04" in selected and any(row["status"] != "pending" for row in self.records) or
                self.run_parameters != self.params.to_external()):
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
                stage = next(stage for stage in self.stages if stage.id == item)
                self.publish(stage.title)
                self._execute_step(item)
                self.states[item] = dict(status="completed", error="", artifacts=[
                    path.relative_to(self.directory.root).as_posix() for name in stage.artifacts
                    for path in self.directory.root.glob(name)] if self.directory else [])
                self.save()
            except BaseException as exc:
                failure = exc
                self.states[item] = dict(status="failed", error=str(exc))
                self.state["failure_reason"] = str(exc)
                raise
            finally:
                self.current_step = None
                try:
                    self.publish(f"{item} {self.states[item]['status']}")
                    if item == "A05" and failure is None:
                        self.sealed = True
                except Exception:
                    if failure is None:
                        raise
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            acquisition.save_manifest(self)
        elif item == "A03":
            params = preparation.point_parameters(self, self.records[0])
            if self.point is None:
                self.point = PointWorkflow(params, project_root=self.root, progress_callback=self._point_progress)
            else:
                self.point.update_parameters(params.to_external())
            self.point.execute(["A01", "A02", "A03"])
            self.records[0]["run_id"] = self.point.snapshot()["run_id"]
            acquisition.save_manifest(self)
        elif item == "A04":
            acquisition.run_points(self)
            self.state["completion_status"] = "acquired"
        elif item == "A05":
            report = self.point.snapshot().get("safety_shutdown") or {}
            if not report.get("completed"):
                raise RuntimeError("最后子运行未安全恢复，拒绝封存批次")
            self.state.update(safety_shutdown=report, completion_status="completed",
                finished_at=datetime.now().astimezone().isoformat())

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if self.point is not None:
            try:
                self.point.finish(failure)
            except Exception as exc:
                errors.append(str(exc))
            point = self.point.snapshot()
            report = point.get("safety_shutdown")
            if report:
                self.state["safety_shutdown"] = report
                errors.extend(error for error in report.get("errors", []) if error not in errors)
        if not self.sealed:
            self.state.update(completion_status="cancelled" if isinstance(failure, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                failure_reason=str(failure) if failure else "；".join(errors) or "本轮采集未完整完成",
                finished_at=datetime.now().astimezone().isoformat())
            try:
                if self.directory:
                    acquisition.save_manifest(self)
                self.save()
            except Exception as exc:
                errors.append("清单抢救失败：" + str(exc))
        if errors:
            self.state["safety_shutdown"] = dict(completed=False, errors=errors)
        self.closed, self.current_step = True, None
        self.revision += 1
        self.states[FINISH_ID] = dict(status="failed" if errors else "completed", error="；".join(errors))
        try:
            self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
        except Exception:
            if failure is None and not errors:
                raise
        if errors and failure is None:
            raise RuntimeError("安全结束失败：" + "；".join(errors))


def run(params: StaticSensitivityOptimizeLocalParams, *, project_root=None) -> Path:
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != FINISH_ID])
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(StaticSensitivityOptimizeLocalParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
