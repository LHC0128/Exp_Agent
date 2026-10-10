"""共同接口和 DG912 Pro 文件传输的无硬件回归。"""

import base64
import inspect
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import numpy as np
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

from signal_generator import DG4000Instrument, DG900Instrument, SignalGeneratorConfig, SignalGeneratorProtocol


IDENTITY = "RIGOL,DG912 Pro,DG9Q123,1.0"
BMP = b"BM" + struct.pack("<IHHI", 58, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0) + b"\0\0\0\0"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1kAAAAASUVORK5CYII=")


class FakeVisa:
    def __init__(self):
        self.timeout = 1234
        self.read_termination = "\r\n"
        self.write_termination = "\r\n"
        self.writes = []
        self.queries = []
        self.catalog = '0,1000000'
        self.raw_chunks = []
        self.fail_segment = None
        self.segment_count = 0
        self.identity = IDENTITY
        self.responses = {}
        self.loads = {}
        self.closed = False
        self.cleared = 0

    def write(self, command):
        self.writes.append(command)
        if command.startswith(":OUTPut") and ":LOAD " in command:
            header, value = command.split()
            self.loads[header + "?"] = "9.9E+37" if value.upper().startswith("INF") else value
        if ":DATA:DAC " in command:
            self.segment_count += 1
            if self.segment_count == self.fail_segment:
                raise VisaIOError(StatusCode.error_timeout)

    def query(self, command):
        self.queries.append(command)
        if command in self.responses:
            response = self.responses[command]
            if isinstance(response, Exception):
                raise response
            return response
        if command == "*IDN?":
            return self.identity
        if command in self.loads:
            return self.loads[command]
        if command == "*OPC?":
            return "+1"
        if command == ":SYSTem:ERRor?":
            return '0,"No error"'
        if ":CATalog:" in command:
            return self.catalog
        if command == ":COUNter:MEASure?":
            return "1.0E3,1E-3,50,5E-4,5E-4\n"
        if ":MOD:TYPe?" in command:
            return "FM"
        if ":SOURce?" in command:
            return "EXT"
        return "0"

    def read_raw(self):
        if not self.raw_chunks:
            return b""
        chunk = self.raw_chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def read(self):
        raise VisaIOError(StatusCode.error_timeout)

    def clear(self):
        self.cleared += 1

    def close(self):
        self.closed = True


def driver_with_fake(cls=DG900Instrument):
    driver = cls("fake", channel=2)
    driver._inst = FakeVisa()
    return driver


