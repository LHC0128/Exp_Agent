"""最优控制工作点兼容已移除 TEC 判稳字段的强类型调用方。"""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import mx_z_optimal_control as workpoint


@pytest.mark.parametrize("module", [
    "mx_y_optimal_control_rf_frequency_response",
    "mx_z_optimal_control_rf_sensitivity",
    "mx_z_optimal_control_xy_leakage_response",
    "mx_z_optimal_control_xy_rf_phase_response",
    "mx_z_optimal_control_xyz_balance",
    "mx_z_optimal_control_dg4000_bias_xyz_balance",
])
def test_typed_callers_only_set_ui_temperature(monkeypatch, module):
    params = import_module(f"lab_workflows.experiment_modules.{module}.definition").ADAPTER.params_type()
    tec = SimpleNamespace(set_target_temperature=Mock())
    devices = {name: Mock() for name in ("z_field", "laser", "pump_rf", "temp_switch", "hf2")}
    devices["tec"] = tec
    devices["hf2"].get_double.return_value = 0.0
    channels = dict(trigger=2, z_field=1, pump_laser=1, probe_laser=2,
                    pump_carrier=1, pump_gate=2, temp_switch=2)
    monkeypatch.setattr(workpoint, "configure_optimal_control_trigger", Mock())
    monkeypatch.setattr(workpoint, "configure_z_optimal_control_output", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_signal_input", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_oscillator", Mock())
    monkeypatch.setattr(workpoint.demod, "configure_demodulator", Mock(return_value=1000.0))
    waiter = Mock(side_effect=AssertionError("普通气室采集不得调用 TEC 判稳"))

    result = workpoint.configure_mx_z_optimal_control_workpoint(
        params, devices, channels, {}, None, None, demod_frequency_hz=1000.0,
        cancellation=None, xy_output_configurator=Mock(),
        reference_clock_configurator=Mock(return_value={}), pump_gate_setter=Mock(),
        temperature_stability_waiter=waiter, configure_main_field_output=False,
    )

    tec.set_target_temperature.assert_called_once_with(params.temperature_c, channel=1)
    waiter.assert_not_called()
    assert result[0] == 1000.0
    assert result[1] is None
