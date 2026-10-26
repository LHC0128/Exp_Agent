"""公共锁相输入策略的无硬件回归。"""

import unittest
from unittest.mock import Mock, call, patch

from lockin_amplifier import SignalInputConfig, demod

from lab_workflows.test_lockin import fake_instrument
from .lockin import initialize_lockin_inputs, verify_lockin_acquisition_mode


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


class LockinAcquisitionModeTests(unittest.TestCase):
    def test_daq_three_fields_tolerance_and_error_contract(self):
        base = "/dev-test/demods/2"
        # 仅开放原有只读操作；任何写入、同步或 get_int 都会直接失败。
        hfi = Mock(spec_set=["model", "demod_path", "get_double"])
        hfi.model = "HF2"
        hfi.demod_path.return_value = base
        arguments = dict(demod_index=2, requested_rate_hz=50000.0,
                         acquisition_rate_hz=48000.0, time_constant_s=1e-4, phase_deg=23.0)
        expected = {f"{base}/rate": 48000.0, f"{base}/timeconstant": 1e-4,
                    f"{base}/phaseshift": 23.0}
        read_calls = [call.demod_path(2), *[call.get_double(path) for path in expected]]
        within = {path: value + 0.5 * max(abs(value) * 1e-6, 1e-9)
                  for path, value in expected.items()}
        for label, values in (("exact", expected), ("within", within)):
            with self.subTest(values=label):
                hfi.reset_mock()
                hfi.get_double.side_effect = [str(values[path]) for path in expected]
                self.assertIsNone(verify_lockin_acquisition_mode(hfi, **arguments))
                self.assertEqual(hfi.method_calls, read_calls)
        for index, (path, value) in enumerate(expected.items()):
            with self.subTest(outside=path):
                values = dict(expected)
                values[path] = value + 2 * max(abs(value) * 1e-6, 1e-9)
                hfi.reset_mock()
                hfi.get_double.side_effect = [str(values[node]) for node in expected]
                with self.assertRaises(RuntimeError) as caught:
                    verify_lockin_acquisition_mode(hfi, **arguments)
                self.assertEqual(str(caught.exception), "解调器实际模式已改变，请结束会话并重新准备")
                self.assertEqual(hfi.method_calls, read_calls[:index + 2])
            with self.subTest(communication=path):
                error = ConnectionError("锁相回读通信失败")
                hfi.reset_mock()
                hfi.get_double.side_effect = [str(value) for value in list(expected.values())[:index]] + [error]
                with self.assertRaises(ConnectionError) as caught:
                    verify_lockin_acquisition_mode(hfi, **arguments)
                self.assertIs(caught.exception, error)
                self.assertEqual(hfi.method_calls, read_calls[:index + 2])

    def test_scope_skips_rate_and_keeps_filter_phase_checks(self):
        base = "/dev-test/demods/2"
        hfi = Mock(spec_set=["model", "acquisition_mode", "demod_path", "get_double"])
        hfi.model, hfi.acquisition_mode = "MFLI", "auto"
        hfi.demod_path.return_value = base
        arguments = dict(demod_index=2, requested_rate_hz=200000.0,
                         acquisition_rate_hz=234375.0, time_constant_s=1e-4, phase_deg=23.0)
        expected = {f"{base}/timeconstant": 1e-4, f"{base}/phaseshift": 23.0}
        values = dict(expected)

        def read_node(path):
            if path == f"{base}/rate":
                raise AssertionError("Scope 禁止读取通信 rate")
            return str(values[path])

        hfi.get_double.side_effect = read_node
        read_calls = [call.demod_path(2), *[call.get_double(path) for path in expected]]
        self.assertIsNone(verify_lockin_acquisition_mode(hfi, **arguments))
        self.assertEqual(hfi.method_calls, read_calls)
        for index, (path, value) in enumerate(expected.items()):
            with self.subTest(changed=path):
                values = dict(expected)
                values[path] = value + 2 * max(abs(value) * 1e-6, 1e-9)
                hfi.reset_mock()
                with self.assertRaises(RuntimeError) as caught:
                    verify_lockin_acquisition_mode(hfi, **arguments)
                self.assertEqual(str(caught.exception), "解调器实际模式已改变，请结束会话并重新准备")
                self.assertEqual(hfi.method_calls, read_calls[:index + 2])
