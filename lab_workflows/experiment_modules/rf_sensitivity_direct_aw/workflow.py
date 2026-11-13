# %% [markdown]
# # RF 灵敏度（DirectAW）：完整运行与分步会话共用编排
#
# 执行引擎（会话状态、快照、发布、保存、目录轮换、执行循环、结束）在共享层
# `lab_workflows.steps.direct_aw_control`；本模块只声明流程清单与运行格式，
# 实现节点动作、安全收尾和多控制门面。

# %% 导入
from copy import deepcopy
from dataclasses import replace
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import TypeVar
import time
from ...common import WorkflowCancelled
from ...experiment_runtime import load_runtime_params, report_runtime_progress
from ...steps import synchronize_connected_clocks
from ...steps import direct_aw_state
from ...steps.direct_aw_control import DirectAWControlRun
from ...steps.direct_aw_control_runs import create_control_run as _create_control_run
from ...steps.safety_shutdown import disconnect_device_mapping
from . import preparation, excitation, phase, acquisition, run_format
from ._helpers import _RuntimeCancellation, hf2_snapshot, wait
from .models import RFSensitivityDirectAWParams
from .control_runs import execute_controls, seal_controls
from .stages import SINGLE_ACQUISITION, MULTI_ACQUISITION, FINISH_ID, invalidate, invalidated_stages


_ControlWorkflow = TypeVar("_ControlWorkflow", bound="SingleControlWorkflow")
# 采集与结束节点：A15 完成本轮采集，A16 安全收尾并关闭会话。
RUN_ID = "A15"


