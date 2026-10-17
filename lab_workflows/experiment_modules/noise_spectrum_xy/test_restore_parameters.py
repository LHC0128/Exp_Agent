"""共享结束路径只能恢复已成功校相的解调配置。"""

import pytest

from . import workflow
from .test_sine_mode import copy_config, fake_hardware, scan_params
from ..noise_spectrum_xy_known_noise import test_workflow as known_tests
from ..noise_spectrum_xy_probe_am import test_workflow as probe_tests


@pytest.mark.parametrize("workflow_type,params_factory,rig,daq_id", [
    (workflow.Workflow, scan_params, fake_hardware, "A11"),
    (known_tests.workflow.Workflow, known_tests.scan_params, known_tests.rig, "A12"),
    (probe_tests.workflow.Workflow, probe_tests.scan_params, probe_tests.rig, "A13"),
], ids=["sine", "known-noise", "probe-am"])
@pytest.mark.parametrize("rerun", [False, True], ids=["unexecuted", "rerun-then-unexecuted"])
def test_finish_after_unexecuted_changes_restores_last_phase_settings(
        tmp_path, workflow_type, params_factory, rig, daq_id, rerun):
    copy_config(tmp_path)
    params = params_factory()
    with rig(tmp_path) as hw:
        hw.rates.side_effect = lambda device, config, **kwargs: 800.0
        ctx = workflow_type(params, project_root=tmp_path)
        selected = []
        for stage in ctx.acquisition_stages:
            selected.append(stage.id)
            if stage.id == daq_id:
                break
        ctx.execute(selected)
        assert ctx.demod_mode == "daq"
        ctx.update_parameters({
            "HF2_DEMOD_RATE": params.hf2_demod_rate / 2,
            "HF2_DEMOD_TC": params.hf2_demod_tc * 2,
        })
        if rerun:
            hw.samples.side_effect = [dict(r=1.0, x=1.0, y=0.0, theta=0.0)] * 20
            ctx.execute(selected)
            params = ctx.params
            ctx.update_parameters({"HF2_DEMOD_RATE": params.hf2_demod_rate / 2})
        hw.rates.reset_mock()
        ctx.finish()
        restored = hw.rates.call_args.args[1]
        assert restored.rate == params.hf2_demod_rate
        assert restored.time_constant == params.hf2_demod_tc
        assert ctx.params.hf2_demod_rate == params.hf2_demod_rate / 2
        assert ctx.closed
