"""可选 RF 线路、真实选件、索引贯穿及旧参数的无硬件回归。"""
from dataclasses import replace

import numpy as np
import pytest
import yaml
from lockin_amplifier import MFLIInstrument

from ...common import load_mapping, WorkflowCancelled
from ...lockin_compatibility import lockin_preflight
from . import acquisition, preparation, workflow
from .models import RFSensitivityDirectAWParams
from .test_workflow import FakeHF2, assert_shutdown, control_source, rig


class FakeMFLI(FakeHF2):
    model = "MFLI"
    _capabilities = staticmethod(MFLIInstrument._capabilities)

    def __init__(self, instance, **kwargs):
        super().__init__(instance, **kwargs)
        self.capabilities = self._capabilities(instance.actual_options)

    def get_double(self, path):
        return 60e6 if path.endswith("/clockbase") else super().get_double(path)


def select_model(instance, monkeypatch, model):
    path = instance.tmp_path / "params/mapping.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("lockin_r", "lockin_xy"):
        config["mapping"][key]["device_id"] = "mfli_dev30661" if model == "MFLI" else "hf2_dev18246"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    instance.mapping = load_mapping(instance.tmp_path)
    instance.actual_options = ["MF-MD", "MF-F5M", "MF-DIG"]

    def create(config):
        instance.lockin = (FakeMFLI if model == "MFLI" else FakeHF2)(instance, device_id=config["device_id"])
        return instance.lockin

    monkeypatch.setattr(preparation, "create_lockin", create)


ROUTES = [
    (model, "physical", source, output)
    for model in ("HF2", "MFLI")
    for source in (("voltage1", "aux_input0", "aux_input1") if model == "HF2" else ("aux_input0", "aux_input1"))
    for output in range(4)
] + [("MFLI", "internal", "voltage1", output) for output in range(4)]


@pytest.mark.parametrize("model,route,source,output", ROUTES)
def test_selected_channels_configure_only_supported_nodes(rig, monkeypatch, model, route, source, output):
    select_model(rig, monkeypatch, model)
    params = replace(rig.params, demod3_idx=2, rf_input_route=route,
                     rf_aux_output_idx=output, rf_physical_input_source=source)
    ctx = workflow.Workflow(params, project_root=rig.tmp_path)
    try:
        ctx.execute([stage.id for stage in ctx.stages[:10]])
        instr = rig.lockin
        expected = 4 + output if route == "internal" else ({"voltage1": 1, "aux_input0": 2, "aux_input1": 3} if model == "HF2" else {"aux_input0": 8, "aux_input1": 9})[source]
        assert instr.nodes[f"{instr.demod_path(2)}/adcselect"] == params.demod3_adc_select == expected
        assert instr.nodes[f"{instr.aux_out_path(output)}/outputselect"] == 1
        assert instr.nodes[f"{instr.aux_out_path(output)}/demodselect"] == 0
        assert {key.split('/')[3] for key in instr.nodes if '/auxouts/' in key} == {str(output)}
        if model == "MFLI":
            assert not any('/sigins/1/' in key for key in instr.nodes)
            assert instr.nodes[f"{instr.aux_out_path(output)}/limitupper"] == 10
        elif source == "voltage1":
            assert instr.nodes[f"{instr.sig_in_path(1)}/range"] == params.loopback_input_range_v
        else:
            assert f"{instr.sig_in_path(1)}/range" not in instr.nodes
    finally:
        ctx.finish()


