import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.main import app, update_laser, update_laser_emission
from backend.schemas import LaserEmissionSettingsBody, LaserSettingsBody


def laser_snapshot(**overrides):
    value = {
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
    value.update(overrides)
    return value


class InstrumentRouteTests(unittest.TestCase):
    def test_laser_route_passes_explicit_settings(self):
        returned = laser_snapshot(current_set_ma=202.0)
        with patch(
            "backend.main.apply_laser_settings",
            return_value=returned,
        ) as apply:
            response = update_laser(
                "DLC_PRO_53043",
                LaserSettingsBody.model_validate(
                    {"settings": {"current_set_ma": 202.0}}
                ),
            )
        self.assertEqual(response["current_set_ma"], 202.0)
        apply.assert_called_once_with(
            "DLC_PRO_53043",
            {"current_set_ma": 202.0},
        )

    def test_emission_route_passes_confirmation_fields(self):
        returned = laser_snapshot(emission=True, laser_emission=True)
        settings = {
            "enabled": True,
            "safety_acknowledged": True,
            "confirm_emission_enable": True,
            "confirmation_text": "DLC PRO_53043",
        }
        with patch(
            "backend.main.apply_laser_emission",
            return_value=returned,
        ) as apply:
            response = update_laser_emission(
                "DLC_PRO_53043",
                LaserEmissionSettingsBody.model_validate(
                    {"settings": settings}
                ),
            )
        self.assertTrue(response["emission"])
        apply.assert_called_once_with("DLC_PRO_53043", settings)

    def test_laser_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(
            ("/api/devices/{device_id}/laser", "PUT"),
            routes,
        )
        self.assertIn(
            ("/api/devices/{device_id}/emission", "PUT"),
            routes,
        )


if __name__ == "__main__":
    unittest.main()
