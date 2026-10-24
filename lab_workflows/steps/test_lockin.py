"""公共锁相输入策略的无硬件回归。"""

import unittest
from unittest.mock import patch

from lockin_amplifier import SignalInputConfig, demod

from lab_workflows.test_lockin import fake_instrument
from .lockin import initialize_lockin_inputs


class LockinInputTests(unittest.TestCase):
    def test_initialization_closes_all_voltage_inputs_and_preserves_other_settings(self):
        for model in ("HF2", "MFLI"):
            with self.subTest(model=model):
                instrument = fake_instrument(model)
                expected = {}
                for index in range(instrument.capabilities["voltage_inputs"]):
                    path = instrument.sig_in_path(index)
                    expected.update({f"{path}/ac": 0, f"{path}/imp50": 0,
                                     f"{path}/range": 2.0, f"{path}/diff": 1})
                    instrument.daq.nodes.update({f"{path}/ac": 1, f"{path}/imp50": 1,
                                                 f"{path}/range": 2.0, f"{path}/diff": 1})
                initialize_lockin_inputs(instrument)
                self.assertEqual({key: instrument.daq.nodes[key] for key in expected}, expected)
                demod.configure_signal_input(instrument, SignalInputConfig(range=3.0))
                self.assertEqual(instrument.get_int(f"{instrument.sig_in_path(0)}/ac"), 0)
                self.assertEqual(instrument.get_int(f"{instrument.sig_in_path(0)}/imp50"), 0)

    def test_initialization_does_not_hide_communication_failure(self):
        instrument = fake_instrument("HF2")
        with patch.object(instrument, "set_int", side_effect=RuntimeError("输入设置失败")):
            with self.assertRaisesRegex(RuntimeError, "输入设置失败"):
                initialize_lockin_inputs(instrument)

    def test_old_typed_input_parameters_cannot_enable_modes(self):
        from lab_workflows.experiment_modules.static_sensitivity.models import StaticSensitivityParams
        from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.models import RFSensitivityDirectAWParams
        from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.models import RFSensitivityDirectAWRParams
        from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency.models import RFDirectAWFrequencyParams
        from lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.models import XYDirectAWDCCalibrationParams
        from lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.models import NoiseSpectrumXYDemod3RParams

        cases = (
            (StaticSensitivityParams, "hf2_ac_coupling", "hf2_50_ohm", True, False),
            (RFSensitivityDirectAWParams, "LOOPBACK_INPUT_AC", "LOOPBACK_INPUT_IMPEDANCE_OHM", 50, 1_000_000),
            (RFSensitivityDirectAWRParams, "LOOPBACK_INPUT_AC", "LOOPBACK_INPUT_IMPEDANCE_OHM", 50, 1_000_000),
            (RFDirectAWFrequencyParams, "SIGIN2_AC_COUPLING", "SIGIN2_IMPEDANCE", 50, 1_000_000),
            (XYDirectAWDCCalibrationParams, "SIGIN2_AC_COUPLING", "SIGIN2_IMPEDANCE_OHM", 50, 1_000_000),
            (NoiseSpectrumXYDemod3RParams, "SIGIN2_AC_COUPLING", "SIGIN2_IMPEDANCE_OHM", 50, 1_000_000),
        )
        for model, ac, impedance, old_value, expected in cases:
            with self.subTest(model=model.__name__):
                params = model.from_external({ac: True, impedance: old_value}, schema_version=model.schema_version)
                values = params.to_external()
                self.assertFalse(values[ac])
                self.assertEqual(values[impedance], expected)
                visible = {field["name"] for field in params.schema()["fields"]}
                self.assertFalse({ac, impedance} & visible)

    def test_old_script_overrides_cannot_enable_modes(self):
        from lab_workflows.experiments import get_experiment

        adapter = get_experiment("rf-sensitivity-aw-frequency").schema_provider.__self__
        defaults = adapter.defaults()
        values = adapter._nested_overrides({"SIGIN2_AC_COUPLING": True, "SIGIN2_IMPEDANCE": 50}, defaults)
        self.assertFalse(values["SIGIN2_AC_COUPLING"])
        self.assertEqual(values["SIGIN2_IMPEDANCE"], 1_000_000)
        visible = {field["name"] for field in adapter.schema()["fields"]}
        self.assertFalse({"SIGIN2_AC_COUPLING", "SIGIN2_IMPEDANCE"} & visible)
