# %% [markdown]
# # XY 已知可控噪声谱：采集与会话编排
# 导入不连接仪器；完整与分步模式调用相同业务步骤。

# %% 导入
from ..noise_spectrum_xy.workflow import execute_shared_nodes
from ..noise_spectrum_xy import acquisition as sine_acquisition
from ..noise_spectrum_xy import phase
from ...experiment_runtime import load_runtime_params
from ...steps.lockin import verify_lockin_acquisition_mode
from ...steps.noise_xy_scan import NoiseXYScan
from .models import NoiseSpectrumXYKnownNoiseParams
from . import acquisition, excitation, stages, preparation
from .preparation import preflight
from .safety import safe_scan_outputs_off


class Workflow(NoiseXYScan):
    data_type = acquisition.DATA_TYPE
    experiment_id = 'noise-spectrum-xy-known-noise'
    acquisition_stages = stages.ACQUISITION
    finish_id = stages.FINISH_ID
    preflight = staticmethod(preflight)
    invalidate = staticmethod(stages.invalidate)
    validate_selection = staticmethod(stages.validate_selection)
    always_shutdown = True
    output_keys = ("X", "Y", "Z", "温控", "Pump", "共用触发")

    def shutdown_outputs(self):
        report = safe_scan_outputs_off(self.devices)
        self.outputs["共用触发"] = "OFF" if report.completed else "未知"
        return report

    def restore_demod(self, params):
        # 结束时恢复走共享 acquisition：A14 节点的显式恢复另走本模块重导出。
        sine_acquisition.restore_demod(self.devices, params, self.calibrated_phase)

    def _execute_step(self, item):
        if item == "A02":
            if not self.devices:
                self.devices, self.connection = preparation.connect_instruments(self)
            return
        if item <= "A10":
            return execute_shared_nodes(self, item)
        if item == "A11":
            excitation.prepare_noise(self)
        elif item == "A12":
            if not self.xy_result or not self.xy_result.converged:
                raise ValueError("XY 校相未通过，禁止配置采集")
            self.daq_settings = acquisition.prepare_daq(self.devices, self.params, self.calibrated_phase)
            self.demod_mode = "daq"
        elif item == "A13":
            if self.demod_mode != "daq":
                raise ValueError("解调器已恢复，请重新执行 A12 DAQ 准备")
            if not self.xy_result or not self.xy_result.converged:
                raise ValueError("XY 校相未通过，禁止扫描")
            phase.verify_xy_mode(self.devices["dg_comp"], self.params)
            verify_lockin_acquisition_mode(self.devices["hf2"],
                demod_index=self.params.hf2_demod_idx, requested_rate_hz=self.params.hf2_daq_rate,
                acquisition_rate_hz=self.daq_settings[0], time_constant_s=self.params.hf2_daq_tc,
                phase_deg=self.calibrated_phase)
            self.noise_readback = excitation.verify_noise_waveform(self.devices["dg_sweep"], self.noise_waveform, self.params, self.limits)
            acquisition.acquire_scan(self)
            self.states[item]["artifacts"] = ["experiment_config.yaml", *[str(path.relative_to(self.run_dir)).replace("\\", "/") for path in sorted((self.run_dir / "raw").iterdir())]]
            self.outputs.update(X="ON" if self.axes[1][-1] else "OFF", Y="ON" if self.axes[1][-1] else "OFF",
                                Z="ON" if len(self.axes[1]) % 2 else "OFF", 温控="ON")
        elif item == "A14":
            acquisition.restore_demod(self.devices, self.phase_parameters or self.params, self.calibrated_phase)
            self.demod_mode = "phase"
            self.states["A12"] = {"status": "stale", "error": "解调器已恢复；再次扫描需要 DAQ 准备"}


# %% 完整运行
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        ctx.execute([stage.id for stage in stages.ACQUISITION if stage.id != stages.FINISH_ID])
        if ctx.states["A10"]["status"] != "completed":
            raise ValueError(ctx.states["A10"]["error"])
        return ctx.run_dir
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(NoiseSpectrumXYKnownNoiseParams))


if __name__ == "__main__":
    main()
