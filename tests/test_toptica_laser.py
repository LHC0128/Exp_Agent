import unittest
from unittest.mock import MagicMock

from toptica_laser import DLCProInstrument


class FakeClient:
    def __init__(self, values):
        self.values = dict(values)
        self.calls = []
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def get(self, parameter):
        self.calls.append(("get", parameter))
        return self.values[parameter]

    def set(self, parameter, value):
        self.calls.append(("set", parameter, value))
        self.values[parameter] = value
        if parameter == "emission-button-enabled":
            self.values["emission"] = value
        return 0


class DLCProInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            "serial-number": "DLC PRO_53043",
            "laser1:dl:cc:current-clip": 392.0,
            "laser1:dl:cc:current-set": 201.0,
            "laser1:dl:tc:temp-set": 20.3,
            "laser1:scan:offset": 80.0,
            "laser1:scan:amplitude": 34.0,
            "laser1:scan:enabled": False,
            "laser1:scan:frequency": 11.0,
            "system-health": 0,
            "laser1:health": 0,
            "interlock-open": False,
            "laser1:enabled": True,
            "emission": False,
        }
        self.client = FakeClient(self.values)
        self.connection_factory = MagicMock(return_value=object())
        self.instrument = DLCProInstrument(
            "192.168.124.68",
            connection_factory=self.connection_factory,
            client_factory=lambda connection: self.client,
        )
        self.instrument.connect()

    def tearDown(self):
        self.instrument.disconnect()

    def test_connection_uses_configured_sdk_ports(self):
        self.connection_factory.assert_called_once_with(
            "192.168.124.68",
            command_line_port=1998,
            monitoring_line_port=1999,
            timeout=5.0,
        )
        self.assertTrue(self.client.opened)

    def test_current_uses_exact_parameter_path_and_live_clip(self):
        result = self.instrument.set_laser_current_ma(385.0)
        self.assertEqual(result, 385.0)
        self.assertEqual(
            self.client.calls[-3:],
            [
                ("get", "laser1:dl:cc:current-clip"),
                ("set", "laser1:dl:cc:current-set", 385.0),
                ("get", "laser1:dl:cc:current-set"),
            ],
        )

    def test_current_above_live_clip_writes_nothing(self):
        self.client.values["laser1:dl:cc:current-clip"] = 380.0
        with self.assertRaisesRegex(ValueError, "current-clip"):
            self.instrument.set_laser_current_ma(385.0)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_non_finite_temperature_writes_nothing(self):
        with self.assertRaisesRegex(ValueError, "有限数值"):
            self.instrument.set_laser_temperature_c(float("nan"))
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_temperature_range_writes_nothing(self):
        with self.assertRaisesRegex(ValueError, "安全范围"):
            self.instrument.set_laser_temperature_c(21.1)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_pzt_maps_to_scan_offset(self):
        result = self.instrument.set_pzt_voltage_v(81.0)
        self.assertEqual(result, 81.0)
        self.assertIn(
            ("set", "laser1:scan:offset", 81.0),
            self.client.calls,
        )

    def test_pzt_envelope_rejected_before_write(self):
        self.client.values["laser1:scan:amplitude"] = 30.0
        with self.assertRaisesRegex(ValueError, "扫描包络越界"):
            self.instrument.set_pzt_voltage_v(10.0)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_pzt_absolute_upper_limit_rejected_before_write(self):
        with self.assertRaisesRegex(ValueError, "安全范围"):
            self.instrument.set_pzt_voltage_v(140.1)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_scan_amplitude_upper_limit_rejected_before_write(self):
        with self.assertRaisesRegex(ValueError, "安全范围"):
            self.instrument.set_scan_amplitude_vpp(50.1)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

    def test_scan_frequency_is_read_only(self):
        self.assertEqual(self.instrument.get_scan_frequency_hz(), 11.0)
        self.assertFalse(hasattr(self.instrument, "set_scan_frequency_hz"))

    def test_setpoint_readback_mismatch_is_rejected(self):
        def ignore_write(parameter, value):
            self.client.calls.append(("set", parameter, value))
            return 0

        self.client.set = ignore_write
        with self.assertRaisesRegex(RuntimeError, "回读超差"):
            self.instrument.set_laser_current_ma(202.0)

    def test_emission_on_requires_gate_and_preconditions(self):
        with self.assertRaisesRegex(PermissionError, "门禁"):
            self.instrument.set_emission(True)
        self.assertFalse(any(call[0] == "set" for call in self.client.calls))

        result = self.instrument.set_emission(
            True,
            remote_enable_allowed=True,
        )
        self.assertTrue(result)
        self.assertIn(
            ("set", "emission-button-enabled", True),
            self.client.calls,
        )

    def test_emission_off_does_not_require_gate(self):
        self.client.values["emission"] = True
        result = self.instrument.set_emission(False)
        self.assertFalse(result)
        self.assertIn(
            ("set", "emission-button-enabled", False),
            self.client.calls,
        )


if __name__ == "__main__":
    unittest.main()
