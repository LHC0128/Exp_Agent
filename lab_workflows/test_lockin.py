"""HF2/MFLI 的无硬件回归：接口单位、记录边界、全局选择与安全拦截。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import yaml

from lockin_amplifier import (HF2Instrument, MFLIInstrument, LockinInstrument, DemodulatorConfig,
                             AuxOutConfig, DAQConfig, ScopeConfig, demod, auxout, daq,
                             acquire_scope, lockin_capabilities)
from lockin_amplifier import instrument as core
from .common import WorkflowCancelled
from .devices import create_lockin
from . import instrument_config, instrument_control, lockin_control
from .lockin_compatibility import lockin_preflight


class FakeServer:
    def __init__(self):
        self.nodes = {}
        self.writes = []
        self.module = Mock()
        self.module.progress.return_value = [1]
        self.module.getInt.return_value = 1

    def setInt(self, path, value):
        self.nodes[path] = value
        self.writes.append((path, value))

    setDouble = setInt

    def getInt(self, path):
        return self.nodes.get(path, 0)

    getDouble = getInt

    def sync(self):
        pass

    def disconnectDevice(self, device_id):
        self.disconnected_device = device_id

    def disconnect(self):
        self.disconnected = True

    def listNodes(self, path, flags):
        return []

    def dataAcquisitionModule(self):
        return self.module

    def scopeModule(self):
        return self.module


def fake_instrument(model="MFLI"):
    instr = MFLIInstrument(device_id="dev30661") if model == "MFLI" else HF2Instrument(device_id="dev18246")
    instr.capabilities = lockin_capabilities(model, ["MF-MD", "MF-DIG", "MF-F5M"])
    instr._daq = FakeServer()
    instr.daq.nodes[f"/{instr.device_id}/clockbase"] = 60e6 if model == "MFLI" else 210e6
    for idx in range(instr.capabilities["demodulators"]):
        instr.daq.nodes[f"{instr.demod_path(idx)}/harmonic"] = 1
        instr.daq.nodes[f"{instr.demod_path(idx)}/rate"] = 1000
    return instr


class CoreTests(unittest.TestCase):
    def test_model_imports_and_legacy_base(self):
        from lockin_amplifier.base import LockinInstrument as Base
        from lockin_amplifier.hf2 import HF2Instrument as HF2
        from lockin_amplifier.mfli import MFLIInstrument as MFLI

        self.assertIs(HF2Instrument, HF2)
        self.assertIs(MFLIInstrument, MFLI)
        self.assertIs(LockinInstrument, Base)
        self.assertIs(core.HF2Instrument, HF2)
        self.assertIs(core.MFLIInstrument, MFLI)
        self.assertIs(core.LockinInstrument, Base)
        self.assertIs(core.lockin_capabilities, lockin_capabilities)
        self.assertIsInstance(MFLI(device_id="dev30661"), Base)
        instr = core.LockinInstrument()
        self.assertEqual((instr.model, instr.port, instr.api_level, instr.interface), ("HF2", 8005, 1, "PCIe"))
        instr._daq = FakeServer()
        instr.daq.nodes[f"/{instr.device_id}/clockbase"] = 210e6
        instr.daq.nodes[f"{instr.sig_in_path(1)}/range"] = 2
        demod.configure_demodulator(instr, DemodulatorConfig(input_channel=1))
        self.assertEqual(instr.get_int(f"{instr.demod_path(0)}/adcselect"), 1)
        base = f"/{instr.device_id}/scopes/0"
        instr.daq.module.read.return_value = {f"{base}/wave": [[dict(wave=np.zeros((1, 2048)), dt=1e-6)]]}
        self.assertEqual(len(acquire_scope(instr, ScopeConfig(sources=["voltage1"]))[0].values), 2048)
        instr.daq.module.set.assert_any_call("externalscaling", 2)

    def test_capability_options_and_connection_guards(self):
        cases = [
            ("hf2", [], (6, 6, 2, 2, 50e6, 1, 2048, 210e6)),
            ("MFLI", [], (1, 1, 1, 1, 500e3, 1, 2048, 60e6)),
            ("mfli", ["mf-md", "F5M", "mf-dig"], (4, 4, 1, 1, 5e6, 2, 5_000_000, 60e6)),
        ]
        keys = ("demodulators", "oscillators", "voltage_inputs", "signal_outputs",
                "max_frequency_hz", "scope_channels", "scope_max_samples", "scope_max_rate")
        for model, options, expected in cases:
            with self.subTest(model=model, options=options):
                caps = lockin_capabilities(model, options)
                self.assertEqual(tuple(caps[key] for key in keys), expected)
                self.assertEqual(caps["model"], model.upper())
                self.assertEqual(caps["options"], sorted(options))
        with self.assertRaisesRegex(ValueError, "不支持的锁相放大器型号"):
            lockin_capabilities("UNKNOWN")
        with self.assertRaisesRegex(ValueError, "设备 ID"):
            MFLIInstrument()
        with self.assertRaisesRegex(ValueError, "API Level 6"):
            MFLIInstrument(device_id="dev30661", api_level=1)

    def test_clockbase_uses_model_node_type(self):
        for model, getter, other in (("HF2", "get_int", "get_double"), ("MFLI", "get_double", "get_int")):
            with self.subTest(model=model):
                instr = fake_instrument(model)
                with patch.object(instr, getter, return_value=123456) as read, patch.object(instr, other) as unused:
                    self.assertEqual(instr.clockbase, 123456.0)
                    read.assert_called_once_with(f"/{instr.device_id}/clockbase")
                    unused.assert_not_called()

    def test_factory_and_connection_failure(self):
        instr = create_lockin(dict(instrument="lockin_amplifier", model="MFLI", device_id="DEV30661",
                                   host="192.168.124.109"))
        self.assertIsInstance(instr, MFLIInstrument)
        self.assertEqual((instr.device_id, instr.port, instr.api_level, instr.interface), ("dev30661", 8004, 6, "1GbE"))
        server = Mock()
        server.connectDevice.side_effect = RuntimeError("设备连接失败")
        with patch.object(core._zi, "ziDAQServer", return_value=server):
            with self.assertRaisesRegex(RuntimeError, "设备连接失败"):
                instr.connect()
        self.assertFalse(instr.connected)
        server.disconnectDevice.assert_called_once_with("dev30661")
        server.disconnect.assert_called_once()

    def test_options_are_read_back_from_device(self):
        instr = MFLIInstrument(device_id="dev30661")
        server = Mock()
        server.getString.side_effect = ["MFLI", "MF-MD\nMF-DIG\nMF-F5M"]
        with patch.object(core._zi, "ziDAQServer", return_value=server):
            instr.connect()
        self.assertEqual(instr.capabilities["demodulators"], 4)
        self.assertEqual(instr.capabilities["max_frequency_hz"], 5e6)
        instr.disconnect()
        self.assertFalse(instr.connected)
        server.disconnect.assert_called_once()

    def test_mfli_input_one_is_not_silently_current(self):
        instr = fake_instrument()
        with self.assertRaisesRegex(ValueError, "不会替换接线"):
            demod.configure_demodulator(instr, DemodulatorConfig(input_channel=1))
        self.assertEqual(instr.daq.writes, [])
        demod.configure_demodulator(instr, DemodulatorConfig(input_source="current0"))
        self.assertEqual(instr.get_int(f"{instr.demod_path(0)}/adcselect"), 1)

    def test_output_volts_and_aux_route(self):
        for model, expected in (("HF2", 0.1), ("MFLI", 0.2)):
            with self.subTest(model=model):
                instr = fake_instrument(model)
                base = instr.sig_out_path(0)
                instr.daq.nodes[f"{base}/range"] = 2
                self.assertEqual(demod.set_output_amplitude(instr, 0, 0.2, 0), 0.2)
                self.assertEqual(instr.get_double(f"{base}/amplitudes/0"), expected)
                self.assertEqual(demod.set_output_offset(instr, 0, 0.2), 0.2)
                self.assertEqual(instr.get_double(f"{base}/offset"), expected)
                with patch.object(lockin_control, "load_safety_limits", return_value={}):
                    snapshot = lockin_control.read_outputs(instr, model.lower(), 0)["signal_outputs"][0]
                self.assertEqual(snapshot["amplitudes_peak_v"][0], 0.2)
                self.assertEqual(snapshot["offset_v"], 0.2)
                auxout.configure_aux_output(instr, AuxOutConfig(aux_index=1, demod_index=3, output_select=1))
                self.assertEqual(instr.get_int(f"{instr.aux_out_path(1)}/outputselect"), 1)
                self.assertEqual(instr.get_int(f"{instr.aux_out_path(1)}/demodselect"), 3)

    def test_frequency_with_new_harmonic_and_external_reference_rejection(self):
        instr = fake_instrument()
        lockin_control.apply_settings(instr, "mfli", 0, {"demod": {"frequency_hz": 1e6, "harmonic": 2}})
        self.assertEqual(instr.get_double(f"{instr.osc_path(0)}/freq"), 500000)
        self.assertEqual(instr.get_int(f"{instr.demod_path(0)}/harmonic"), 2)
        instr.daq.nodes["/dev30661/extrefs/0/enable"] = 1
        instr.daq.listNodes = lambda path, flags: ["/dev30661/extrefs/0/enable"] if "/extrefs/" in path else []
        before = list(instr.daq.writes)
        with self.assertRaisesRegex(ValueError, "外部解调参考"):
            demod.set_demod_frequency(instr, 0, 2e6, harmonic=3)
        self.assertEqual(instr.daq.writes, before)

    def test_daq_preserves_rows_bursts_and_integer_timestamps(self):
        instr = fake_instrument()
        origin = 2**60
        path = f"{instr.demod_path(0)}/sample.x"
        instr.daq.module.read.return_value = {path: [
            {"value": [[1, 2], [3, 4]], "timestamp": np.array([[origin, origin+60], [origin+120, origin+180]], dtype=np.uint64)},
            {"value": [[5, 6]], "timestamp": np.array([[origin+600, origin+660]], dtype=np.uint64)},
        ]}
        results = daq.acquire_data(instr, DAQConfig(grid_cols=2))
        np.testing.assert_equal(results[0].values, [1, 2, 3, 4, 5, 6])
        np.testing.assert_allclose(results[0].time, np.array([0, 60, 120, 180, 600, 660]) / 60e6)
        self.assertEqual(results[0].record_slices, [(0, 2), (2, 4), (4, 6)])
        self.assertEqual(results[0].config_snapshot["time_source"], "module_timestamp")
        instr.daq.module.clear.assert_called_once()
        with self.assertRaisesRegex(ValueError, "设备 ID"):
            daq.acquire_data(instr, DAQConfig(device="dev18246"))

    def test_scope_scaled_volts_time_and_restoration(self):
        for model in ("HF2", "MFLI"):
            with self.subTest(model=model):
                instr = fake_instrument(model)
                length = 2048 if model == "HF2" else 16
                base = f"/{instr.device_id}/scopes/0"
                instr.daq.nodes[f"{base}/enable"] = 1
                instr.daq.nodes[f"{instr.sig_in_path(0)}/range"] = 2
                instr.daq.module.read.return_value = {f"{base}/wave": [[dict(wave=np.full((1, length), 0.125),
                    dt=1e-6, timestamp=2**60+60, triggertimestamp=2**60, flags=8)]]}
                result = acquire_scope(instr, ScopeConfig(length=length))[0]
                np.testing.assert_equal(result.values, np.full(length, 0.125))
                np.testing.assert_allclose(result.time, np.arange(-length, 0)*1e-6+60/instr.clockbase)
                self.assertEqual(instr.get_int(f"{base}/enable"), 1)
                instr.daq.module.clear.assert_called_once()
                if model == "HF2":
                    instr.daq.module.set.assert_any_call("externalscaling", 2)
                    self.assertNotIn(f"{base}/length", dict(instr.daq.writes))

    def test_scope_model_sources_and_setting_restoration(self):
        for model, sources, length in (("HF2", ["voltage1"], 2048),
                                       ("MFLI", ["aux_input0", "aux_input1"], 16)):
            with self.subTest(model=model):
                instr = fake_instrument(model)
                base = f"/{instr.device_id}/scopes/0"
                original = {"time": 4, "enable": 1, "channel": 0}
                if model == "HF2":
                    original["trigchannel"] = -2
                    instr.daq.nodes[f"{instr.sig_in_path(1)}/range"] = 3
                else:
                    original.update(length=32, single=1, trigenable=1,
                                    **{"segments/enable": 1, "channels/0/inputselect": 0, "channels/1/inputselect": 0})
                instr.daq.nodes.update({f"{base}/{key}": value for key, value in original.items()})
                waves = np.full((len(sources), length), 0.125)
                instr.daq.module.read.return_value = {f"{base}/wave": [[dict(wave=waves, dt=1e-6)]]}
                results = acquire_scope(instr, ScopeConfig(sources=sources, length=length))
                self.assertEqual([result.signal_name for result in results], sources)
                for result in results:
                    np.testing.assert_equal(result.values, np.full(length, 0.125))
                self.assertEqual({key: instr.get_int(f"{base}/{key}") for key in original}, original)
                instr.daq.module.clear.assert_called_once()
                if model == "HF2":
                    self.assertIn((f"{base}/channel", 1), instr.daq.writes)
                    instr.daq.module.set.assert_any_call("externalscaling", 3)
                else:
                    self.assertIn((f"{base}/channel", 3), instr.daq.writes)
                    self.assertIn((f"{base}/channels/0/inputselect", 8), instr.daq.writes)
                    self.assertIn((f"{base}/channels/1/inputselect", 9), instr.daq.writes)
                    self.assertIn((f"{base}/segments/enable", 0), instr.daq.writes)
                    self.assertNotIn("externalscaling", [call.args[0] for call in instr.daq.module.set.call_args_list])
        instr = fake_instrument()
        instr.capabilities = lockin_capabilities("MFLI")
        with self.assertRaisesRegex(ValueError, "最多同时记录 1 路"):
            acquire_scope(instr, ScopeConfig(sources=["aux_input0", "aux_input1"], length=16))
        self.assertEqual(instr.daq.writes, [])

    def test_scope_error_flags_and_cancellation_restore(self):
        instr = fake_instrument()
        base = "/dev30661/scopes/0"
        instr.daq.nodes[f"{base}/enable"] = 1
        instr.daq.module.read.return_value = {f"{base}/wave": [[dict(wave=np.zeros((1, 16)), dt=1e-6, flags=1)]]}
        with self.assertRaisesRegex(RuntimeError, "丢样"):
            acquire_scope(instr, ScopeConfig(length=16))
        self.assertEqual(instr.get_int(f"{base}/enable"), 1)
        instr.daq.module.reset_mock()
        instr.daq.module.progress.return_value = [0]
        with self.assertRaises(WorkflowCancelled):
            acquire_scope(instr, ScopeConfig(length=16), cancellation_check=lambda: (_ for _ in ()).throw(WorkflowCancelled()))
        self.assertEqual(instr.get_int(f"{base}/enable"), 1)
        instr.daq.module.clear.assert_called_once()

    def test_outputs_require_limits_and_fail_closed(self):
        instr = fake_instrument()
        with patch.object(lockin_control, "load_safety_limits", return_value={}):
            with self.assertRaises(PermissionError):
                lockin_control.apply_settings(instr, "mfli", 0, {"signal_output": {"index": 0, "enabled": True}})
            self.assertEqual(instr.daq.writes, [])
            lockin_control.apply_settings(instr, "mfli", 0, {"signal_output": {"index": 0, "enabled": False}})
        instr.daq.nodes["/dev30661/sigouts/0/range"] = 1
        instr.daq.nodes["/dev30661/sigouts/0/on"] = 1
        with patch.object(lockin_control, "load_safety_limits", return_value={"mfli_signal_output_0": {"min": -0.5, "max": 0.5}}):
            with self.assertRaisesRegex(ValueError, "安全拦截"):
                lockin_control.apply_settings(instr, "mfli", 0, {"signal_output": {"index": 0, "amplitude_peak_v": 0.6, "mixer_enabled": True}})
            with patch.object(demod, "set_output_offset", side_effect=RuntimeError("写失败")):
                with self.assertRaisesRegex(RuntimeError, "写失败"):
                    lockin_control.apply_settings(instr, "mfli", 0, {"signal_output": {"index": 0, "offset_v": 0.1}})
        self.assertEqual(instr.get_int("/dev30661/sigouts/0/on"), 0)

    def test_signal_output_readback_failure_closes_output(self):
        instr = fake_instrument()
        server = instr.daq
        server.nodes["/dev30661/sigouts/0/range"] = 1
        device = Mock(instrument="lockin_amplifier", device_id="mfli")
        endpoint = Mock(kind="demod", index=0)
        with patch.object(instrument_control, "_resolve_control_target", return_value=({}, device, endpoint)), \
             patch.object(instrument_control, "_target_record"), \
             patch.object(instrument_control, "_connect", return_value=instr), \
             patch.object(instrument_control, "_read_hf2_state", side_effect=RuntimeError("回读失败")), \
             patch.object(lockin_control, "load_safety_limits", return_value={"mfli_signal_output_0": {"min": -0.5, "max": 0.5}}):
            with self.assertRaisesRegex(RuntimeError, "回读失败"):
                instrument_control.apply_control_target_lockin("lockin_r", "devices", "mapping", {"signal_output": {"index": 0, "enabled": True}})
        self.assertEqual(server.nodes["/dev30661/sigouts/0/on"], 0)
        self.assertTrue(server.disconnected)


class SelectionTests(unittest.TestCase):
    def test_global_selection_atomicity_and_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "params").mkdir()
            devices = {"schema_version": 1, "devices": {
                "hf2": {"instrument": "lockin_amplifier", "model": "HF2", "connection": {"device_id": "dev18246"}, "capabilities": {"channels": [0, 1, 2, 3, 4, 5]}},
                "mfli": {"instrument": "lockin_amplifier", "model": "MFLI", "connection": {"device_id": "dev30661"}, "capabilities": {"channels": [0, 1, 2, 3], "options": ["MF-MD", "MF-F5M"]}},
            }}
            mapping = {"schema_version": 2, "mapping": {key: {"instrument": "lockin_amplifier", "device_id": "hf2", "endpoint": {"kind": "demod", "index": idx}}
                       for key, idx in (("lockin_r", 0), ("lockin_xy", 3))}}
            for filename, data in (("devices.yaml", devices), ("mapping.yaml", mapping)):
                (root / "params" / filename).write_text(yaml.safe_dump(data), encoding="utf-8")
            library = instrument_config.public_device_library(root)
            original = instrument_config.public_physical_mappings(root)
            selected = instrument_config.select_lockin_device("mfli", device_library_revision=library["revision"], physical_mapping_revision=original["revision"], root=root)
            self.assertEqual({item["device_id"] for item in selected["mapping"].values()}, {"mfli"})
            self.assertEqual(selected["mapping"]["lockin_xy"]["endpoint"]["index"], 3)
            saved = (root / "params" / "mapping.yaml").read_bytes()
            with self.assertRaises(instrument_config.RevisionConflict):
                instrument_config.select_lockin_device("hf2", device_library_revision=library["revision"], physical_mapping_revision=original["revision"], root=root)
            self.assertEqual(saved, (root / "params" / "mapping.yaml").read_bytes())
            with patch.object(instrument_config, "_atomic_write", side_effect=OSError("替换失败")):
                with self.assertRaises(OSError):
                    instrument_config.select_lockin_device("hf2", device_library_revision=library["revision"], physical_mapping_revision=selected["revision"], root=root)
            self.assertEqual(saved, (root / "params" / "mapping.yaml").read_bytes())

    def test_compatibility_does_not_remap_or_lower_frequency(self):
        mapping = {"lockin_r": {"model": "MFLI", "capabilities": {"options": ["MF-MD", "MF-F5M"]}}}
        self.assertEqual(lockin_preflight("static-sensitivity", {"HF2_OSC_FREQ": 5e6}, mapping=mapping), [])
        self.assertTrue(lockin_preflight("rf-sensitivity-aw", {}, mapping=mapping))
        self.assertTrue(lockin_preflight("static-sensitivity", {"HF2_OSC_FREQ": 5e6+1}, mapping=mapping))
        self.assertTrue(lockin_preflight("static-sensitivity", {"HF2_DEMOD0_IDX": 4}, mapping=mapping))

    def test_complete_calibration_windows_and_plain_indices(self):
        mapping = {"lockin_r": {"model": "MFLI", "capabilities": {"options": ["MF-MD", "MF-F5M"]}}}
        cases = [
            ("bell-bloom-z-field-calibration", {"ZERO_BIAS_CENTER_FREQUENCY_HZ": 4.99e6, "Z_INITIAL_HZ_PER_V": 30000,
                "Z_BIAS_START_V": -0.2, "Z_BIAS_STOP_V": 0.2, "FREQUENCY_HALF_WIDTH_HZ": 10000}),
            ("mx-z-field-calibration", {"ZERO_BIAS_CENTER_FREQUENCY_HZ": 4.98e6, "Z_INITIAL_HZ_PER_V": 30000,
                "Z_BIAS_START_V": -1, "Z_BIAS_STOP_V": 0, "FREQUENCY_HALF_WIDTH_HZ": 10000,
                "Z_PREDICTION_POLARITY": "positive_decreases_frequency"}),
            ("mx-main-field-calibration", {"MAIN_FIELD_REFERENCE_FREQUENCY_HZ": 4.98e6, "MAIN_FIELD_REFERENCE_CURRENT_MA": 0,
                "MAIN_FIELD_START_MA": 0, "MAIN_FIELD_STOP_MA": 1, "MAIN_FIELD_INITIAL_HZ_PER_MA": 30000,
                "FREQUENCY_HALF_WIDTH_HZ": 10000}),
            ("mx-keithley-6221-main-field-calibration", {"KEITHLEY_REFERENCE_FREQUENCY_HZ": 4.98e6, "KEITHLEY_REFERENCE_CURRENT_MA": 0,
                "KEITHLEY_CURRENT_START_MA": 0, "KEITHLEY_CURRENT_STOP_MA": 1, "KEITHLEY_INITIAL_HZ_PER_MA": 30000,
                "FREQUENCY_HALF_WIDTH_HZ": 10000}),
        ]
        for experiment_id, values in cases:
            with self.subTest(experiment=experiment_id):
                self.assertTrue(any("完整扫频窗口" in error for error in lockin_preflight(experiment_id, values, mapping=mapping)))
        cases[0][1]["ZERO_BIAS_CENTER_FREQUENCY_HZ"] = 4.984e6
        self.assertEqual(lockin_preflight(*cases[0], mapping=mapping), [])
        self.assertTrue(lockin_preflight("mx-y-rf-sensitivity", {"DEMOD_IDX": 4}, mapping=mapping))
        self.assertTrue(lockin_preflight("mx-y-rf-sensitivity", {"DEMOD_OSC_IDX": 4}, mapping=mapping))
        self.assertTrue(lockin_preflight("mx-z-optimal-control-xyz-balance", {"DEMOD_FREQUENCY_HZ": 5e6 + 1}, mapping=mapping))
        self.assertEqual(lockin_preflight("z-coil-current-frequency-response", {"FREQUENCY_START_HZ": 6e6}, mapping=mapping), [])
        coupling = {**cases[1][1], "SENSE_RESISTOR_OHM": 10}
        self.assertTrue(any("完整扫频窗口" in error for error in lockin_preflight("mx-z-current-coupling-calibration", coupling, mapping=mapping)))


if __name__ == "__main__":
    unittest.main()
