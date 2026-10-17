# %% [markdown]
# # XY 正弦噪声谱：共享模块编排与分步会话
# 导入只声明接口；硬件连接只发生在执行模块时。

# %% 导入
from pathlib import Path
from uuid import uuid4
import time
import numpy as np
import yaml
from datetime import datetime
from tqdm import tqdm
from lockin_amplifier import demod, daq
from ...common import find_project_root
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...instrument_config import instrument_config_snapshot
from .models import NoiseSpectrumXYParams
from . import preparation, excitation, phase, acquisition
from .preparation import build_target_scan_axes, build_run_config, connect_devices, configure_initial_outputs, EXPERIMENT_TYPE
from .excitation import configure_pump, configure_sequence_2_trigger, validate_pump_levels, validate_trigger_square_levels
from .phase import calibrate_hf2_phase, configure_xy_sine, calibrate_xy_control, validate_sine_peak, set_xy_sine_phase, set_xy_sine_peak, measure_xy_phase
from ._helpers import _RuntimeCancellation, wait, set_temp_switch
from ._shutdown import safe_scan_outputs_off
from .preparation import GS200Instrument, TECInstrument, create_lockin, synchronize_connected_clocks
from .stages import ACQUISITION, initial_states, invalidate, validate_selection
from .state_io import write_state


