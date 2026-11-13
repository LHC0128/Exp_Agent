# %% [markdown]
# # XY 正弦噪声谱：本实验壳类与硬件节点
# 会话生命周期（快照、发布、执行循环、安全结束）在 `lab_workflows.steps.noise_xy_scan`；
# 本模块保留同名 `Workflow`、本实验的节点动作与模块入口，公开导入不变。
# 模块级导入保持拆分前一致：工具与测试按模块名打桩（yaml/np/tqdm/demod/daq 等）。

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
from ...steps.lockin import verify_lockin_acquisition_mode
from ...steps.noise_xy_scan import NoiseXYScan
from .models import NoiseSpectrumXYParams
from . import preparation, excitation, phase, acquisition
from .preparation import build_target_scan_axes, build_run_config, connect_devices, configure_initial_outputs, EXPERIMENT_TYPE
from .excitation import configure_pump, configure_sequence_2_trigger, validate_pump_levels, validate_trigger_square_levels
from .phase import calibrate_hf2_phase, configure_xy_sine, calibrate_xy_control, validate_sine_peak, set_xy_sine_phase, set_xy_sine_peak, measure_xy_phase
from ._helpers import _RuntimeCancellation, wait, set_temp_switch
from ._shutdown import safe_scan_outputs_off
from .preparation import GS200Instrument, TECInstrument, create_lockin, synchronize_connected_clocks
from .stages import ACQUISITION, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states
from lab_workflows.workflow_state import write_state
from lab_workflows.common import data_root


class Workflow(NoiseXYScan):
    """一个子进程持有一个设备会话；阶段状态不等同于历史数据。"""

    acquisition_stages = ACQUISITION
    finish_id = "A14"
    preflight = staticmethod(preparation.preflight)
    invalidate = staticmethod(invalidate)
    validate_selection = staticmethod(validate_selection)
    always_shutdown = False
    data_type = preparation.EXPERIMENT_TYPE
    experiment_id = "noise-spectrum-xy"
    output_keys = ("X", "Y", "Z", "温控", "Pump", "共用触发")

    def __init__(self, params, *, project_root=None, session_id=None):
        # 输出键由本实验声明；共享引擎只更新取值。
        self.outputs = {key: "未知" for key in self.output_keys}
        super().__init__(params, project_root=project_root, session_id=session_id)

    def shutdown_outputs(self):
        return safe_scan_outputs_off(self.devices)

    def restore_demod(self, params):
        acquisition.restore_demod(self.devices, params, self.calibrated_phase)

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
            verify_lockin_acquisition_mode(self.devices["hf2"],
                demod_index=self.params.hf2_demod_idx, requested_rate_hz=self.params.hf2_daq_rate,
                acquisition_rate_hz=self.daq_settings[0], time_constant_s=self.params.hf2_daq_tc,
                phase_deg=self.calibrated_phase)
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
