import sys
import unittest
from pathlib import Path

from pydantic import TypeAdapter, ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.schemas import (
    CurrentSourceSettingsBody,
    DeviceSnapshot,
    GeneratorChannelSettingsBody,
    LaserEmissionSettingsBody,
    LaserSettingsBody,
    ScopeSettingsBody,
)


class DeviceSchemaTests(unittest.TestCase):
    def test_generator_settings_reject_unknown_fields(self):
        with self.assertRaises(ValidationError):
            GeneratorChannelSettingsBody.model_validate(
                {"settings": {"frequecy": 1000.0}}
            )

    def test_scope_settings_keep_only_explicit_fields(self):
        body = ScopeSettingsBody.model_validate(
            {"settings": {"timebase_scale": 0.001}}
        )
        self.assertEqual(
            body.settings.model_dump(exclude_unset=True),
            {"timebase_scale": 0.001},
        )

    def test_device_snapshot_is_discriminated_by_type(self):
        value = TypeAdapter(DeviceSnapshot).validate_python(
            {
                "id": "scope",
                "type": "SDS",
                "label": "示波器",
                "resource": "USB::scope",
                "short_resource": "scope",
                "options": {},
                "idn": "SDS",
                "sampling_rate": 1e6,
                "memory_depth": "1M",
                "acquire_type": "NORMal",
                "timebase_scale": 0.001,
                "timebase_delay": 0.0,
                "channels": [],
                "trigger": {
                    "mode": "AUTO",
                    "type": "EDGE",
                    "source": "C1",
                    "slope": "RISing",
                    "level": 0.0,
                },
            }
        )
        self.assertEqual(value.type, "SDS")

    def test_current_source_settings_reject_unknown_fields(self):
        with self.assertRaises(ValidationError):
            CurrentSourceSettingsBody.model_validate(
                {"settings": {"current_mA": 9.3}}
            )

    def test_current_source_settings_require_a_change(self):
        with self.assertRaises(ValidationError):
            CurrentSourceSettingsBody.model_validate({"settings": {}})

    def test_current_source_snapshot_is_discriminated_by_type(self):
        value = TypeAdapter(DeviceSnapshot).validate_python(
            {
                "id": "90Z631552",
                "type": "GS200",
                "label": "主磁场",
                "resource": "USB::GS200",
                "short_resource": "90Z631552",
                "options": {
                    "mapping_key": "main_magnetic_field",
                    "source_function": "CURRent",
                },
                "idn": "YOKOGAWA,GS210,90Z631552,2.02",
                "mapping_key": "main_magnetic_field",
                "source_function": "CURR",
                "output": True,
                "current_ma": 9.305,
                "current_range_ma": 10.0,
                "voltage_limit_v": 10.0,
                "current_limit_ma": 200.0,
                "min_current_ma": -10.0,
                "max_current_ma": 10.0,
            }
        )
        self.assertEqual(value.type, "GS200")
        self.assertAlmostEqual(value.current_ma, 9.305)

    def test_laser_settings_require_a_change(self):
        with self.assertRaises(ValidationError):
            LaserSettingsBody.model_validate({"settings": {}})

    def test_laser_settings_reject_scan_frequency_write(self):
        with self.assertRaises(ValidationError):
            LaserSettingsBody.model_validate(
                {"settings": {"scan_frequency_hz": 20.0}}
            )

    def test_laser_emission_requires_boolean_enabled(self):
        with self.assertRaises(ValidationError):
            LaserEmissionSettingsBody.model_validate(
                {"settings": {"confirmation_text": "DLC PRO_53043"}}
            )

    def test_laser_snapshot_is_discriminated_by_type(self):
        value = TypeAdapter(DeviceSnapshot).validate_python(
            {
                "id": "DLC_PRO_53043",
                "type": "DLC_PRO",
                "label": "Probe 激光",
                "resource": "192.168.124.68",
                "short_resource": "DLC PRO_53043",
                "options": {},
                "controller_serial": "DLC PRO_53043",
                "system_type": "DLCpro",
                "system_label": "",
                "firmware_version": "3.5.1",
                "system_health_code": 0,
                "system_health": "OK",
                "interlock_open": False,
                "front_key_locked": False,
                "emission": False,
                "laser_type": "DLpro",
                "laser_product_name": "DLpro (S/N 24105)",
                "laser_enabled": True,
                "laser_health_code": 0,
                "laser_health": "OK",
                "laser_emission": False,
                "laser_head_model": "unknown",
                "laser_head_serial": "24105",
                "current_set_ma": 201.0,
                "current_actual_ma": 201.1,
                "current_clip_ma": 392.0,
                "current_clip_limit_ma": 392.0,
                "min_current_ma": 200.0,
                "max_current_ma": 390.0,
                "temperature_set_c": 20.3,
                "temperature_actual_c": 20.3,
                "min_temperature_c": 19.0,
                "max_temperature_c": 21.0,
                "pzt_voltage_v": 80.0,
                "pzt_actual_v": 80.0,
                "min_pzt_voltage_v": 0.0,
                "max_pzt_voltage_v": 140.0,
                "scan_amplitude_vpp": 34.0,
                "min_scan_amplitude_vpp": 0.0,
                "max_scan_amplitude_vpp": 50.0,
                "scan_frequency_hz": 11.0,
                "scan_enabled": False,
                "scan_unit": "V",
                "scan_output_channel": 50,
                "remote_emission_control_enabled": False,
                "safety_keys": {
                    "current": "probe_laser_current",
                    "temperature": "probe_laser_temperature",
                    "pzt": "probe_laser_pzt_voltage",
                    "scan_amplitude": "probe_laser_scan_amplitude",
                },
                "state_known": True,
            }
        )
        self.assertEqual(value.type, "DLC_PRO")
        self.assertEqual(value.controller_serial, "DLC PRO_53043")


if __name__ == "__main__":
    unittest.main()
