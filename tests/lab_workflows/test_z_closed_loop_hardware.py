"""闭环输出在开启前检查幅度基准和回读。"""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction import hardware


@pytest.mark.parametrize("amplitude,unit,load", [(20, "VPP", "50"), (10, "VPP", "50"),
                                                (20, "VRMS", "INF"), (10, "VPP", "INF")])
def test_output_requires_matching_readback(monkeypatch, amplitude, unit, load):
    device = Mock()
    device.get_amplitude.return_value = amplitude
    device.get_offset.return_value = 0
    device.get_frequency.return_value = 30000
    device.get_voltage_unit.return_value = unit
    device.get_output_load.return_value = load
    configure = Mock()
    monkeypatch.setattr(hardware, "configure_z_optimal_control_output", configure)
    args = (Mock(), device, 1, SimpleNamespace(repeat_frequency_hz=30000),
            SimpleNamespace(amplitude_vpp=20, offset_v=0))
    if (amplitude, unit, load) == (20, "VPP", "50"):
        actual = hardware.configure_verified_output(*args)
        assert actual["amplitude_vpp"] == 20
        assert device.set_output.call_args_list == [call(False, channel=1), call(True, channel=1)]
    else:
        with pytest.raises(RuntimeError, match="回读不一致"):
            hardware.configure_verified_output(*args)
        device.set_output.assert_called_once_with(False, channel=1)
    configure.assert_called_once_with(*args, output=False)
    assert device.method_calls.index(call.set_output_load("50", channel=1)) < device.method_calls.index(call.get_amplitude(channel=1))
