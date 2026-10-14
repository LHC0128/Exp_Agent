# %% [markdown]
# # XY Probe AM 噪声谱：采集与会话编排
# 导入不连接仪器；完整与分步模式调用相同业务步骤。

# %% 导入
from ..noise_spectrum_xy import acquisition as xy_acquisition
from ..noise_spectrum_xy.phase import (calibrate_hf2_phase, calibrate_xy_control,
                                       configure_xy_sine, validate_sine_peak, verify_xy_mode)
from ..noise_spectrum_xy.workflow import Workflow as SineWorkflow
from ...experiment_runtime import load_runtime_params
from . import acquisition, excitation, preparation, stages
from .models import NoiseSpectrumXYProbeAMParams
from .preparation import preflight
from .safety import safe_scan_outputs_off


class Workflow(SineWorkflow):
    acquisition_stages = stages.ACQUISITION
    finish_id = stages.FINISH_ID
    preflight = staticmethod(preflight)
    invalidate = staticmethod(stages.invalidate)
    validate_selection = staticmethod(stages.validate_selection)

    def __init__(self, params, *, project_root=None, session_id=None):
        super().__init__(params, project_root=project_root, session_id=session_id)
        self.noise_waveform = None
        self.z_waveform = None
        self.noise_readback = None
        self.z_readback = None
        self.outputs["Probe AM"] = "未知"

    def shutdown_outputs(self):
        report = safe_scan_outputs_off(self.devices, self.mapping)
        if report.completed:
            self.outputs.update(X="OFF", Y="OFF", Z="OFF", 温控="ON", 共用触发="OFF", **{"Probe AM": "OFF"})
        else:
            self.outputs.update(X="未知", Y="未知", Z="未知", 温控="未知", 共用触发="未知", **{"Probe AM": "未知"})
        return report

    def _execute_step(self, item):
        if item == "A02":
            # Probe AM 使用独立 DG4000；连接含 dg_probe_am 并在失败时收尾已连输出。
            if not self.devices:
                self.devices, self.connection = preparation.connect_instruments(self)
            return
        if item == "A03":
            self.clock_failed = False
            try:
                self.clock_sources = preparation.synchronize_devices(self.devices, self.mapping)
            except BaseException:
                self.clock_failed = True
                raise
            return
        if item <= "A07":
            return super()._execute_step(item)
        if item == "A08":
            excitation.configure_probe_carrier(self)
        elif item == "A09":
            self.calibrated_phase = calibrate_hf2_phase(self.devices["hf2"], self.devices["dg_comp"],
                self.devices["dg_temp"], self.params, self.limits)
            self.demod_mode = "phase"
            self.outputs.update(X="OFF", Y="OFF", 温控="ON")
        elif item == "A10":
            configure_xy_sine(self.devices["dg_comp"], self.params, self.limits)
            self.outputs.update(X="OFF", Y="OFF")
        elif item == "A11":
            if self.demod_mode != "phase":
                xy_acquisition.restore_demod(self.devices, self.params, self.calibrated_phase)
                self.demod_mode = "phase"
            # 重试时先恢复校相峰值；扫描可能已经改变了仪器幅度。
            verify_xy_mode(self.devices["dg_comp"], self.params)
            for channel in (1, 2):
                self.devices["dg_comp"].set_output(False, channel=channel)
                self.devices["dg_comp"].set_amplitude(2 * validate_sine_peak(
                    self.params.xy_calib_envelope_v, self.limits), channel=channel)
            self.xy_result = calibrate_xy_control(self.devices["hf2"], self.devices["dg_comp"],
                self.devices["dg_temp"], self.params, self.limits)
            self.outputs.update(X="ON", Y="ON", 温控="ON")
            if not self.xy_result.converged:
                self.states[item] = {"status": "quality_failed", "error": "XY 校相未收敛，禁止扫描；可以调整参数后重试"}
        elif item == "A12":
            excitation.prepare_noise(self)
        elif item == "A13":
            if not self.xy_result or not self.xy_result.converged:
                raise ValueError("XY 校相未通过，禁止配置采集")
            self.daq_settings = xy_acquisition.prepare_daq(self.devices, self.params, self.calibrated_phase)
            self.demod_mode = "daq"
        elif item == "A14":
            if self.demod_mode != "daq":
                raise ValueError("解调器已恢复，请重新执行 A13 DAQ 准备")
            if not self.xy_result or not self.xy_result.converged:
                raise ValueError("XY 校相未通过，禁止扫描")
            verify_xy_mode(self.devices["dg_comp"], self.params)
            xy_acquisition.verify_daq_mode(self)
            self.noise_readback = excitation.verify_am_noise(self.devices["dg_probe_am"],
                int(self.mapping["Probe_AOM_AM"]["channel"]), self.noise_waveform, self.params, self.limits)
            if self.z_waveform is not None:
                self.z_readback = excitation.verify_z_noise(self.devices["dg_sweep"],
                    int(self.mapping["Z_magnetic_field"]["channel"]), self.z_waveform, self.params, self.limits)
            acquisition.acquire_scan(self)
            self.states[item]["artifacts"] = ["experiment_config.yaml",
                *[str(path.relative_to(self.run_dir)).replace("\\", "/")
                  for path in sorted((self.run_dir / "raw").iterdir())]]
            last_on = bool(len(self.axes[1]) % 2)
            self.outputs.update(X="ON" if last_on else "OFF", Y="ON" if last_on else "OFF", 温控="ON",
                                **{"Probe AM": f"载波 ON·噪声 {'ON' if last_on else 'OFF'}"})
        elif item == "A15":
            xy_acquisition.restore_demod(self.devices, self.params, self.calibrated_phase)
            self.demod_mode = "phase"
            self.states["A13"] = {"status": "stale", "error": "解调器已恢复；再次扫描需要 DAQ 准备"}


# %% 完整运行
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in stages.ACQUISITION if stage.id != stages.FINISH_ID])
        if ctx.states["A11"]["status"] != "completed":
            raise ValueError(ctx.states["A11"]["error"])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(NoiseSpectrumXYProbeAMParams))


if __name__ == "__main__":
    main()