class CommonInterfaceTests(unittest.TestCase):
    def test_model_modules_preserve_legacy_imports_and_shared_types(self):
        from signal_generator import dg4000, dg900, instrument, types

        self.assertIs(DG4000Instrument, dg4000.DG4000Instrument)
        self.assertIs(DG4000Instrument, instrument.DG4000Instrument)
        self.assertIs(DG900Instrument, dg900.DG900Instrument)
        self.assertEqual(DG4000Instrument.__module__, "signal_generator.dg4000")
        self.assertEqual(DG900Instrument.__module__, "signal_generator.dg900")
        self.assertEqual(instrument.DEFAULT_TIMEOUT, dg4000.DEFAULT_TIMEOUT)
        for name in ("WaveShape", "ModType", "SweepSpacing", "BurstMode",
                     "PulseTransition", "VoltageUnit", "OutputPolarity", "SyncPolarity"):
            with self.subTest(parameter_type=name):
                self.assertIs(getattr(instrument, name), getattr(types, name))
                self.assertIs(getattr(dg4000, name), getattr(types, name))
        for name in ("BurstMode", "ModType", "OutputPolarity", "VoltageUnit", "WaveShape"):
            self.assertIs(getattr(dg900, name), getattr(types, name))

    def test_all_existing_common_parameter_names_defaults_and_order_match(self):
        common = set(vars(DG900Instrument)) & set(vars(DG4000Instrument))
        for name in sorted(common):
            if not name.startswith("_") and callable(getattr(DG4000Instrument, name)):
                with self.subTest(method=name):
                    self.assertEqual(inspect.signature(getattr(DG4000Instrument, name)).parameters,
                                     inspect.signature(getattr(DG900Instrument, name)).parameters)
        for cls in (DG4000Instrument, DG900Instrument):
            self.assertIsInstance(cls("fake"), SignalGeneratorProtocol)
        for name in ("upload_arb_wave", "config_arb_wave", "list_files", "delete_file", "set_mod_am_dssc"):
            self.assertFalse(hasattr(DG4000Instrument, name))
            self.assertFalse(hasattr(SignalGeneratorProtocol, name))

    def test_apply_and_convenience_methods_preserve_output_contract(self):
        for cls in (DG4000Instrument, DG900Instrument):
            driver = driver_with_fake(cls)
            with self.subTest(model=cls.__name__):
                self.assertIsNone(driver.apply_wave("SINusoid", channel=2, freq=100, amp=1, offset=0, phase=90))
                self.assertEqual(driver._inst.writes, [":SOURce2:APPLy:SINusoid 100,1,0,90"])
                for method, kwargs in (("setup_sine", {}), ("setup_square", {}), ("setup_ramp", {}), ("setup_pulse", {})):
                    driver._inst.writes.clear()
                    self.assertIsNone(getattr(driver, method)(100, 1, **kwargs))
                    self.assertEqual(driver._inst.writes[-1], ":OUTPut2:STATe ON")
                driver._inst.writes.clear()
                driver.setup_noise(1, 0.2)
                expected = ":SOURce2:APPLy:NOISe " + ("1,0.2" if cls is DG4000Instrument else "DEF,1,0.2")
                self.assertEqual(driver._inst.writes, [expected, ":OUTPut2:STATe ON"])

    def test_pro_aliases_and_unsupported_apply_parameters(self):
        driver = driver_with_fake()
        driver.apply_wave("CUSTom", freq=100, amp=1, offset=0, phase=0)
        self.assertIn(":APPLy:ARBitrary ", driver._inst.writes[-1])
        for kwargs in ({"delay": 0}, {"frequency": 100}, {"amplitude": 1}, {"phase": 90}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                driver.apply_wave("PULSe", **kwargs)
        with self.assertRaises(ValueError):
            driver.apply_wave("HARMonic")

    def test_modulation_source_uses_selected_or_active_type_and_native_scpi(self):
        driver = driver_with_fake()
        driver._inst.responses[":SOURce2:AM:STATe?"] = "1"
        driver.set_mod_type("FM")
        driver.set_mod_source("EXTernal")
        self.assertEqual(driver._inst.writes[-1], ":SOURce2:FM:SOURce EXTernal")
        self.assertEqual(driver.get_mod_source(), "EXTernal")
        self.assertIn(":SOURce2:AM:SOURce?", driver._inst.queries)
        other = driver_with_fake(DG4000Instrument)
        other.set_mod_source("EXTernal")
        self.assertEqual(other._inst.writes[-1], ":SOURce2:MOD:FM:SOURce EXTernal")
        driver.set_mod_am_dssc(True)
        self.assertEqual(driver._inst.writes[-1], ":SOURce2:AM:DSSC ON")

    def test_shared_config_applies_to_pro_without_missing_optional_methods(self):
        driver = driver_with_fake()
        config = SignalGeneratorConfig(channel=2, mod_enabled=True, mod_type="FM")
        config.apply_to(driver)
        self.assertIn(":SOURce2:FM:INTernal:FREQuency 100.0", driver._inst.writes)
        for kwargs in ({"sweep_enabled": True}, {"polarity": "INVerted"},
                       {"shape": "PULSe", "pulse_leading": 1e-6}, {"shape": "HARMonic"}):
            driver._inst.writes.clear()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SignalGeneratorConfig(**kwargs).apply_to(driver)
            self.assertEqual(driver._inst.writes, [])

    def test_counter_returns_unparsed_string_and_load_returns_raw_value(self):
        for cls in (DG4000Instrument, DG900Instrument):
            driver = driver_with_fake(cls)
            self.assertIsNone(driver.set_counter())
            self.assertEqual(driver._inst.writes[-1], ":COUNter:STATe ON")
            self.assertEqual(driver.get_counter_measure(), "1.0E3,1E-3,50,5E-4,5E-4\n")
            driver._inst.responses[":OUTPut2:LOAD?"] = "INF"
            self.assertEqual(driver.get_output_load(), "INF")

    def test_pro_high_z_uses_short_keyword_and_verifies_readback(self):
        for value in ("INF", "INFinity", "INFinite", "9.9E+37", "+9.900000E+37", 9.9e37, 9e37):
            driver = driver_with_fake()
            driver._inst.responses[":OUTPut2:LOAD?"] = "9.900000E+37"
            with self.subTest(value=value):
                driver.set_output_load(value)
                self.assertEqual(driver._inst.writes, [":OUTPut2:LOAD INF"])
                self.assertEqual(driver._inst.queries, [":OUTPut2:LOAD?"])

    def test_classic_high_z_readback_is_reapplied_as_keyword_and_numeric_loads_are_preserved(self):
        for cls in (DG4000Instrument, DG900Instrument):
            for value, expected in (("9.9E+37", "INFinity" if cls is DG4000Instrument else "INF"),
                                    (9.9e37, "INFinity" if cls is DG4000Instrument else "INF"),
                                    (1, "1"), (50, "50"), ("50", "50")):
                driver = driver_with_fake(cls)
                with self.subTest(model=cls.__name__, value=value):
                    driver.set_output_load(value)
                    self.assertEqual(driver._inst.writes, [f":OUTPut2:LOAD {expected}"])
                    self.assertEqual(driver.get_output_load(), "9.9E+37" if expected.startswith("INF") else expected)

    def test_invalid_load_is_rejected_before_writing(self):
        for cls in (DG4000Instrument, DG900Instrument):
            for value in (0, -1, float("nan"), float("-inf"), "无效负载"):
                driver = driver_with_fake(cls)
                with self.subTest(model=cls.__name__, value=value), self.assertRaises(ValueError):
                    driver.set_output_load(value)
                self.assertEqual(driver._inst.writes, [])

    def test_pro_load_mismatch_is_reported_and_failed_connection_is_closed(self):
        for requested, actual in (("INF", "1"), ("INF", "50"), (50, "1")):
            driver = driver_with_fake()
            driver._inst.responses[":OUTPut2:LOAD?"] = actual
            with self.subTest(requested=requested, actual=actual), \
                 self.assertRaisesRegex(RuntimeError, "负载设置失败"):
                driver.set_output_load(requested)

        driver = DG900Instrument("fake", load_policy={1: "INF"})
        visa = FakeVisa()
        visa.responses[":OUTPut1:LOAD?"] = "1"
        resource_manager = Mock()
        resource_manager.open_resource.return_value = visa
        with patch("pyvisa.ResourceManager", return_value=resource_manager), \
             self.assertRaisesRegex(RuntimeError, "负载设置失败"):
            driver.connect()
        self.assertTrue(visa.closed)
        # ResourceManager 是进程级共享后端，清理时不得关闭它
        resource_manager.close.assert_not_called()
        self.assertIsNone(driver._inst)

    def test_screenshot_reads_complete_split_blocks_and_returns_saved_bytes(self):
        for cls, image_format, payload in ((DG4000Instrument, "BMP", BMP), (DG900Instrument, "BMP", BMP), (DG900Instrument, "PNG", PNG)):
            driver = driver_with_fake(cls)
            header = f"#{len(str(len(payload)))}{len(payload)}".encode()
            frame = header + payload + b"\n"
            driver._inst.raw_chunks = [frame[:1], frame[1:3], frame[3:17], frame[17:]]
            with self.subTest(model=cls.__name__, format=image_format), TemporaryDirectory() as directory:
                path = Path(directory) / "screen"
                self.assertEqual(driver.get_screenshot(image_format, path), payload)
                self.assertEqual(path.read_bytes(), payload)
                self.assertEqual(driver._inst.timeout, 1234)
                self.assertEqual(driver._inst.read_termination, "\r\n")
                formats = [cmd for cmd in driver._inst.writes if ":FORMat " in cmd]
                self.assertEqual(formats, [] if cls is DG4000Instrument else [f":HCOPy:SDUMp:DATA:FORMat {image_format}"])

    def test_screenshot_rejects_truncation_and_restores_session_after_read_error(self):
        for chunks in ([b"#258" + BMP[:-1]], [b"#257" + BMP[:-1]],
                       [VisaIOError(StatusCode.error_timeout)], [b"#0" + BMP]):
            driver = driver_with_fake()
            driver._inst.raw_chunks = list(chunks)
            with self.subTest(chunks=chunks), self.assertRaises((RuntimeError, ValueError, VisaIOError)):
                driver.get_screenshot()
            self.assertEqual(driver._inst.timeout, 1234)
            self.assertEqual(driver._inst.read_termination, "\r\n")
        with patch("pyvisa.ResourceManager") as manager, self.assertRaises(ValueError):
            DG4000Instrument("fake").get_screenshot("PNG")
        manager.assert_not_called()

    def test_pro_raw_image_streams_are_complete(self):
        for image_format, payload in (("BMP", BMP), ("PNG", PNG)):
            driver = driver_with_fake()
            driver._inst.raw_chunks = [payload[:17], payload[17:]]
            self.assertEqual(driver.get_screenshot(image_format), payload)

    def test_recovery_never_resets_implicitly_and_explicit_reset_closes_temporary_resource(self):
        for cls in (DG4000Instrument, DG900Instrument):
            driver = driver_with_fake(cls)
            self.assertEqual(driver.recover_connection(), IDENTITY)
            self.assertNotIn("*RST", driver._inst.writes)
            visa = FakeVisa()
            manager = Mock()
            manager.open_resource.return_value = visa
            with patch("pyvisa.ResourceManager", return_value=manager), patch("signal_generator._visa.time.sleep") as sleep:
                self.assertEqual(cls.reset_stuck_resource("fake"), IDENTITY)
            self.assertEqual(visa.writes, ["*RST", "*CLS"])
            sleep.assert_called_once_with(8.0)
            self.assertTrue(visa.closed)
            # ResourceManager 是进程级共享后端，不得关闭（可能仍有其他设备会话）
            manager.close.assert_not_called()

    def test_recovery_handles_empty_identity_and_invalid_parameters(self):
        visa = FakeVisa()
        visa.identity = " "
        manager = Mock()
        manager.open_resource.return_value = visa
        with patch("pyvisa.ResourceManager", return_value=manager), self.assertRaises(RuntimeError):
            DG900Instrument("fake").recover_connection(drain_reads=0)
        self.assertTrue(visa.closed)
        for args in ({"drain_reads": -1}, {"drain_reads": 1.5}):
            with patch("pyvisa.ResourceManager") as resource_manager, self.assertRaises(ValueError):
                DG900Instrument("fake").recover_connection(**args)
            resource_manager.assert_not_called()
        for value in (None, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                driver_with_fake().recover_connection_with_reset(value)


class ArbFileTests(unittest.TestCase):
    def test_strict_shape_range_and_point_count_reject_before_connecting(self):
        cases = [np.zeros((32, 1)), np.full(32, np.nan), np.full(32, np.inf), np.full(32, 1.001),
                 np.full(32, -1.001), np.full(32, 1j), np.zeros(31), np.zeros(16_385)]
        for data in cases:
            with self.subTest(shape=data.shape), patch("pyvisa.ResourceManager") as manager, self.assertRaises(ValueError):
                DG900Instrument("fake").upload_arb_wave("wave.arb", data)
            manager.assert_not_called()
        with self.assertRaises(ValueError):
            driver_with_fake().upload_arb_wave("wave.arb", np.zeros(31), advanced=True)
        driver = driver_with_fake()
        driver.MAX_ARB_POINTS = 33
        with self.assertRaises(ValueError):
            driver.upload_arb_wave("wave.arb", np.zeros(34), advanced=True)

    def test_single_segment_uses_end_and_records_only_successful_points(self):
        driver = driver_with_fake()
        self.assertEqual(driver.upload_arb_wave("wave.arb", np.linspace(-1, 1, 32), advanced=True), 32)
        writes = [cmd for cmd in driver._inst.writes if ":DATA:DAC " in cmd]
        self.assertEqual(len(writes), 1)
        self.assertIn('"INT:\\wave.arb",END,-32768,', writes[0])
        self.assertTrue(writes[0].endswith(",32767"))
        self.assertEqual(driver.get_arb_wave_points("INT:/./WAVE.arb"), 32)
        self.assertEqual(driver._inst.timeout, 1234)
        self.assertEqual(driver._inst.read_termination, "\r\n")
        self.assertEqual(driver._inst.write_termination, "\r\n")

    def test_segments_respect_byte_boundary_without_losing_or_duplicating_samples(self):
        for count in (2857, 2858, 8000):
            driver = driver_with_fake()
            driver.upload_arb_wave("wave.arb", np.full(count, -1.0), advanced=True)
            segments = [cmd.split(",", 2)[1:] for cmd in driver._inst.writes if ":DATA:DAC " in cmd]
            expected_flags = ["END"] if count == 2857 else ["HEADer", "END"] if count == 2858 else ["HEADer", "CONTinue", "END"]
            self.assertEqual([flag for flag, _ in segments], expected_flags)
            self.assertTrue(all(len(data.encode()) <= 20_000 for _, data in segments))
            codes = [int(value) for _, data in segments for value in data.split(",")]
            self.assertEqual(codes, [-32768] * count)
        driver = driver_with_fake()
        self.assertEqual(driver.upload_arb_wave("normal.arb", np.zeros(16_384)), 16_384)

    def test_overwrite_protection_handles_case_and_path_aliases(self):
        driver = driver_with_fake()
        driver.upload_arb_wave("wave.arb", np.zeros(32), advanced=True)
        driver._inst.catalog = '64,999,"wave.arb,ARB,587"'
        before = list(driver._inst.writes)
        with self.assertRaises(FileExistsError):
            driver.upload_arb_wave("INT:/folder/../WAVE.arb", np.zeros(32), advanced=True)
        self.assertEqual(driver._inst.writes, before)
        self.assertEqual(driver.get_arb_wave_points("wave.arb"), 32)
        self.assertEqual(driver.upload_arb_wave("wave.arb", np.zeros(33), advanced=True, overwrite=True), 33)

    def test_failed_overwrite_invalidates_record_and_restores_parameters(self):
        driver = driver_with_fake()
        driver.upload_arb_wave("wave.arb", np.zeros(32), advanced=True)
        driver._inst.fail_segment = 3
        driver.MAX_ARB_CHUNK_BYTES = 17
        with self.assertRaises(VisaIOError):
            driver.upload_arb_wave("wave.arb", np.zeros(32), advanced=True, overwrite=True)
        with self.assertRaises(KeyError):
            driver.get_arb_wave_points("wave.arb")
        self.assertEqual((driver._inst.timeout, driver._inst.read_termination, driver._inst.write_termination), (1234, "\r\n", "\r\n"))
        driver._inst.responses["*OPC?"] = "0"
        with self.assertRaises(RuntimeError):
            driver.upload_arb_wave("other.arb", np.zeros(32), advanced=True)
        with self.assertRaises(KeyError):
            driver.get_arb_wave_points("other.arb")

    def test_catalog_keeps_byte_sizes_and_never_guesses_points(self):
        driver = driver_with_fake()
        driver._inst.catalog = '64,999,"wave,one.arb,ARB,587", "folder,FOLD,0", "readme,,20"'
        self.assertEqual(driver.list_files(), [{"name": "wave,one.arb", "attribute": "ARB", "size_bytes": 587},
                                              {"name": "folder", "attribute": "FOLD", "size_bytes": 0},
                                              {"name": "readme", "attribute": "", "size_bytes": 20}])
        with self.assertRaises(KeyError):
            driver.get_arb_wave_points("wave,one.arb")
        for response in ('bad', '0,1,"wave.arb,ARB,-1"', '0,1,"wave.arb"', '0,1,"unclosed'):
            driver._inst.catalog = response
            with self.assertRaises(ValueError):
                driver.list_files()

    def test_configuration_uses_explicit_or_uploaded_points_and_native_modes(self):
        driver = driver_with_fake()
        with self.assertRaises(KeyError):
            driver.config_arb_wave(1, "existing.arb", freq=1000)
        driver.config_arb_wave(1, "existing.arb", freq=1000, points=32)
        self.assertIn(":SOURce1:FUNCtion:SEQuence:ARB:SRATe 32000", driver._inst.writes)
        self.assertIn(':SOURce1:FUNCtion:SEQuence:ARB:LOAD "INT:\\existing.arb"', driver._inst.writes)
        self.assertFalse(any(cmd.startswith(":OUTPut") for cmd in driver._inst.writes))
        with self.assertRaises(KeyError):
            driver.get_arb_wave_points("existing.arb")
        driver._inst.writes.clear()
        driver.upload_arb_wave("normal.arb", np.zeros(16_384))
        driver.config_arb_wave(2, "normal.arb", freq=10)
        self.assertIn(":SOURce2:FUNCtion:SEQuence:ARB:STATe 0", driver._inst.writes)
        self.assertIn(':MMEMory:LOAD:DATA 2,"INT:\\normal.arb"', driver._inst.writes)

    def test_frequency_choice_and_sampling_limits_fail_before_writing(self):
        driver = driver_with_fake()
        for channel in (0, 3, 1.0, True):
            with self.assertRaises(ValueError):
                driver.config_arb_wave(channel, "wave.arb", freq=1, points=32)
        for kwargs in ({}, {"freq": 1, "sample_rate": 32}, {"freq": 10e6, "points": 32},
                       {"sample_rate": 1e-7}, {"sample_rate": float("nan")}, {"sample_rate": 312.5e6 + 1},
                       {"freq": 0}, {"freq": 1, "points": True}, {"freq": 1, "points": 32.5},
                       {"freq": 1, "points": 16_777_217}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                driver.config_arb_wave(1, "wave.arb", **({"points": 32} | kwargs))
        self.assertEqual(driver._inst.writes, [])
        for rate in (1e-6, 312.5e6):
            driver.config_arb_wave(1, "wave.arb", sample_rate=rate, points=32)

    def test_delete_invalidates_uploaded_points(self):
        driver = driver_with_fake()
        driver.upload_arb_wave("wave.arb", np.zeros(32), advanced=True)
        self.assertIsNone(driver.delete_file("wave.arb"))
        self.assertIn(':MMEMory:DELete "INT:\\wave.arb"', driver._inst.writes)
        with self.assertRaises(KeyError):
            driver.get_arb_wave_points("wave.arb")


if __name__ == "__main__":
    unittest.main()
