import unittest

from signal_generator import DG4000Instrument, DG900Instrument


class FakeInstrument:
    def __init__(self, responses):
        self.responses = responses
        self.writes = []

    def query(self, command):
        return self.responses[command]

    def write(self, command):
        self.writes.append(command)


class SequencedFakeInstrument(FakeInstrument):
    def query(self, command):
        response = self.responses[command]
        if isinstance(response, list):
            return response.pop(0)
        return response


class DriverGetterTests(unittest.TestCase):
    def test_dg900_dc_output_and_clock_queries(self):
        device = DG900Instrument("FAKE", channel=2)
        device._inst = FakeInstrument(
            {
                ":SOURce2:APPLy?": '"DC,DEF,DEF,+3.750000E-01,DEF"',
                ":OUTPut2:STATe?": "ON",
                ":SYSTem:ROSCillator:SOURce?": "EXT",
            }
        )
        self.assertAlmostEqual(device.get_dc_voltage(), 0.375)
        self.assertTrue(device.get_output())
        self.assertEqual(device.get_ref_clock_source(), "EXT")

    def test_dg900_apply_response_parses_scientific_values_and_def(self):
        device = DG900Instrument("FAKE", channel=1)
        device._inst = FakeInstrument({
            ":SOURce1:APPLy?": (
                '"SIN,+1.000000E+03,+2.500000E+00,-5.000000E-01,'
                '+9.000000E+01"'
            ),
        })
        state = device.get_wave_parameters()
        self.assertEqual(state["shape"], "SINusoid")
        self.assertEqual(state["frequency"], 1000.0)
        self.assertEqual(state["amplitude"], 2.5)
        self.assertEqual(state["offset"], -0.5)
        self.assertEqual(state["phase"], 90.0)

    def test_dg900_mod_and_burst_queries_use_official_hierarchies(self):
        device = DG900Instrument("FAKE", channel=2)
        device._inst = FakeInstrument({
            ":SOURce2:AM:STATe?": "0",
            ":SOURce2:FM:STATe?": "1",
            ":SOURce2:FM:SOURce?": "EXT",
            ":SOURce2:FM:DEViation?": "+2.500000E+03",
            ":SOURce2:BURSt:MODE?": "TRIG",
            ":SOURce2:BURSt:NCYCles?": "+5.000000E+02",
            ":TRIGger2:SOURce?": "EXT",
            ":TRIGger2:SLOPe?": "NEG",
        })
        self.assertEqual(device.get_active_mod_type(), "FM")
        self.assertEqual(device.get_mod_source("FM"), "EXTernal")
        self.assertEqual(device.get_mod_fm_deviation(), 2500.0)
        self.assertEqual(device.get_burst_mode(), "TRIGgered")
        self.assertEqual(device.get_burst_ncycles(), 500)
        self.assertEqual(device.get_burst_trigger_source(), "EXTernal")
        self.assertEqual(device.get_burst_trigger_slope(), "NEGative")

    def test_dg900_burst_setters_use_trigger_subsystem(self):
        device = DG900Instrument("FAKE", channel=1)
        fake = FakeInstrument({})
        device._inst = fake
        device.set_burst_delay(0.25)
        device.set_burst_trigger_source("EXTernal")
        device.set_burst_trigger_slope("POSitive")
        self.assertEqual(fake.writes, [
            ":TRIGger1:DELay 2.500000e-01",
            ":TRIGger1:SOURce EXTernal",
            ":TRIGger1:SLOPe POSitive",
        ])

    def test_dg900_disable_all_mod_only_writes_enabled_types(self):
        device = DG900Instrument("FAKE", channel=1)
        fake = FakeInstrument({
            ":SOURce1:AM:STATe?": "0",
            ":SOURce1:FM:STATe?": "1",
            ":SOURce1:PM:STATe?": "0",
            ":SOURce1:FSKey:STATe?": "0",
            ":SOURce1:PWM:STATe?": "0",
        })
        device._inst = fake

        device.disable_all_mod()

        self.assertEqual(fake.writes, [":SOURce1:FM:STATe OFF"])

    def test_dg900_transaction_helpers_wait_and_surface_errors(self):
        device = DG900Instrument("FAKE", channel=1)
        fake = SequencedFakeInstrument({
            "*OPC?": "1",
            ":SYSTem:ERRor?": ["-222,Data out of range", '0,"No error"'],
        })
        device._inst = fake

        device.clear_status()
        device.wait_for_operation_complete()
        with self.assertRaisesRegex(RuntimeError, "Data out of range"):
            device.raise_for_errors()

        self.assertEqual(fake.writes, ["*CLS"])

    def test_dg4000_mod_and_burst_getters_preserve_command_tree(self):
        device = DG4000Instrument("FAKE", channel=1)
        device._inst = FakeInstrument({
            ":SOURce1:MOD:TYPE?": "FSK",
            ":SOURce1:MOD:STATe?": "1",
            ":SOURce1:MOD:FSKey:FREQuency?": "+5.000000E+03",
            ":SOURce1:BURSt:STATe?": "ON",
            ":SOURce1:BURSt:MODE?": "GAT",
            ":SOURce1:BURSt:TRIGger:SOURce?": "EXT",
        })
        self.assertEqual(device.get_mod_type(), "FSKey")
        self.assertTrue(device.get_mod_state())
        self.assertEqual(device.get_mod_fsk_frequency(), 5000.0)
        self.assertTrue(device.get_burst_state())
        self.assertEqual(device.get_burst_mode(), "GATed")
        self.assertEqual(device.get_burst_trigger_source(), "EXTernal")


if __name__ == "__main__":
    unittest.main()
