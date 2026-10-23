# %% [markdown]
# # 已知噪声下的 RF 灵敏度：完整运行与分步会话共用流程
# %% 导入
from copy import deepcopy
from dataclasses import replace
import numpy as np
from ...experiment_runtime import load_runtime_params
from ..rf_sensitivity_direct_aw import workflow as shared
from ..rf_sensitivity_direct_aw.stages import initial_states, invalidated_stages
from .models import RFSensitivityKnownNoiseParams
from .preparation import preflight
from .waveform import generate_noise
from .excitation import upload_z
from .acquisition import make_control_directory, inherit_control, execute_controls, seal_controls
from . import run_format, stages


class ControlWorkflow(shared.SingleControlWorkflow):
    params_type = RFSensitivityKnownNoiseParams
    identity = run_format

    def preflight(self, params):
        return preflight(params, self.root)

    def upload_z_rf(self, amplitude, phase_base=None):
        return upload_z(self, amplitude, phase_base)

    def _execute_step(self, item):
        super()._execute_step(item)
        if item == "A14" or item == "A13B":
            self.outputs["Z"] = "ON"


class Workflow(shared.SingleControlWorkflow):
    stages = stages.ACQUISITION
    params_type = RFSensitivityKnownNoiseParams
    identity = run_format

    def __init__(self, *args, **kwargs):
        self.controls = {}
        super().__init__(*args, **kwargs)

    def preflight(self, params):
        return preflight(params, self.root)

    def _new_directory(self, selected):
        previous, inherited = self.directory, deepcopy(self.state)
        old_controls = self.controls
        if previous and self.state["completion_status"] in ("running", "acquired"):
            previous.update_config(completion_status="failed", failure_reason="本轮被重跑替代，保留已采集数据")
        # 子运行按实际有效前置复制，避免共用编排把旧噪声记录带到新轮次。
        self.directory = None
        super()._new_directory(selected)
        for key in ("calibration", "clock_sync", "temperature_control", "optional_connection_errors", "actual_rates"):
            if key in inherited:
                self.state[key] = inherited[key]
        affected = {stage.id for item in selected if item != "A01" for stage in invalidated_stages(self.stages, item)}
        self.controls = {}
        noise = generate_noise(self.params)
        np.savez(self.directory.raw / "noise_waveform.npz", **noise)
        self.state["data_files"] = ["raw/noise_waveform.npz"]
        self.state["controls_sealed"] = False
        for relative in self.params.control_waveform_files:
            params = replace(self.params, arb_waveform_file=relative, control_waveform_files=[relative])
            ctx = ControlWorkflow(params, project_root=self.root, session_id=self.session_id)
            ctx.devices = self.devices
            ctx.noise, ctx.z_rf_frequency_hz = noise, params.z_rf_freq
            ctx.state["calibration"] = deepcopy(self.state["calibration"])
            ctx.state["control_metrics"] = {
                "envelope_rms_V": float(np.sqrt(np.mean(ctx.envelope["envelope_V"] ** 2))),
                "envelope_peak_V": float(np.max(np.abs(ctx.envelope["envelope_V"]))),
                "repeat_frequency_Hz": ctx.envelope["source_repeat_freq_Hz"],
            }
            ctx.directory = make_control_directory(self.directory, relative, params, ctx.state)
            ctx.states = deepcopy(self.states)
            version = ctx.directory.root.name
            if old := old_controls.get(version):
                inherit_control(old, ctx, affected)
            np.savez(ctx.directory.raw / "noise_waveform.npz", **noise)
            self.controls[version] = ctx
        self.save()

    def _execute_step(self, item):
        if item in ("A09", "A10", "A11", "A12", "A13", "A14", "A13B"):
            execute_controls(self, item)
        else:
            super()._execute_step(item)
            if item == "A02":
                for ctx in self.controls.values():
                    ctx.devices = self.devices
            if item == "A15":
                seal_controls(self)


# %% 完整采集入口
def run(params, *, project_root=None):
    ctx = Workflow(params, project_root=project_root)
    failure = None
    try:
        selected = [stage.id for stage in ctx.stages if stage.id not in ("A16", "A13B")]
        if params.linewidth_mode == "frequency_sweep":
            selected.insert(selected.index("A15"), "A13B")
        ctx.execute(selected)
        if ctx.quality_error:
            raise ctx.quality_error
        return ctx.directory.root
    except BaseException as exc:
        failure = exc
        raise
    finally:
        ctx.finish(failure)


def main():
    run(load_runtime_params(RFSensitivityKnownNoiseParams))


if __name__ == "__main__":
    main()
