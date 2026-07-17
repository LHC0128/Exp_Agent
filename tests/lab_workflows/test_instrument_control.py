import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lab_workflows.devices import ChannelRecord, DeviceRecord
from lab_workflows.instrument_control import (
    _read_generator_channel,
    _validate_generator_output,
    apply_generator_channel,
)


class InstrumentControlTests(unittest.TestCase):
    def test_generator_snapshot_keeps_optional_errors(self):
        instrument = MagicMock()
        instrument.MOD_TYPES = ("AM", "FM", "PM", "FSKey", "PWM")
        instrument.get_wave_parameters.return_value = {
            "shape": "SINusoid",
            "frequency": 1000.0,
            "amplitude": 1.0,
            "offset": 0.0,
            "phase": 45.0,
        }
        instrument.get_output.return_value = False
        instrument.get_mod_type_state.side_effect = lambda kind, channel: kind == "AM"
        instrument.get_mod_source.return_value = "INTernal"
        instrument.get_mod_internal_frequency.return_value = 100.0
        instrument.get_mod_internal_function.return_value = "SINusoid"
        instrument.get_mod_am_depth.return_value = 80.0
        instrument.get_burst_state.return_value = False
        instrument.get_burst_mode.return_value = "TRIGgered"
        instrument.get_burst_ncycles.return_value = 5
        instrument.get_burst_phase.return_value = 0.0
        instrument.get_burst_period.return_value = 0.1
        instrument.get_burst_delay.side_effect = RuntimeError("当前模式不可查询")
        instrument.get_burst_trigger_source.return_value = "IMMediate"
        instrument.get_burst_trigger_slope.return_value = "POSitive"

        channel = SimpleNamespace(
            number=1, mapping_key="Pump_laser_power",
            label="Pump光功率", read_only=False,
        )
        state = _read_generator_channel(instrument, "DG900", channel)
        self.assertEqual(state["shape"], "SINusoid")
        self.assertTrue(state["mod"]["enabled"])
        self.assertEqual(state["mod"]["am_depth"], 80.0)
        self.assertIsNone(state["burst"]["delay"])
        self.assertEqual(state["readback_errors"][0]["field"], "burst.delay")

    def test_apply_mod_disables_burst_and_writes_output_last(self):
        record = DeviceRecord(
            id="DG9QTEST", type="DG900", label="测试信号源",
            resource="FAKE", short_resource="DG9QTEST",
            channels=[ChannelRecord(1, "Pump_laser_power", "Pump光功率")],
        )
        instrument = MagicMock()
        settings = {
            "shape": "SINusoid",
            "frequency": 1000.0,
            "amplitude": 0.5,
            "offset": 0.25,
            "phase": 0.0,
            "output": False,
            "mod": {
                "enabled": True,
                "type": "AM",
                "source": "EXTernal",
                "am_depth": 80.0,
            },
            "burst": {"enabled": False},
            "target_mode": "mod",
        }
        with (
            patch("lab_workflows.instrument_control.find_device", return_value=record),
            patch("lab_workflows.instrument_control._connect", return_value=instrument),
            patch("lab_workflows.instrument_control.read_device", return_value={"ok": True}),
        ):
            result = apply_generator_channel("DG9QTEST", 1, settings)

        self.assertEqual(result, {"ok": True})
        names = [call[0] for call in instrument.method_calls]
        self.assertLess(names.index("set_burst_state"), names.index("set_mod_type_state"))
        self.assertLess(names.index("set_mod_type_state"), names.index("set_output"))
        instrument.disable_all_mod.assert_called_once_with(1)
        instrument.disconnect.assert_called_once()

    def test_read_only_channel_rejects_write_before_connect(self):
        record = DeviceRecord(
            id="DG9QTEST", type="DG900", label="受保护信号源",
            resource="FAKE", short_resource="DG9QTEST",
            channels=[ChannelRecord(1, "Protected_Test", "受保护通道", read_only=True)],
        )
        with patch("lab_workflows.instrument_control.find_device", return_value=record):
            with self.assertRaises(PermissionError):
                apply_generator_channel("DG9QTEST", 1, {"offset": 0.0})

    def test_heat_control_current_waveform_is_allowed_but_larger_peak_is_rejected(self):
        _validate_generator_output(
            "Heat_Control", {"offset": 0.0, "amplitude": 0.5}
        )
        with self.assertRaises(ValueError):
            _validate_generator_output(
                "Heat_Control", {"offset": 0.0, "amplitude": 0.6}
            )


if __name__ == "__main__":
    unittest.main()
