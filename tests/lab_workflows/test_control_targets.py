import unittest
from unittest.mock import MagicMock, call, patch

from keithley_6221 import Keithley6221Instrument
from lab_workflows.devices import CURRENT_SOURCE_DRIVERS, ChannelRecord, DeviceRecord
from lab_workflows.instrument_control import (
    ControlRevisionConflict,
    _6221_duration_state,
    _apply_generator_connected,
    apply_control_target_current_source,
    list_control_targets,
    read_control_target,
)
from lab_workflows.instrument_config import DeviceDefinition


class ControlTargetTests(unittest.TestCase):
    @staticmethod
    def current_source_context(record_type="6221"):
        target = {
            "mapping_key": "coil_current",
            "kind": "current_source",
            "device_id": "source",
        }
        device = DeviceDefinition(
            device_id="source",
            instrument=("keithley_6221" if record_type == "6221" else "gs200"),
            model=("6221" if record_type == "6221" else "GS200"),
            label="Source",
            resource="USB::SOURCE::INSTR",
        )
        record = DeviceRecord(
            id="source", type=record_type, label="Source",
            resource="USB::SOURCE::INSTR", short_resource="SOURCE",
            options={"mapping_key": "coil_current"},
        )
        return target, device, record

    def test_catalog_follows_mapping_order_and_does_not_connect(self):
        with patch("lab_workflows.instrument_control._connect") as connect:
            catalog = list_control_targets()
        connect.assert_not_called()
        keys = [item["mapping_key"] for item in catalog["targets"]]
        self.assertEqual(keys[:3], [
            "keithley_6221_main_field", "main_magnetic_field", "Z_magnetic_field"
        ])
        keithley_target = catalog["targets"][0]
        self.assertEqual(keithley_target["model"], "6221")
        self.assertEqual(keithley_target["device_id"], "keithley_6221_4503331")
        self.assertEqual(keithley_target["safety"]["min"], -100.0)
        self.assertEqual(keithley_target["safety"]["max"], 100.0)
        z_target = catalog["targets"][2]
        self.assertEqual(z_target["model"], "DG4000")
        self.assertEqual(z_target["device_id"], "dg4e242401288")

    def test_keithley_6221_has_independent_registered_driver(self):
        self.assertIs(CURRENT_SOURCE_DRIVERS["6221"], Keithley6221Instrument)
        self.assertNotEqual(
            CURRENT_SOURCE_DRIVERS["6221"], CURRENT_SOURCE_DRIVERS["GS200"]
        )

    def test_6221_firmware_equivalent_duration_readback_is_preserved(self):
        instrument = MagicMock()
        instrument.get_waveform_duration_time.return_value = 0.01
        instrument.get_waveform_duration_cycles.return_value = 1.23
        self.assertEqual(_6221_duration_state(instrument), {
            "duration_mode": "MIXED",
            "duration_value": 0.01,
            "duration_time_s": 0.01,
            "duration_cycles": 1.23,
        })

    def test_rf_legacy_alias_is_hidden_and_y_field_keeps_full_safety(self):
        targets = {item["mapping_key"]: item for item in list_control_targets()["targets"]}
        self.assertNotIn("rf_coil", targets)
        self.assertEqual(targets["Y_magnetic_field"]["shared_mapping_keys"], [])
        self.assertEqual(targets["Y_magnetic_field"]["safety"]["min"], -10.0)
        self.assertEqual(targets["Y_magnetic_field"]["safety"]["max"], 10.0)
        self.assertTrue(all(not item["description"] for item in targets.values()))

    def test_stale_revision_is_rejected_before_connect(self):
        with patch("lab_workflows.instrument_control._connect") as connect:
            with self.assertRaises(ControlRevisionConflict):
                read_control_target("Z_magnetic_field", "stale", "stale")
        connect.assert_not_called()

    def test_non_vpp_only_allows_output_off_or_unit_conversion(self):
        target = {"mapping_key": "Z_magnetic_field"}
        record = DeviceRecord(
            id="dg", type="DG900", label="DG", resource="USB",
            short_resource="DG", channels=[ChannelRecord(1, "Z_magnetic_field", "Z")],
        )
        instrument = MagicMock()
        current = {"voltage_unit": "VRMS", "offset": 0.0, "amplitude": 1.0}
        with patch("lab_workflows.instrument_control._read_generator_channel", return_value=current), patch("lab_workflows.instrument_control._apply_basic_waveform") as basic:
            _apply_generator_connected(target, record, instrument, {"output": False})
            instrument.set_output.assert_called_once_with(False, 1)
            with self.assertRaisesRegex(ValueError, "不是 Vpp"):
                _apply_generator_connected(target, record, instrument, {"amplitude": 1.0})
            _apply_generator_connected(target, record, instrument, {"voltage_unit": "VPP"})
        self.assertEqual(basic.call_count, 2)

    def test_gs200_explicitly_rejects_6221_fields(self):
        target, device, record = self.current_source_context("GS200")
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -10.0, "max": 10.0},
        ), patch("lab_workflows.instrument_control._connect") as connect:
            with self.assertRaisesRegex(ValueError, "GS200 不支持"):
                apply_control_target_current_source(
                    "coil_current", "devices", "mapping",
                    {"compliance_v": 10.0},
                )
        connect.assert_not_called()

    def test_6221_builtin_waveform_envelope_is_checked_before_connect(self):
        target, device, record = self.current_source_context()
        settings = {"waveform": {
            "action": "configure",
            "shape": "SIN",
            "frequency_hz": 10.0,
            "amplitude_peak_ma": 3.0,
            "offset_ma": 3.0,
            "duty_cycle_percent": 50.0,
            "ranging": "BEST",
            "duration_mode": "INFINITE",
        }}
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -5.0, "max": 5.0},
        ), patch("lab_workflows.instrument_control._connect") as connect:
            with self.assertRaises(ValueError):
                apply_control_target_current_source(
                    "coil_current", "devices", "mapping", settings
                )
        connect.assert_not_called()

    def test_6221_arbitrary_start_uses_actual_envelope_and_start_order(self):
        target, device, record = self.current_source_context()
        instrument = MagicMock()
        settings = {"waveform": {
            "action": "configure_and_start",
            "shape": "ARB",
            "frequency_hz": 25.0,
            "amplitude_peak_ma": 4.0,
            "offset_ma": 1.0,
            "duty_cycle_percent": 50.0,
            "ranging": "FIXED",
            "duration_mode": "CYCLES",
            "duration_value": 2.0,
            "arbitrary_points": [-0.5, 0.25],
            "confirm_start": True,
        }}
        snapshot = {"type": "6221"}
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -2.0, "max": 2.0},
        ), patch(
            "lab_workflows.instrument_control._connect", return_value=instrument
        ), patch(
            "lab_workflows.instrument_control._read_6221_state",
            return_value=snapshot,
        ):
            response = apply_control_target_current_source(
                "coil_current", "devices", "mapping", settings
            )

        self.assertEqual(response["snapshot"], snapshot)
        instrument.upload_arbitrary.assert_called_once_with([-0.5, 0.25])
        calls = [call[0] for call in instrument.method_calls]
        self.assertLess(calls.index("abort_waveform"), calls.index("set_waveform_function"))
        self.assertLess(calls.index("set_waveform_function"), calls.index("arm_waveform"))
        self.assertLess(calls.index("arm_waveform"), calls.index("start_waveform"))

    def test_6221_start_requires_confirmation_before_connect(self):
        target, device, record = self.current_source_context()
        settings = {"waveform": {
            "action": "configure_and_start", "shape": "SIN",
            "frequency_hz": 10.0, "amplitude_peak_ma": 1.0,
            "offset_ma": 0.0, "ranging": "BEST",
            "duration_mode": "INFINITE",
        }}
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -5.0, "max": 5.0},
        ), patch("lab_workflows.instrument_control._connect") as connect:
            with self.assertRaisesRegex(ValueError, "二次确认"):
                apply_control_target_current_source(
                    "coil_current", "devices", "mapping", settings
                )
        connect.assert_not_called()

    def test_6221_write_failure_attempts_full_safe_shutdown(self):
        target, device, record = self.current_source_context()
        instrument = MagicMock()
        instrument.set_waveform_frequency.side_effect = RuntimeError("write failed")
        settings = {"waveform": {
            "action": "configure", "shape": "SIN",
            "frequency_hz": 10.0, "amplitude_peak_ma": 1.0,
            "offset_ma": 0.0, "ranging": "BEST",
            "duration_mode": "INFINITE",
        }}
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -5.0, "max": 5.0},
        ), patch(
            "lab_workflows.instrument_control._connect", return_value=instrument
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                apply_control_target_current_source(
                    "coil_current", "devices", "mapping", settings
                )
        self.assertEqual(instrument.abort_waveform.call_count, 2)
        instrument.set_output.assert_any_call(False)
        instrument.set_current.assert_called_once_with(0.0)
        instrument.disconnect.assert_called_once()

    def test_6221_range_or_response_change_turns_output_off_before_reenable(self):
        target, device, record = self.current_source_context()
        instrument = MagicMock()
        instrument.get_output.return_value = True
        settings = {
            "current_ma": 1.0,
            "output": True,
            "confirm_output_enable": True,
            "current_range_ma": 2.0,
            "output_response": "SLOW",
        }
        snapshot = {"type": "6221"}
        with patch(
            "lab_workflows.instrument_control._resolve_control_target",
            return_value=(target, device, None),
        ), patch(
            "lab_workflows.instrument_control._target_record", return_value=record
        ), patch(
            "lab_workflows.instrument_control._gs200_safety_rule",
            return_value={"min": -5.0, "max": 5.0},
        ), patch(
            "lab_workflows.instrument_control._connect", return_value=instrument
        ), patch(
            "lab_workflows.instrument_control._read_6221_state",
            return_value=snapshot,
        ):
            apply_control_target_current_source(
                "coil_current", "devices", "mapping", settings
            )
        calls = instrument.method_calls
        off_index = calls.index(call.set_output(False))
        range_index = calls.index(call.set_current_range(0.002))
        on_index = calls.index(call.set_output(True))
        self.assertLess(off_index, range_index)
        self.assertLess(range_index, on_index)


if __name__ == "__main__":
    unittest.main()
