import unittest
from unittest.mock import MagicMock

from signal_generator.instrument import DG4000Instrument


class DG4000SetDcVoltageTests(unittest.TestCase):
    """验证 DG4000 已选 DC 时通过 APPLy:USER 设置电平。"""

    def _instrument(self) -> DG4000Instrument:
        device = DG4000Instrument("FAKE", channel=1)
        device._inst = MagicMock()
        return device

    def test_set_dc_voltage_writes_apply_user(self):
        device = self._instrument()
        device.set_dc_voltage(0.3, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands, [
            ":SOURce1:APPLy:USER 0,0,3.000000e-01,0",
        ])

    def test_set_dc_voltage_handles_negative_values(self):
        device = self._instrument()
        device.set_dc_voltage(-0.5, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands, [
            ":SOURce1:APPLy:USER 0,0,-5.000000e-01,0",
        ])

    def test_set_dc_voltage_zero_uses_bound_channel_without_toggling_output(self):
        device = self._instrument()
        device.channel = 2
        device.set_dc_voltage(0.0)
        device._inst.write.assert_called_once_with(
            ":SOURce2:APPLy:USER 0,0,0.000000e+00,0"
        )

    def test_setup_dc_sets_shape_level_and_output(self):
        device = self._instrument()
        device.setup_dc(0.3, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands, [
            ":SOURce1:FUNCtion:SHAPe DC",
            ":SOURce1:APPLy:USER 0,0,3.000000e-01,0",
            ":OUTPut1:STATe ON",
        ])

    def test_get_dc_voltage_queries_reported_offset(self):
        device = self._instrument()
        device._inst.query.return_value = "3.000000e-01"
        self.assertAlmostEqual(device.get_dc_voltage(channel=1), 0.3)
        self.assertEqual(
            [call.args[0] for call in device._inst.query.call_args_list],
            [
                ":SOURce1:VOLTage:LEVel:IMMediate:OFFSet?",
            ],
        )


if __name__ == "__main__":
    unittest.main()
