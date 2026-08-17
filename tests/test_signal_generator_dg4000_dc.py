import unittest
from unittest.mock import MagicMock

from signal_generator.instrument import DG4000Instrument


class DG4000SetDcVoltageTests(unittest.TestCase):
    """验证 DG4000 DC 电平的 HIGH/LOW 三步写入（DG4162 实测行为）。"""

    def _instrument(self) -> DG4000Instrument:
        device = DG4000Instrument("FAKE", channel=1)
        device._inst = MagicMock()
        return device

    def test_set_dc_voltage_writes_low_high_low_sequence(self):
        device = self._instrument()
        device.set_dc_voltage(0.3, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands, [
            ":SOURce1:VOLTage:LEVel:IMMediate:LOW 2.990000e-01",
            ":SOURce1:VOLTage:LEVel:IMMediate:HIGH 3.010000e-01",
            ":SOURce1:VOLTage:LEVel:IMMediate:LOW 2.990000e-01",
        ])

    def test_set_dc_voltage_handles_negative_values(self):
        device = self._instrument()
        device.set_dc_voltage(-0.5, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands, [
            ":SOURce1:VOLTage:LEVel:IMMediate:LOW -5.010000e-01",
            ":SOURce1:VOLTage:LEVel:IMMediate:HIGH -4.990000e-01",
            ":SOURce1:VOLTage:LEVel:IMMediate:LOW -5.010000e-01",
        ])

    def test_setup_dc_sets_shape_level_and_output(self):
        device = self._instrument()
        device.setup_dc(0.3, channel=1)
        commands = [call.args[0] for call in device._inst.write.call_args_list]
        self.assertEqual(commands[0], ":SOURce1:FUNCtion:SHAPe DC")
        self.assertEqual(commands[1], ":SOURce1:VOLTage:LEVel:IMMediate:LOW 2.990000e-01")
        self.assertEqual(commands[2], ":SOURce1:VOLTage:LEVel:IMMediate:HIGH 3.010000e-01")
        self.assertEqual(commands[3], ":SOURce1:VOLTage:LEVel:IMMediate:LOW 2.990000e-01")
        self.assertEqual(commands[4], ":OUTPut1:STATe ON")


if __name__ == "__main__":
    unittest.main()
