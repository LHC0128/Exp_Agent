"""公共安全校验的有限数值边界；不连接仪器。"""

from unittest.mock import Mock

import pytest

from . import common


NONFINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("value", NONFINITE, ids=["nan", "pos_inf", "neg_inf"])
def test_nonfinite_rejected_before_rules_or_config_io(monkeypatch, value):
    reader = Mock(return_value={})
    monkeypatch.setattr(common, "load_safety_limits", reader)
    for limits in ({"rf_coil": {"min": 0.0, "max": 2.0}},
                   {"rf_coil": {"min": None, "max": None}}, {"other": {"max": 2.0}}, None):
        with pytest.raises(ValueError, match="rf_coil.*有限"):
            common.validate_safety_limit("rf_coil", value, limits)
    reader.assert_not_called()


def test_finite_values_keep_existing_bounds_and_return_types():
    limits = {"rf_coil": {"min": 0.0, "max": 2.0}}
    for value in (0, 0.25, 2.0, True):
        result = common.validate_safety_limit("rf_coil", value, limits)
        assert result is value and type(result) is type(value)
    for value in (-0.01, 2.01):
        with pytest.raises(ValueError, match="安全拦截"):
            common.validate_safety_limit("rf_coil", value, limits)
    # 本轮只补非有限值检查，不改变缺规则或无上下限的有限值透传策略。
    for limits in ({"other": {"max": 2.0}}, {"unbounded": {"min": None, "max": None}}):
        value = -123.5
        assert common.validate_safety_limit("unbounded", value, limits) is value


def test_output_load_markers_still_use_their_own_normalizer():
    from signal_generator.types import normalize_output_load

    assert normalize_output_load("INF") == "INF"
    assert normalize_output_load(9.9e37) == "INF"


@pytest.mark.parametrize("value", NONFINITE, ids=["nan", "pos_inf", "neg_inf"])
def test_original_y_rf_boundary_blocks_nonfinite_before_any_output(monkeypatch, value):
    from .experiment_modules.mx_z_optimal_control_rf_sensitivity import excitation

    monkeypatch.setattr(common, "load_safety_limits", lambda: {
        "Y_magnetic_field": {"min": -1.0, "max": 1.0}, "rf_coil": {"min": 0.0, "max": 2.0}})
    for field in ("amplitude_vpp", "offset_v"):
        device = Mock()
        values = dict(amplitude_vpp=0.2, offset_v=0.0, phase_deg=0.0)
        values[field] = value
        with pytest.raises(ValueError, match="有限"):
            excitation._rearm_y_rf(device, 2, **values)
        assert device.method_calls == []
