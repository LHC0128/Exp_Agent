"""示波器采集的完整与分步共用编排；等待期间保持 SDS 连接与温控开关状态。"""

# %% 导入
from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import uuid4

import numpy as np
import yaml
from sds_acquisition import SDSAcquisition, SDSInstrument

from ...common import WorkflowCancelled, find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from ...devices import create_signal_generator
from ...steps import DeviceSession
from ..noise_spectrum_xy.state_io import write_state
from . import acquisition, preparation
from .models import ScopeCaptureParams
from .run_format import DATA_TYPE, EXPERIMENT_ID
from .stages import ACQUISITION, initial_states, invalidate, validate_selection

EXECUTION_MODE = "typed_workflow"
# 兼容旧导入边界：历史脚本从 workflow 模块使用这些名称。
MAXIMUM_POINTS = acquisition.MAXIMUM_POINTS
sleep_cancellable = acquisition.sleep_cancellable
configure = acquisition.configure
capture = acquisition.capture


preflight = preparation.preflight

class Workflow:
    """保持同一硬件会话；采集重跑生成新目录，空闲等待仍占用硬件锁。"""

    acquisition_stages = ACQUISITION
    finish_id = "A05"
    preflight = staticmethod(preflight)

    def __init__(self, params: ScopeCaptureParams, *, project_root: Path | None = None,
                 session_id: str | None = None, experiment_id: str = EXPERIMENT_ID,
                 data_type: str = DATA_TYPE, instrument_factory: Callable | None = None,
                 acquirer_factory: Callable | None = None):
        self.root = project_root or find_project_root()
        self.experiment_id, self.data_type = experiment_id, data_type
        # 运行时解析默认工厂，测试可以整体替换模块级驱动构造。
        self.instrument_factory = instrument_factory or SDSInstrument
        self.acquirer_factory = acquirer_factory or SDSAcquisition
        self.params, self.mapping, self.temp_mapping = self.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.acquisition_stages)
        self.connection = DeviceSession()
        self.scope = None
        self.acquirer = None
        self.temp_switch = None
        self.temp_channel = None
        self.scope_config = None
        self.scope_snapshot = None
        self.run_dir = self.run_directory = self.current_step = None
        self.closed = False
        self.outputs = {"SDS": "未知", "温控开关": "未知"}
        self.shutdown = None
        self.clock_sources = {}

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source="最近成功模块的写入结果；SDS 配置另有实际回读",
            demod_mode="scope" if self.scope_config is not None else "unknown",
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources,
            daq_settings=self.scope_snapshot)

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.run_dir:
            write_state(self.run_dir / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def _check_configuration(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")

    def recheck(self):
        self._check_configuration()
        self.params, self.mapping, self.temp_mapping = self.preflight(self.params, self.root)

    def update_parameters(self, values):
        self._check_configuration()
        previous = self.params.to_external()
        unknown = set(values) - set(previous)
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        params, mapping, temp_mapping = self.preflight(
            type(self.params).from_external(previous | values), self.root)
        changed = {key for key, value in params.to_external().items() if value != previous[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.temp_mapping = params, mapping, temp_mapping
        self.revision += 1
        self.publish("参数已应用；请显式重跑已失效的步骤")

    def execute(self, selected):
        selected = validate_selection(self.acquisition_stages, self.states, selected)
        if selected != [self.finish_id]:
            self.recheck()
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                invalidate(self.acquisition_stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            try:
                self.publish(next(stage.title for stage in self.acquisition_stages if stage.id == item))
                self._execute_step(item)
                self.states[item]["status"] = "completed"
            except BaseException as exc:
                self.states[item] = {"status": "quality_failed" if isinstance(exc, ValueError) else
                    "cancelled" if isinstance(exc, WorkflowCancelled) else "failed", "error": str(exc)}
                if item == "A04" and self.run_directory is not None:
                    # 分步会话可能不立即结束；失败目录不能停留在 running。
                    try:
                        self.run_directory.update_config(
                            completion_status="cancelled" if isinstance(exc, WorkflowCancelled) else "failed",
                            failure_reason=str(exc))
                    except Exception:
                        pass
                try:
                    self.publish(str(exc), "error")
                except Exception:
                    pass
                raise
            finally:
                self.current_step = None
            self.publish(f"{item} 已完成")
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            preparation.connect(self, create_signal_generator)
        elif item == "A03":
            if self.scope is None or self.acquirer is None:
                raise ValueError("请先连接 SDS")
            check_cancelled()
            self.scope_config, self.scope_snapshot = acquisition.configure(
                self.scope, self.params, self.acquirer)
            self.outputs["SDS"] = f"已配置：C{self.params.channel}，等待完整帧"
        elif item == "A04":
            if self.scope_config is None or self.scope_snapshot is None:
                raise ValueError("请先完成 A03 示波器采集配置")
            self._acquire_run()

    def _acquire_run(self):
        return acquisition.acquire_run(self)

    def finish(self, failure=None):
        if self.closed:
            return
        errors: list[str] = []
        try:
            if self.scope is not None or self.temp_switch is not None:
                report = preparation.shutdown(self)
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                if report.completed:
                    self.outputs.update({"SDS": "STOP；已断开", "温控开关":
                        "5 V ON；已断开" if self.temp_switch is not None else "未连接；未修改"})
                else:
                    self.outputs.update({key: "未知" for key in self.outputs})
            elif self.connection is not None:
                self.connection.cleanup_connection_failure()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.closed, self.current_step = True, None
            self.revision += 1
            if self.run_directory is not None:
                try:
                    updates = dict(safety_shutdown=self.shutdown)
                    config = yaml.safe_load(self.run_directory.config_path.read_text(encoding="utf-8")) or {}
                    if errors or (failure is not None and config.get("completion_status") == "running"):
                        status = "cancelled" if isinstance(failure, WorkflowCancelled) else "failed"
                        updates.update(completion_status=status, failure_reason="；".join(
                            ([str(failure)] if failure is not None else
                             [str(config["failure_reason"])] if config.get("failure_reason") else []) + errors))
                    self.run_directory.update_config(**updates)
                except Exception as exc:
                    errors.append(f"保存安全收尾记录失败：{exc}")
            self.states[self.finish_id] = {"status": "failed" if errors else "completed",
                                           "error": "；".join(errors)}
            try:
                self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            except Exception:
                if failure is None:
                    raise
        if errors and failure is None:
            raise RuntimeError("示波器结束清理失败：" + "；".join(errors))


# %% 完整运行与历史兼容入口
def run(params: ScopeCaptureParams, *, project_root: Path | None = None,
        instrument_factory: Callable | None = None, acquirer_factory: Callable | None = None) -> Path:
    """完整运行复用同一组分步业务步骤，结束后总是安全收尾。"""
    ctx = Workflow(params, project_root=project_root,
                   instrument_factory=instrument_factory, acquirer_factory=acquirer_factory)
    failure = None
    try:
        ctx.execute([stage.id for stage in ctx.acquisition_stages if stage.id != ctx.finish_id])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main() -> int:
    run(load_runtime_params(ScopeCaptureParams))
    return 0


if __name__ == "__main__":
    try:
        main()
    except WorkflowCancelled as exc:
        print(str(exc))
        raise SystemExit(130)
