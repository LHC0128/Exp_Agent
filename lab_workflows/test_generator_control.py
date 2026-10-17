"""信号源卡片单位、包络校验与写入顺序的无硬件回归。"""

import math
import unittest
from unittest.mock import Mock, call, patch

from lab_workflows import instrument_control as control
from lab_workflows.devices import ChannelRecord, DeviceRecord


class GeneratorControlTests(unittest.TestCase):
    def setUp(self):
        self.current = {
            "shape": "SINusoid", "voltage_unit": "VPP", "amplitude": 0.1,
            "offset": 0.0, "load": "INF", "output": True,
        }
        self.record = DeviceRecord(
            "fake", "DG900", "假信号源", "fake", "fake",
            channels=[ChannelRecord(2, "test_voltage", "测试电压")],
        )
        self.instrument = Mock()
        self.limits = {"test_voltage": {"min": -1.0, "max": 1.0}}
        for name, value in (
            ("_read_generator_channel", self.current), ("load_safety_limits", self.limits),
        ):
            patcher = patch.object(control, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self, settings):
        control._apply_generator_connected(
            {"mapping_key": "test_voltage"}, self.record, self.instrument, settings,
        )

    def test_units_write_validated_vpp_before_restoring_display_unit(self):
        for model in ("DG4000", "DG900"):
            self.record.type = model
            for shape, factor in (("SINusoid", math.sqrt(8)), ("SQUare", 2), ("RAMP", math.sqrt(12))):
                for unit, amplitude, expected in (
                    ("VPP", 0.1, 0.1), ("VRMS", 0.1, 0.1 * factor),
                    ("DBM", -10, math.sqrt(0.005) * factor),
                ):
                    with self.subTest(model=model, shape=shape, unit=unit):
                        self.instrument.reset_mock()
                        self.current["voltage_unit"] = "VRMS"
                        self.apply({"shape": shape, "amplitude": amplitude, "voltage_unit": unit, "load": 50})
                        self.instrument.set_amplitude.assert_called_once_with(expected, 2)
                        calls = self.instrument.mock_calls
                        self.assertLess(calls.index(call.set_voltage_unit("VPP", 2)), calls.index(call.set_amplitude(expected, 2)))
                        self.assertLess(calls.index(call.set_output_load(50, 2)), calls.index(call.set_amplitude(expected, 2)))
                        self.assertEqual(self.instrument.set_voltage_unit.call_args, call(unit, 2))

    def test_unsafe_or_invalid_values_do_not_write(self):
        cases = [
            {"amplitude": 0.8, "voltage_unit": "VRMS"},
            {"offset": 0.9, "amplitude": 0.1, "voltage_unit": "VRMS"},
            {"amplitude": 20, "voltage_unit": "DBM", "load": 50},
            {"amplitude": float("nan")}, {"amplitude": float("inf")},
            {"offset": float("nan")}, {"amplitude": -1},
            {"amplitude": 1, "voltage_unit": "UNKNOWN"},
            {"shape": "PULSe", "amplitude": 0.1, "voltage_unit": "VRMS"},
            {"shape": "USER", "amplitude": 0.1, "voltage_unit": "VRMS"},
            {"amplitude": 1e100, "voltage_unit": "DBM", "load": 50},
        ]
        cases += [{"amplitude": -10, "voltage_unit": "DBM", "load": load}
                  for load in ("INF", "9.9E+37", 9.9e37, 0, -50, float("inf"))]
        cases += [{"voltage_unit": "DBM"}, {"voltage_unit": "DBM", "load": "INF"}]
        for settings in cases:
            with self.subTest(settings=settings):
                self.instrument.reset_mock()
                with self.assertRaises(ValueError):
                    self.apply(settings)
                self.assertEqual(self.instrument.mock_calls, [])

    def test_missing_readbacks_are_not_assumed_to_be_zero(self):
        for field in ("offset", "amplitude", "voltage_unit"):
            with self.subTest(field=field), patch.dict(self.current, {field: None}):
                self.instrument.reset_mock()
                with self.assertRaises(ValueError):
                    self.apply({"output": True})
                self.assertEqual(self.instrument.mock_calls, [])

    def test_partial_edit_validates_current_rms_amplitude(self):
        self.current.update(voltage_unit="VRMS", amplitude=0.8)
        with self.assertRaises(ValueError):
            self.apply({"frequency": 1000})
        self.assertEqual(self.instrument.mock_calls, [])

    def test_partial_unit_change_keeps_physical_amplitude(self):
        self.current.update(voltage_unit="VRMS", amplitude=0.1)
        self.apply({"voltage_unit": "VPP", "phase": 90})
        self.instrument.set_amplitude.assert_not_called()
        self.assertEqual(self.instrument.set_voltage_unit.call_args, call("VPP", 2))

    def test_partial_load_change_reapplies_validated_vpp(self):
        self.current.update(voltage_unit="VRMS", amplitude=0.1)
        self.apply({"load": 50})
        self.instrument.set_amplitude.assert_called_once_with(0.1 * math.sqrt(8), 2)
        self.assertEqual(self.instrument.set_voltage_unit.call_args, call("VRMS", 2))

    def test_dc_ignores_hidden_ac_amplitude_and_keeps_final_dc_sequence(self):
        for model in ("DG4000", "DG900"):
            with self.subTest(model=model):
                self.instrument.reset_mock()
                self.record.type = model
                self.apply({"shape": "DC", "amplitude": 100, "voltage_unit": "VRMS",
                            "offset": 0.3, "output": True,
                            "mod": {"enabled": False}, "burst": {"enabled": False}})
                self.instrument.set_amplitude.assert_not_called()
                self.assertEqual(self.instrument.set_voltage_unit.call_args, call("VPP", 2))
                if model == "DG4000":
                    self.assertEqual(self.instrument.mock_calls[-3:], [
                        call.set_shape("DC", 2), call.set_dc_voltage(0.3, 2), call.set_output(True, 2),
                    ])
                else:
                    self.instrument.setup_dc.assert_called_once_with(0.3, 2)

    def test_unknown_unit_can_still_be_turned_off_or_recovered(self):
        self.record.type = "DG4000"
        self.current.update(voltage_unit=None, amplitude=None, offset=None)
        self.apply({"output": False})
        self.assertEqual(self.instrument.mock_calls, [call.set_output(False, 2)])
        self.instrument.reset_mock()
        self.apply({"voltage_unit": "VPP"})
        self.instrument.set_voltage_unit.assert_called_with("VPP", 2)
        self.instrument.set_amplitude.assert_not_called()
        self.instrument.set_output.assert_not_called()

    def test_legacy_api_uses_same_validation_for_selected_channel(self):
        self.record.channels.insert(0, ChannelRecord(1, "other", "另一通道"))
        with patch.object(control, "find_device", return_value=self.record), \
                patch.object(control, "_connect", return_value=self.instrument), \
                patch.object(control, "read_device", return_value={"ok": True}):
            result = control.apply_generator_channel("fake", 2, {
                "shape": "SINusoid", "amplitude": 0.1, "voltage_unit": "VRMS",
            })
        self.assertEqual(result, {"ok": True})
        self.instrument.set_amplitude.assert_called_once_with(0.1 * math.sqrt(8), 2)
        self.instrument.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