class SingleControlWorkflow(DirectAWControlRun):
    """一个设备会话可重复采集；每轮参数和原始数据独立保存。"""
    stages = SINGLE_ACQUISITION
    params_type = RFSensitivityDirectAWParams
    identity = run_format
    finish_id, run_id = FINISH_ID, RUN_ID
    output_keys = ("X", "Y", "Z", "温控", "Pump", "共用触发")

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        # 运行目录初始元数据由本实验的运行格式（identity）决定。
        self.state = direct_aw_state.initial_state(self.params, identity=self.identity)
        self.cancellation = _RuntimeCancellation()
        self.started = time.monotonic()

    @classmethod
    def create_control_run(cls: type[_ControlWorkflow], params, **kwargs) -> _ControlWorkflow:
        """创建已选定一个控制的子运行；共享工厂返回 (ctx, run_dir)，这里保持只返回 ctx。"""
        ctx, _ = _create_control_run(cls, params, **kwargs)
        return ctx

    def _control_run_metadata(self):
        relative = self.params.control_waveform_files[0]
        return dict(raw_format_version=1, control_layout="single", control_metrics={
            "envelope_rms_V": float(np.sqrt(np.mean(self.envelope["envelope_V"] ** 2))),
            "envelope_peak_V": float(np.max(np.abs(self.envelope["envelope_V"]))),
            "repeat_frequency_Hz": self.envelope["source_repeat_freq_Hz"],
            "control_scale_factor": self.params.control_scale_factor(relative),
        })

    def _is_quality_failure(self, item, exc):
        """校相门槛失败与 A11 扫描拟合失败都在原会话内停止后继，可重试。"""
        return super()._is_quality_failure(item, exc) or (item == "A11" and isinstance(exc, ValueError))

    def on_quality_failure(self):
        preparation.restore_temperature(self)

    def preflight(self, params):
        return preparation.preflight(params, self.root, experiment_id=self.identity.EXPERIMENT_ID)

    def progress(self, stage, completed):
        total = self.params.rf_amp_points + self.params.noise_n_avg
        report_runtime_progress(stage, f"已完成 {completed}/{total} 个采集单元", 25 + 65 * completed / total,
            estimated_remaining_seconds=(time.monotonic() - self.started) / completed * (total - completed))

    def after_step(self, item):
        super().after_step(item)
        if item == self.run_id:
            # 本轮完成后校相链失效以便下次显式重跑；本轮结束节点本身保持已完成。
            record = dict(self.states[item])
            invalidate(self.stages, self.states, "A09")
            self.states[item] = record

    def _execute_step(self, item):
        if item == "A02":
            if not self.devices:
                self.devices = preparation.connect_devices(self.mapping, params=self.params, experiment_id=self.identity.EXPERIMENT_ID)
        elif item == "A03":
            try:
                self.state["clock_sync"] = synchronize_connected_clocks(self.devices, self.mapping,
                    {"signals": "X_magnetic_field", "hf2": "lockin_r"}, settle_wait=wait)
                self.clock_failed = False
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            self.outputs_safe = False
            preparation.configure_initial_outputs(self)
            self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON", 共用触发="OFF")
        elif item == "A05":
            preparation.configure_temperature(self)
        elif item == "A06":
            excitation.configure_pump(self)
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            excitation.configure_trigger(self)
            self.outputs["共用触发"] = "ON"
        elif item == "A08":
            self.state["phase_calibration_status"] = "running"
            phase.calibrate_demod0(self)
            self.demod_mode = "phase"
        elif item == "A09":
            self.outputs_safe = False
            phase.calibrate_xy(self)
            self.outputs.update(X="ON", Y="ON", 温控="ON")
        elif item == "A10":
            phase.configure_loopback(self)
            self.demod_mode = "response"
            self.outputs["Z"] = "ON"
            acquisition.record_response_settings(self)
        elif item == "A11":
            acquisition.record_response_settings(self)
            phase.calibrate_rf(self)
        elif item == "A12":
            phase.calibrate_demod3(self)
        elif item == "A13":
            acquisition.record_response_settings(self)
            self.started = time.monotonic()
            acquisition.acquire_response(self)
        elif item == "A13B":
            if self.params.linewidth_mode != "frequency_sweep":
                raise ValueError("线宽模式为色散等效，未启用带宽扫频；请先在 A13B 节点把 LINEWIDTH_MODE 切换为扫频测线宽")
            if self.demod_mode != "response":
                acquisition.restore_response(self)
            self.state["hf2_bandwidth_snapshot"] = hf2_snapshot(self.devices["hf2"], self.params)
            acquisition.record_stage_settings(self, "bandwidth")
            acquisition.acquire_bandwidth(self)
        elif item == "A14":
            if self.identity.RESPONSE_COMPONENT == "X":
                acquisition.record_response_settings(self)
            acquisition.acquire_noise(self)
            self.outputs.update(Z="OFF", 温控="ON")
        elif item == "A15":
            acquisition.restore_response(self)
            report_runtime_progress("shutdown", "结束本轮采集，关闭 XYZ 并保持设备会话", 95)
            report = preparation.shutdown(self.devices, disconnect_tec=False)
            self.state["safety_shutdown"] = report.to_dict()
            if not report.completed:
                raise RuntimeError("安全收尾失败：" + "；".join(report.errors))
            self.outputs_safe = True
            self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
            self.state["hf2_final_snapshot"] = hf2_snapshot(self.devices["hf2"], self.params)
            self.state.update(completion_status="completed", finished_at=datetime.now().isoformat())
        self.save()

    def shutdown(self, failure):
        """恢复响应配置并执行安全收尾；返回全部错误，不抛出。"""
        errors = []
        sealed = self.sealed
        try:
            report_runtime_progress("shutdown", "恢复响应配置并执行安全收尾", 95)
        except Exception as exc:
            self.state["progress_reporting_error"] = str(exc)
        if self.demod_mode == "noise":
            try:
                acquisition.restore_response(self)
            except Exception as exc:
                self.state["hf2_restore_error"] = str(exc)
                errors.append(str(exc))
        if self.devices:
            try:
                if self.outputs_safe:
                    errors.extend(disconnect_device_mapping({"tec": self.devices.get("tec")}))
                    if errors:
                        self.state["safety_shutdown"].update(completed=False, errors=list(errors))
                else:
                    report = preparation.shutdown(self.devices)
                    self.state["safety_shutdown"] = report.to_dict()
                    errors.extend(report.errors)
                    if report.completed:
                        self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
                if self.clock_failed:
                    errors.extend(disconnect_device_mapping(self.devices))
            except Exception as exc:
                errors.append(str(exc))
                self.state["safety_shutdown"] = dict(completed=False, errors=list(errors))
            if not sealed:
                try:
                    self.state["hf2_final_snapshot"] = hf2_snapshot(self.devices["hf2"], self.params)
                except Exception as exc:
                    self.state["hf2_snapshot_error"] = str(exc)
        return errors


