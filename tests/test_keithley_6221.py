import math
import unittest

from keithley_6221 import Keithley6221Instrument


class FakeInstrument:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.writes = []

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        response = self.responses[command]
        if isinstance(response, list):
            return response.pop(0)
        return response


class Keithley6221DriverTests(unittest.TestCase):
    def make_device(self, responses=None):
        device = Keithley6221Instrument("FAKE")
        fake = FakeInstrument(responses)
        device._inst = fake
        return device, fake

    def test_dc_setters_emit_documented_commands(self):
        device, fake = self.make_device()

        device.set_current(-0.00125)
        device.set_autorange(False)
        device.set_current_range(0.002)
        device.set_compliance(12.5)
        device.set_analog_filter(True)
        device.set_output_response("slow")
        device.set_output(True)
        device.clear_source()

        self.assertEqual(fake.writes, [
            "CURR -0.00125",
            "CURR:RANG:AUTO OFF",
            "CURR:RANG 0.002",
            "CURR:COMP 12.5",
            "CURR:FILT ON",
            "OUTP:RESP SLOW",
            "OUTP ON",
            "SOUR:CLE",
        ])

    def test_dc_queries_parse_values(self):
        device, _ = self.make_device({
            "*IDN?": "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1,1",
            "CURR?": "-1.25E-3",
            "CURR:RANG?": "2E-3",
            "CURR:RANG:AUTO?": "ON",
            "CURR:COMP?": "12.5",
            "CURR:FILT?": "0",
            "OUTP:RESP?": '"FAST"',
            "OUTP?": "1",
        })

        self.assertIn("6221", device.idn())
        self.assertAlmostEqual(device.get_current(), -0.00125)
        self.assertAlmostEqual(device.get_current_range(), 0.002)
        self.assertTrue(device.get_autorange())
        self.assertEqual(device.get_compliance(), 12.5)
        self.assertFalse(device.get_analog_filter())
        self.assertEqual(device.get_output_response(), "FAST")
        self.assertTrue(device.get_output())

    def test_dc_boundaries_and_non_finite_values_are_rejected(self):
        device, fake = self.make_device()
        device.set_current(-0.105)
        device.set_current(0.105)
        device.set_compliance(0.1)
        device.set_compliance(105)
        self.assertEqual(len(fake.writes), 4)

        for value in (-0.1050001, 0.1050001, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    device.set_current(value)
        for value in (0.099, 105.001):
            with self.assertRaises(ValueError):
                device.set_compliance(value)

    def test_waveform_setters_and_queries(self):
        device, fake = self.make_device({
            "SOUR:WAVE:FUNC?": "ARB0",
            "SOUR:WAVE:FREQ?": "1000",
            "SOUR:WAVE:AMPL?": "2E-3",
            "SOUR:WAVE:OFFS?": "-5E-4",
            "SOUR:WAVE:DCYC?": "25",
            "SOUR:WAVE:RANG?": "BEST",
            "SOUR:WAVE:DUR:TIME?": "INF",
            "SOUR:WAVE:DUR:CYCL?": "5",
            "SOUR:WAVE:ARB:POIN?": "3",
        })

        device.set_waveform_function("ARB")
        device.set_waveform_frequency(1000)
        device.set_waveform_amplitude(0.002)
        device.set_waveform_offset(-0.0005)
        device.set_waveform_duty_cycle(25)
        device.set_waveform_ranging("best")
        self.assertEqual(device.get_waveform_function(), "ARB")
        self.assertEqual(device.get_waveform_frequency(), 1000)
        self.assertEqual(device.get_waveform_amplitude(), 0.002)
        self.assertEqual(device.get_waveform_offset(), -0.0005)
        self.assertEqual(device.get_waveform_duty_cycle(), 25)
        self.assertEqual(device.get_waveform_ranging(), "BEST")
        self.assertEqual(device.get_waveform_duration_time(), "INF")
        self.assertEqual(device.get_waveform_duration_cycles(), 5)
        self.assertEqual(device.get_arbitrary_point_count(), 3)
        self.assertEqual(fake.writes, [
            "SOUR:WAVE:FUNC ARB0",
            "SOUR:WAVE:FREQ 1000",
            "SOUR:WAVE:AMPL 0.002",
            "SOUR:WAVE:OFFS -0.0005",
            "SOUR:WAVE:DCYC 25",
            "SOUR:WAVE:RANG BEST",
        ])

    def test_waveform_limits_are_enforced(self):
        device, fake = self.make_device()
        device.set_waveform_amplitude(2e-12)
        device.set_waveform_amplitude(0.105)
        device.set_waveform_frequency(1e-3)
        device.set_waveform_frequency(1e5)
        device.set_waveform_duty_cycle(0)
        device.set_waveform_duty_cycle(100)
        self.assertEqual(len(fake.writes), 6)

        for setter, value in (
            (device.set_waveform_amplitude, 1e-12),
            (device.set_waveform_amplitude, 0.106),
            (device.set_waveform_frequency, 0.0009),
            (device.set_waveform_frequency, 100001),
            (device.set_waveform_duty_cycle, -0.1),
            (device.set_waveform_duty_cycle, 100.1),
        ):
            with self.assertRaises(ValueError):
                setter(value)

    def test_duration_modes_set_unused_limit_to_infinite(self):
        device, fake = self.make_device()
        device.set_waveform_duration("TIME", 0.25)
        device.set_waveform_duration("CYCLES", 12.5)
        device.set_waveform_duration("INFINITE")
        self.assertEqual(fake.writes, [
            "SOUR:WAVE:DUR:CYCL INF",
            "SOUR:WAVE:DUR:TIME 0.25",
            "SOUR:WAVE:DUR:TIME INF",
            "SOUR:WAVE:DUR:CYCL 12.5",
            "SOUR:WAVE:DUR:TIME INF",
            "SOUR:WAVE:DUR:CYCL INF",
        ])

    def test_arbitrary_upload_batches_at_one_hundred_points(self):
        device, fake = self.make_device()
        points = [-1.0] + [0.0] * 199 + [1.0]
        self.assertEqual(device.upload_arbitrary(points), 201)
        self.assertEqual(len(fake.writes), 3)
        self.assertTrue(fake.writes[0].startswith("SOUR:WAVE:ARB:DATA -1,0"))
        self.assertTrue(fake.writes[1].startswith("SOUR:WAVE:ARB:APPEND 0"))
        self.assertEqual(fake.writes[2], "SOUR:WAVE:ARB:APPEND 1")
        for command in fake.writes:
            self.assertLessEqual(command.count(",") + 1, 100)

    def test_arbitrary_upload_rejects_invalid_points(self):
        device, fake = self.make_device()
        for points in ([0.0], [0.0] * 65536, [0.0, 1.1], [0.0, math.nan]):
            with self.assertRaises(ValueError):
                device.upload_arbitrary(points)
        self.assertEqual(fake.writes, [])

    def test_waveform_lifecycle_and_error_queue(self):
        device, fake = self.make_device({
            "*OPC?": "1",
            "SYST:ERR?": ["-222,Data out of range", '0,"No error"'],
        })
        device.abort_waveform()
        device.arm_waveform()
        device.start_waveform()
        device.wait_for_operation_complete()
        with self.assertRaisesRegex(RuntimeError, "Data out of range"):
            device.raise_for_errors()
        self.assertEqual(fake.writes, [
            "SOUR:WAVE:ABOR", "SOUR:WAVE:ARM", "SOUR:WAVE:INIT"
        ])


if __name__ == "__main__":
    unittest.main()