class Workflow:
    """一个子进程持有一个设备会话；阶段状态不等同于历史数据。"""

    acquisition_stages = ACQUISITION
    finish_id = "A14"
    preflight = staticmethod(preparation.preflight)
    invalidate = staticmethod(invalidate)
    validate_selection = staticmethod(validate_selection)
    always_shutdown = False
    data_type = preparation.EXPERIMENT_TYPE
    experiment_id = "noise-spectrum-xy"

    def shutdown_outputs(self):
        return safe_scan_outputs_off(self.devices)

    def __init__(self, params, *, project_root=None, session_id=None):
        self.root = Path(project_root).resolve() if project_root else find_project_root(Path(__file__).parent)
        self.params, self.mapping, self.limits, self.axes = self.preflight(params, self.root)
        self.config_revision = self._configuration_revision()
        self.session_id = session_id or uuid4().hex
        self.revision = 0
        self.states = initial_states(self.acquisition_stages)
        self.devices = {}
        self.connection = None
        self.clock_sources = {}
        self.calibrated_phase = None
        self.phase_parameters = None
        self.xy_result = None
        self.daq_settings = None
        self.demod_mode = "unknown"
        self.run_dir = None
        self.current_step = None
        self.closed = False
        self.outputs = {key: "未知" for key in ("X", "Y", "Z", "温控", "Pump", "共用触发")}
        self.shutdown = None
        self.clock_failed = False
        self.outputs_started = False

    def _configuration_revision(self):
        snapshot = instrument_config_snapshot(self.root)
        return (snapshot["device_library_revision"], snapshot["physical_mapping_revision"],
                (self.root / "params/safety_limits.yaml").read_bytes())

    def snapshot(self):
        return dict(session_id=self.session_id, revision=self.revision,
            status="closed" if self.closed else "running" if self.current_step else "waiting",
            parameters=self.params.to_external(), steps=self.states, current_step=self.current_step,
            run_id=self.run_dir.name if self.run_dir else None, outputs=self.outputs,
            output_source="最近成功模块的写入结果；未经额外回读", demod_mode=self.demod_mode,
            xy_phase_calibration=(dict(converged=self.xy_result.converged,
                final_phase_deg=self.xy_result.final_phase_deg,
                history=[item.to_dict() for item in self.xy_result.history]) if self.xy_result else None),
            safety_shutdown=self.shutdown, clock_sources=self.clock_sources,
            demod_phase_deg=self.calibrated_phase,
            daq_settings=(dict(actual_rate_sa_s=self.daq_settings[0], grid_cols=self.daq_settings[1],
                **self.daq_settings[2]) if self.daq_settings else None))

    def publish(self, message="等待选择下一模块", level="info"):
        snapshot = self.snapshot()
        write_state(self.root / "data/.gui_jobs" / self.session_id / "session.json", snapshot)
        if self.run_dir:
            write_state(self.run_dir / "acquisition_state.json", snapshot)
        report_runtime_progress(self.current_step or "waiting", message,
            data={"workflow_session": snapshot, "level": level})

    def recheck(self):
        if self.closed:
            raise ValueError("会话已经结束，请新建会话")
        if self._configuration_revision() != self.config_revision:
            raise RuntimeError("设备库、映射或安全限值已改变，请结束会话后重新连接")
        self.params, self.mapping, self.limits, self.axes = self.preflight(self.params, self.root)

    def update_parameters(self, values):
        self.recheck()
        merged = self.params.to_external() | values
        unknown = set(values) - set(self.params.to_external())
        if unknown:
            raise ValueError("未知参数：" + "、".join(sorted(unknown)))
        new, mapping, limits, axes = self.preflight(type(self.params).from_external(merged), self.root)
        changed = {key for key, value in new.to_external().items() if value != self.params.to_external()[key]}
        for stage in self.acquisition_stages:
            if changed.intersection(stage.parameters):
                self.invalidate(self.acquisition_stages, self.states, stage.id)
        self.params, self.mapping, self.limits, self.axes = new, mapping, limits, axes
        self.revision += 1
        self.publish("参数已更新，受影响模块需要重新执行")

    def execute(self, selected):
        selected = self.validate_selection(self.acquisition_stages, self.states, selected)
        if selected != [self.finish_id]:
            self.recheck()
        self.revision += 1
        for item in selected:
            if item == self.finish_id:
                self.finish()
                break
            check_cancelled()
            if item != "A01":
                self.invalidate(self.acquisition_stages, self.states, item)
            self.current_step = item
            self.states[item] = {"status": "running", "error": ""}
            try:
                self.publish(next(stage.title for stage in self.acquisition_stages if stage.id == item))
                self._execute_step(item)
                if self.states[item]["status"] == "running":
                    self.states[item]["status"] = "completed"
            except BaseException as exc:
                self.states[item] = {"status": "failed", "error": str(exc)}
                self.outputs = {key: "未知" for key in self.outputs}
                try:
                    self.publish(str(exc), "error")
                except Exception:
                    pass
                raise
            finally:
                self.current_step = None
            self.publish(f"{item} {self.states[item]['status']}")
            if self.states[item]["status"] != "completed":
                break
        return self.snapshot()

    def _execute_step(self, item):
        if item == "A01":
            self.recheck()
        elif item == "A02":
            if not self.devices:
                self.devices, self.connection = preparation.connect_instruments(self.mapping)
        elif item == "A03":
            self.clock_failed = False
            try:
                self.clock_sources = preparation.synchronize_devices(self.devices, self.mapping)
            except BaseException:
                self.clock_failed = True
                raise
        elif item == "A04":
            acquisition.initialize_run_directory(self, self.data_type)
            self.outputs_started = True
            configure_initial_outputs(self.devices, self.params, self.mapping, self.limits)
            self.outputs.update(Z="OFF", 温控="ON", 共用触发="OFF")
        elif item == "A05":
            preparation.configure_temperature(self.devices, self.params)
        elif item == "A06":
            configure_pump(self.devices["dg_mod"], self.params, self.limits)
            self.outputs["Pump"] = "ON"
        elif item == "A07":
            configure_sequence_2_trigger(self.devices["dg_sweep"], self.params, self.limits)
            self.outputs["共用触发"] = "ON"
        elif item == "A08":
            self.calibrated_phase = calibrate_hf2_phase(self.devices["hf2"], self.devices["dg_comp"],
                self.devices["dg_temp"], self.params, self.limits)
            self.phase_parameters = self.params
            self.demod_mode = "phase"
            self.outputs.update(X="OFF", Y="OFF", 温控="ON")
        elif item == "A09":
            configure_xy_sine(self.devices["dg_comp"], self.params, self.limits)
            self.outputs.update(X="OFF", Y="OFF")
        elif item == "A10":
            if self.demod_mode != "phase":
                acquisition.restore_demod(self.devices, self.params, self.calibrated_phase)
                self.demod_mode = "phase"
            # 重试时先恢复校相峰值；扫描可能已经改变了仪器幅度。
            phase.verify_xy_mode(self.devices["dg_comp"], self.params)
            for channel in (1, 2):
                self.devices["dg_comp"].set_output(False, channel=channel)
                self.devices["dg_comp"].set_amplitude(2 * validate_sine_peak(
                    self.params.xy_calib_envelope_v, self.limits), channel=channel)
            self.xy_result = calibrate_xy_control(self.devices["hf2"], self.devices["dg_comp"],
                self.devices["dg_temp"], self.params, self.limits)
            self.outputs.update(X="ON", Y="ON", 温控="ON")
            if not self.xy_result.converged:
                self.states[item] = {"status": "quality_failed", "error": "XY 校相未收敛，禁止扫描；可以调整参数后重试"}
        elif item == "A11":
            if not self.xy_result or not self.xy_result.converged:
                raise ValueError("XY 校相未通过，禁止配置采集")
            self.daq_settings = acquisition.prepare_daq(self.devices, self.params, self.calibrated_phase)
            self.demod_mode = "daq"
        elif item == "A12":
            if self.demod_mode != "daq":
                raise ValueError("解调器已恢复，请重新执行 A11 DAQ 准备")
            phase.verify_xy_mode(self.devices["dg_comp"], self.params)
            acquisition.verify_daq_mode(self)
            acquisition.acquire_scan(self)
            self.states[item]["artifacts"] = ["experiment_config.yaml", "raw/control_scan_axes.npz",
                *[str(path.relative_to(self.run_dir)).replace("\\", "/")
                    for path in sorted((self.run_dir / "raw").glob("waveform_C*.npy"))]]
            last_on = bool(self.axes[1][-1])
            self.outputs.update(X="ON" if last_on else "OFF", Y="ON" if last_on else "OFF", 温控="ON")
        elif item == "A13":
            acquisition.restore_demod(self.devices, self.params, self.calibrated_phase)
            self.demod_mode = "phase"
            self.states["A11"] = {"status": "stale", "error": "解调器已恢复；再次扫描需要 DAQ 准备"}

    def finish(self, failure=None):
        if self.closed:
            return
        errors = []
        if failure is None and self.demod_mode == "daq" and self.calibrated_phase is not None:
            try:
                acquisition.restore_demod(self.devices, self.phase_parameters or self.params, self.calibrated_phase)
                self.demod_mode = "phase"
            except Exception as exc:
                errors.append(str(exc))
        try:
            if self.devices and (not self.clock_failed or self.outputs_started or self.always_shutdown):
                report = self.shutdown_outputs()
                self.shutdown = report.to_dict()
                errors.extend(report.errors)
                if report.completed:
                    self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON")
                else:
                    self.outputs.update(X="未知", Y="未知", Z="未知", 温控="未知")
            if self.devices and self.clock_failed:
                preparation.disconnect_device_mapping(self.devices)
        except Exception as exc:
            errors.append(str(exc))
        finally:
            self.closed = True
            self.current_step = None
            self.revision += 1
            if self.run_dir and not getattr(self, "scan_started", False):
                try:
                    path = self.run_dir / "experiment_config.yaml"
                    config = yaml.safe_load(path.read_text(encoding="utf-8"))
                    config.update(completion_status="failed", failure_reason=str(failure or "本轮未开始扫描"))
                    acquisition.save_config(path, config)
                except Exception as exc:
                    errors.append(f"初始配置收尾保存失败：{exc}")
            self.states[self.finish_id] = {"status": "failed" if errors else "completed", "error": "；".join(errors)}
            try:
                self.publish("会话已结束" + ("；" + "；".join(errors) if errors else ""))
            except Exception:
                if failure is None:
                    raise
        if errors and failure is None:
            raise RuntimeError("安全收尾未全部完成：" + "；".join(errors))


# %% 运行入口
def run(params: NoiseSpectrumXYParams, *, project_root: Path | None = None) -> Path:
    """完整采集和分步会话使用相同模块；直接调用会自动结束。"""
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in ACQUISITION if stage.id != "A14"])
        if ctx.states["A10"]["status"] != "completed":
            raise ValueError(ctx.states["A10"]["error"])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(NoiseSpectrumXYParams))


if __name__ == "__main__":
    main()