# %% 多控制门面保留公开 Workflow 导入；共享步骤不反向导入编排模块。
class Workflow(SingleControlWorkflow):
    """正式多控制编排；旧单文件调用继续使用同一单控制内核。"""
    stages = MULTI_ACQUISITION

    def __init__(self, *args, **kwargs):
        self.controls = {}
        super().__init__(*args, **kwargs)

    @property
    def multi_control(self):
        return self.params.control_waveform_files is not None

    def _new_directory(self, selected):
        if not self.multi_control:
            super()._new_directory(selected)
            self.state.update(raw_format_version=1, control_layout="single")
            self.save()
            return
        previous, inherited, old_controls = self.directory, deepcopy(self.state), self.controls
        if previous and self.state["completion_status"] in ("running", "acquired"):
            previous.update_config(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据")
        # 多控制的数据只经各自的有效前置复制，禁止从父目录混入旧响应和噪声。
        self.directory = None
        super()._new_directory(selected)
        self.state.update(raw_format_version=2, control_layout="controls", controls_sealed=False)
        for key in ("calibration", "clock_sync", "temperature_control", "optional_connection_errors", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        affected = {stage.id for item in selected if item != "A01"
                    for stage in invalidated_stages(self.stages, item)}
        self.controls = {}
        for relative in self.params.control_waveform_files:
            params = replace(self.params, control_waveform_files=[relative])
            ctx = SingleControlWorkflow.create_control_run(params, parent_directory=self.directory,
                project_root=self.root, session_id=self.session_id, devices=self.devices,
                shared_calibration=self.state["calibration"], stage_states=self.states,
                previous=old_controls.get(Path(relative).parent.parent.name), affected_stages=affected)
            self.controls[ctx.directory.root.name] = ctx
        self.save()

    def _execute_step(self, item):
        if self.multi_control and item in ("A09", "A10", "A11", "A12", "A13", "A14", "A13B"):
            execute_controls(self, item)
            return
        super()._execute_step(item)
        if item == "A02":
            for ctx in self.controls.values():
                ctx.devices = self.devices
        if item == "A15" and self.multi_control:
            seal_controls(self)

    def _save_failed_controls(self, failure):
        for version, ctx in self.controls.items():
            if ctx.state["completion_status"] == "completed":
                continue
            ctx.state.update(completion_status=self.state["completion_status"],
                finished_at=self.state.get("finished_at"),
                failure_reason=ctx.state.get("failure_reason") or self.state.get("failure_reason"),
                safety_shutdown=self.state.get("safety_shutdown"))
            try:
                ctx.save()
            except Exception as error:
                self.state.setdefault("control_state_save_errors", {})[version] = str(error)
                if failure is None:
                    raise

    def finish(self, failure=None):
        try:
            super().finish(failure)
        except BaseException as error:
            self._save_failed_controls(error)
            raise
        else:
            self._save_failed_controls(failure)


# %% 完整采集与子进程入口
def run(params: RFSensitivityDirectAWParams, *, project_root=None, workflow_type=Workflow) -> Path:
    ctx = workflow_type(params, project_root=project_root)
    failure = None
    try:
        # 完整运行按线宽模式决定是否包含可选带宽扫频节点。
        skip = () if ctx.params.linewidth_mode == "frequency_sweep" else ("A13B",)
        ctx.execute([stage.id for stage in ctx.stages if stage.id != FINISH_ID and stage.id not in skip])
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(RFSensitivityDirectAWParams))


if __name__ == "__main__":
    main()