@pytest.mark.parametrize("model", ["HF2", "MFLI"])
@pytest.mark.parametrize("variant", ["X", "R"])
def test_non_three_index_reaches_calibration_daq_and_metadata(rig, monkeypatch, model, variant):
    select_model(rig, monkeypatch, model)
    requests = []

    def acquire(*args, **kwargs):
        requests.append(kwargs["demod_idx"])
        return rig.acquire(*args, **kwargs)

    monkeypatch.setattr(acquisition.daq, "acquire_data", acquire)
    if variant == "R":
        from ..rf_sensitivity_direct_aw_r.models import RFSensitivityDirectAWRParams
        from ..rf_sensitivity_direct_aw_r.workflow import Workflow
        values = {key: value for key, value in rig.params.to_external().items()
                  if key in RFSensitivityDirectAWRParams.external_names()}
        params = RFSensitivityDirectAWRParams.from_external(values | {"Z_RF_NOISE_FREQ": 3000})
    else:
        params, Workflow = rig.params, workflow.Workflow
    params = replace(params, demod3_idx=2, rf_aux_output_idx=3,
                     rf_input_route="internal" if model == "MFLI" else "physical")
    ctx = Workflow(params, project_root=rig.tmp_path)
    try:
        # A13B 为模式门控的可选节点，与完整运行同规则按线宽模式过滤。
        ctx.execute([stage.id for stage in ctx.stages if stage.id != "A16"
                     and (params.linewidth_mode == "frequency_sweep" or stage.id != "A13B")])
    finally:
        ctx.finish()
    assert requests and set(requests) == {2}
    config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"]["DEMOD3_IDX"] == 2
    assert "AuxOut4" in config["signal_chain"] and "index 2" in config["signal_chain"]
    assert config["calibration"]["demod3"]["demod_idx"] == 2
    assert f"{rig.lockin.demod_path(2)}/rate" in config["hf2_noise_snapshot"]
    for name in ("response_data.npz", "noise_D3_0000.npz"):
        with np.load(ctx.directory.raw / name) as data:
            assert data["demod_idx"] == 2
    assert_shutdown(rig)


@pytest.mark.parametrize("changes", [
    {"demod3_idx": 0}, {"demod3_idx": 6}, {"rf_aux_output_idx": -1},
    {"rf_aux_output_idx": 4}, {"rf_input_route": "unknown"}, {"rf_input_route": "internal"},
])
def test_invalid_hf2_routes_fail_before_connection(rig, changes):
    with pytest.raises((ValueError, PermissionError)):
        workflow.Workflow(replace(rig.params, **changes), project_root=rig.tmp_path)
    assert not any(event[1] == "connect" for event in rig.events)


def test_actual_md_option_is_checked_before_outputs(rig, monkeypatch):
    select_model(rig, monkeypatch, "MFLI")
    rig.actual_options = []
    with pytest.raises(ValueError, match="MF-MD"):
        workflow.run(rig.params, project_root=rig.tmp_path)
    assert not any(event[1] in ("setup_dc", "setup_sine") for event in rig.events)
    assert any(event[1] == "disconnect" for event in rig.events)


def test_actual_dig_option_is_checked_before_outputs(rig, monkeypatch):
    select_model(rig, monkeypatch, "MFLI")
    rig.actual_options = ["MF-MD", "MF-F5M"]
    with pytest.raises(ValueError, match="MF-DIG"):
        workflow.run(replace(rig.params, rf_input_route="internal", noise_rate=200000), project_root=rig.tmp_path)
    assert not any(event[1] in ("setup_dc", "setup_sine") for event in rig.events)
    assert any(event[1] == "disconnect" for event in rig.events)


@pytest.mark.parametrize("failure", ["missing_dig", "too_long"])
def test_scope_preflight_failure_does_not_connect_or_start_outputs(rig, monkeypatch, failure):
    select_model(rig, monkeypatch, "MFLI")
    params = replace(rig.params, rf_input_route="internal", noise_rate=200000, noise_duration=100 if failure == "too_long" else 1)
    if failure == "missing_dig":
        path = rig.tmp_path / "params/devices.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["devices"]["mfli_dev30661"]["capabilities"]["options"].remove("MF-DIG")
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="MF-DIG" if failure == "missing_dig" else "超出内存"):
        workflow.Workflow(params, project_root=rig.tmp_path)
    assert not rig.events


@pytest.mark.parametrize("model", ["HF2", "MFLI"])
@pytest.mark.parametrize("missing", [True, False])
def test_selected_channel_limit_failure_is_non_mutating(rig, monkeypatch, model, missing):
    select_model(rig, monkeypatch, model)
    path = rig.tmp_path / "params/safety_limits.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    key = f"{rig.mapping['lockin_r']['device_library_id']}_aux_output_3"
    if missing:
        config["safety_limits"].pop(key)
    else:
        config["safety_limits"][key]["max"] = 1
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, PermissionError)):
        workflow.Workflow(replace(rig.params, rf_aux_output_idx=3), project_root=rig.tmp_path)
    assert not rig.events


