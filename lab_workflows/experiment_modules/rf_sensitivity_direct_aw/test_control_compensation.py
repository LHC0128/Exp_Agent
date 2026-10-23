"""补偿在 K/B 标定前应用，复用假仪器核对三种控制路径与 R 变体边界。"""
from dataclasses import replace
import numpy as np
import pytest
from .models import RFSensitivityDirectAWParams, RFSensitivityDirectAWSingleParams
from .waveform import load_envelope, synthesize_xy
from .workflow import Workflow
from .test_workflow import control_source, rig
from ..rf_sensitivity_aw_known_noise.models import RFSensitivityKnownNoiseParams
from ..rf_sensitivity_aw_known_noise.workflow import Workflow as KnownWorkflow
from ..rf_sensitivity_aw_known_noise.test_workflow import known_rig
from ..rf_control_scale_sensitivity.preparation import scaled_envelope
from ..rf_control_scale_sensitivity.workflow import Workflow as ScaleWorkflow
from ..rf_control_scale_sensitivity.test_workflow import scale_rig
from ..rf_sensitivity_direct_aw_r.models import RFSensitivityDirectAWRParams


def test_compensation_before_kb_and_yaml_roundtrip(control_source, tmp_path):
    original = load_envelope(control_source, 16384)
    for factor in (1., 1.25, -.8, 0.):
        params = replace(control_source, control_scale_factors={"v1": factor})
        result = load_envelope(params, 16384)
        np.testing.assert_allclose(result["omega_ctrl_Hz"], original["omega_ctrl_Hz"] * factor)
        np.testing.assert_allclose(result["envelope_V"],
            (original["omega_ctrl_Hz"] * factor - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v)
        np.testing.assert_array_equal(result["source_omega_ctrl_Hz"], original["source_omega_ctrl_Hz"])
        assert result["control_scale_factor"] == factor
        saved = params.save_yaml(tmp_path / "params.yaml")
        assert RFSensitivityDirectAWParams.from_yaml(saved).to_external() == params.to_external()
    zero = load_envelope(replace(control_source, xy_ctrl_b_hz=0, control_scale_factors={"v1": 0.}), 16384)
    waveform = synthesize_xy(control_source, zero, 0)
    assert not np.any(waveform["x_normalized"])
    assert not np.any(waveform["y_normalized"])


def test_old_parameters_and_r_variant_keep_unit_factor(control_source):
    # 旧单控制快照不包含后来新增的多选和 PSD 参数，不能从当前根实验整包复制。
    values = {key: value for key, value in control_source.to_external().items()
              if key in RFSensitivityDirectAWSingleParams.external_names()}
    values.pop("CONTROL_SCALE_FACTORS")
    assert RFSensitivityDirectAWParams.from_external(values).control_scale_factor() == 1
    assert RFSensitivityKnownNoiseParams().control_scale_factor("v1/waveforms/optimal_control_waveform.csv") == 1
    assert RFSensitivityDirectAWRParams.from_external(values | {"CONTROL_SCALE_FACTORS": {}}).control_scale_factor() == 1
    assert "CONTROL_SCALE_FACTORS" not in RFSensitivityDirectAWRParams.external_names()
    with pytest.raises(ValueError, match="未知参数"):
        RFSensitivityDirectAWRParams.from_external(values | {"CONTROL_SCALE_FACTORS": {"v1": 1.2}})


def test_single_control_rejects_unsafe_compensation_before_connecting(rig):
    with pytest.raises(ValueError):
        Workflow(replace(rig.params, control_scale_factors={"v1": 1e8}), project_root=rig.tmp_path)
    assert not rig.events


def test_all_known_noise_controls_are_checked_before_connecting(known_rig):
    with pytest.raises(ValueError):
        KnownWorkflow(replace(known_rig.params, control_scale_factors={"v2": 1e8}), project_root=known_rig.tmp_path)
    assert not known_rig.events


def test_scale_defers_compensation_and_applies_it_exactly_once(scale_rig):
    params = replace(scale_rig.params, control_scale_factors={"ControlA": 1.5})
    original = load_envelope(replace(params, control_scale_factors={}), 16384)
    ctx = ScaleWorkflow(params, project_root=scale_rig.tmp_path)
    np.testing.assert_array_equal(ctx.envelope["omega_ctrl_Hz"], original["omega_ctrl_Hz"])
    result = scaled_envelope(ctx.params, ctx.envelope, .8)
    np.testing.assert_allclose(result["omega_ctrl_Hz"], original["omega_ctrl_Hz"] * .8 * 1.5)
    assert result["effective_control_scale"] == pytest.approx(1.2)
    assert not scale_rig.events