@pytest.mark.parametrize("changes", [{"demod3_idx": 4}, {"rf_physical_input_source": "voltage1"}])
def test_invalid_mfli_routes_fail_before_connection(rig, monkeypatch, changes):
    select_model(rig, monkeypatch, "MFLI")
    with pytest.raises(ValueError):
        workflow.Workflow(replace(rig.params, **changes), project_root=rig.tmp_path)
    assert not rig.events


def test_declared_md_option_is_required_before_connection(rig, monkeypatch):
    select_model(rig, monkeypatch, "MFLI")
    path = rig.tmp_path / "params/devices.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["devices"]["mfli_dev30661"]["capabilities"]["options"].remove("MF-MD")
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="MF-MD"):
        workflow.Workflow(rig.params, project_root=rig.tmp_path)
    assert not rig.events


@pytest.mark.parametrize("condition", ["range_exceeded", "range_invalid", "unsupported_source", "tip_protect"])
def test_actual_mfli_input_and_output_safety_rechecked_before_aux_write(rig, monkeypatch, condition):
    select_model(rig, monkeypatch, "MFLI")
    ctx = workflow.Workflow(replace(rig.params, rf_input_route="internal"), project_root=rig.tmp_path)
    try:
        ctx.execute([stage.id for stage in ctx.stages[:9]])
        instr = rig.lockin
        if condition.startswith("range_"):
            instr.nodes[f"{instr.sig_in_path(0)}/range"] = 10.0 if condition == "range_exceeded" else 0.0
        elif condition == "unsupported_source":
            instr.nodes[f"{instr.demod_path(0)}/adcselect"] = 8
        else:
            instr.nodes[f"{instr.aux_out_path(1)}/tipprotect/enable"] = 1
        before = dict(instr.nodes)
        with pytest.raises(ValueError):
            ctx.execute(["A10"])
        assert instr.nodes == before
    finally:
        ctx.finish()
    assert_shutdown(rig)


def test_legacy_parameters_keep_hf2_wiring_and_other_experiments_stay_restricted(rig, monkeypatch):
    legacy = {key: value for key, value in rig.params.to_external().items() if not key.startswith("RF_INPUT_") and key not in {"RF_AUX_OUTPUT_IDX", "RF_PHYSICAL_INPUT_SOURCE"}}
    params = RFSensitivityDirectAWParams.from_external(legacy, schema_version=2)
    assert (params.rf_input_route, params.rf_aux_output_idx, params.rf_input_source) == ("physical", 1, "voltage1")
    select_model(rig, monkeypatch, "MFLI")
    with pytest.raises(ValueError, match="不会自动更换接线"):
        workflow.Workflow(params, project_root=rig.tmp_path)
    assert lockin_preflight("noise-spectrum-xy-demod3-r", rig.params.to_external(), mapping=rig.mapping)


def test_route_update_invalidates_a10_without_writing_and_rerun_keeps_old_data(rig):
    ctx = workflow.Workflow(rig.params, project_root=rig.tmp_path)
    try:
        ctx.execute([stage.id for stage in ctx.stages[:13]])
        previous = ctx.directory.root
        raw = (previous / "raw/response_data.npz").read_bytes()
        count = len(rig.events)
        ctx.update_parameters({"RF_AUX_OUTPUT_IDX": 2, "RF_PHYSICAL_INPUT_SOURCE": "aux_input0", "DEMOD3_IDX": 2})
        assert len(rig.events) == count
        assert ctx.states["A09"]["status"] == "completed"
        assert ctx.states["A10"]["status"] == ctx.states["A13"]["status"] == "stale"
        ctx.execute([stage.id for stage in ctx.stages[9:13]])
        assert ctx.directory.root != previous
        assert (previous / "raw/response_data.npz").read_bytes() == raw
        assert "AuxOut3" in ctx.state["signal_chain"]
    finally:
        ctx.finish()


@pytest.mark.parametrize("failure", [WorkflowCancelled("模拟取消"), RuntimeError("模拟 DAQ 失败")])
def test_mfli_internal_noise_failures_still_restore_and_shutdown(rig, monkeypatch, failure):
    select_model(rig, monkeypatch, "MFLI")
    rig.daq_error = failure
    with pytest.raises(type(failure), match="模拟"):
        workflow.run(replace(rig.params, rf_input_route="internal", demod3_idx=2), project_root=rig.tmp_path)
    assert_shutdown(rig)
    assert rig.lockin.nodes[f"{rig.lockin.demod_path(2)}/timeconstant"] == rig.params.demod3_tc
