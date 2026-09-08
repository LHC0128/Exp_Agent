import sys
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))

from backend.main import (
    DeviceLibraryBody,
    KeithleyWaveformConvertBody,
    PhysicalMappingsBody,
    app,
    keithley_waveform_convert,
    manager,
    update_device_library,
    update_laser,
    update_laser_emission,
    update_physical_mappings,
)
from backend.schemas import (
    CurrentSourceSettings,
    LaserEmissionSettingsBody,
    LaserSettingsBody,
)
from lab_workflows.instrument_config import RevisionConflict


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
    def test_current_source_request_rejects_dc_and_waveform_mix(self):
        with self.assertRaisesRegex(ValueError, "不能混在同一请求"):
            CurrentSourceSettings.model_validate({
                "current_ma": 1.0,
                "waveform": {"action": "abort"},
            })

    def test_current_source_waveform_start_requires_confirmation(self):
        with self.assertRaisesRegex(ValueError, "confirm_start"):
            CurrentSourceSettings.model_validate({
                "waveform": {
                    "action": "configure_and_start",
                    "shape": "SIN",
                    "frequency_hz": 10.0,
                    "amplitude_peak_ma": 1.0,
                    "offset_ma": 0.0,
                    "ranging": "BEST",
                    "duration_mode": "INFINITE",
                }
            })

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

    def test_configuration_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        for expected in (
            ("/api/device-library", "GET"),
            ("/api/device-library", "PUT"),
            ("/api/device-library/discover-visa", "POST"),
            ("/api/physical-mappings", "GET"),
            ("/api/physical-mappings", "PUT"),
        ):
            self.assertIn(expected, routes)

    def test_control_target_routes_are_registered(self):
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        for expected in (
            ("/api/control-targets", "GET"),
            ("/api/control-targets/refresh-all", "POST"),
            ("/api/control-targets/{mapping_key}/refresh", "POST"),
            ("/api/control-targets/{mapping_key}/generator", "PUT"),
            ("/api/control-targets/{mapping_key}/current-source", "PUT"),
            ("/api/control-targets/{mapping_key}/laser", "PUT"),
            ("/api/control-targets/{mapping_key}/emission", "PUT"),
            ("/api/control-targets/{mapping_key}/scope", "PUT"),
            ("/api/control-targets/{mapping_key}/tec", "PUT"),
        ):
            self.assertIn(expected, routes)

    def test_device_library_revision_conflict_returns_409(self):
        body = DeviceLibraryBody(base_revision="old", devices={})
        with patch(
            "backend.main.save_device_library",
            side_effect=RevisionConflict("stale"),
        ):
            with self.assertRaises(HTTPException) as caught:
                update_device_library(body)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.headers.get("X-Error-Code"), "revision_conflict")

    def test_hardware_busy_conflict_carries_error_code(self):
        body = DeviceLibraryBody(base_revision="current", devices={})
        self.assertTrue(manager.hardware_lock.acquire(blocking=False))
        try:
            with self.assertRaises(HTTPException) as caught:
                update_device_library(body)
        finally:
            manager.hardware_lock.release()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.headers.get("X-Error-Code"), "hardware_busy")

    def test_keithley_waveform_convert_is_pure_computation(self):
        body = KeithleyWaveformConvertBody(
            arbitrary_text="1000\n3000\n5000\n",
            calibration_text=(
                "experiment_id: mx-keithley-6221-main-field-calibration\n"
                "success: true\n"
                "K_f_Hz_per_mA: 2.0\n"
                "f_0mA_Hz: 1000.0\n"
                "frequency_linear_fit:\n  r_squared: 0.999\n"
            ),
        )
        result = keithley_waveform_convert(body)
        self.assertEqual(result["calibration"]["slope_hz_per_ma"], 2.0)
        self.assertEqual(result["waveform"]["minimum_ma"], 0.0)
        self.assertEqual(result["waveform"]["maximum_ma"], 2000.0)
        self.assertEqual(result["waveform"]["points"], [-1.0, 0.0, 1.0])

    def test_keithley_waveform_convert_rejects_bad_input(self):
        body = KeithleyWaveformConvertBody(arbitrary_text="恒定,恒定\n")
        with self.assertRaises(HTTPException) as caught:
            keithley_waveform_convert(body)
        self.assertEqual(caught.exception.status_code, 422)

    def test_physical_mapping_validation_returns_422(self):
        body = PhysicalMappingsBody(
            base_revision="current",
            mapping={},
            constraints={
                "shared_channel_groups": {},
                "colocation_groups": {},
            },
        )
        with patch(
            "backend.main.save_physical_mappings",
            side_effect=ValueError("invalid endpoint"),
        ):
            with self.assertRaises(HTTPException) as caught:
                update_physical_mappings(body)
        self.assertEqual(caught.exception.status_code, 422)

    def test_configuration_save_is_rejected_while_hardware_busy(self):
        body = DeviceLibraryBody(base_revision="current", devices={})
        self.assertTrue(manager.hardware_lock.acquire(blocking=False))
        try:
            with self.assertRaises(HTTPException) as caught:
                update_device_library(body)
        finally:
            manager.hardware_lock.release()
        self.assertEqual(caught.exception.status_code, 409)

    def test_configuration_save_rejected_while_hardware_job_queued(self):
        """TOCTOU 场景：锁短暂空闲但已有排队任务时配置写入必须拒绝。"""
        body = DeviceLibraryBody(base_revision="current", devices={})
        release = Event()
        first = manager.create("first", lambda _job: release.wait(1))
        for _ in range(100):
            if first.status == "running":
                break
            time.sleep(0.01)
        second = manager.create("second", lambda _job: {"value": 2})
        for _ in range(100):
            if manager.queued_hardware_count() == 1:
                break
            time.sleep(0.01)
        self.assertEqual(second.status, "queued")
        # 释放第一个任务：锁短暂空闲，但第二个任务已排队，
        # 配置写入不能利用该窗口直接落盘。
        release.set()
        with self.assertRaises(HTTPException) as caught:
            update_device_library(body)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(
            caught.exception.headers.get("X-Error-Code"), "hardware_busy"
        )
        for _ in range(100):
            if second.status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        # 队列清空且无任务运行后，写入与检查在硬件锁内原子完成。
        with patch(
            "backend.main.save_device_library",
            return_value={"ok": True},
        ) as save:
            result = update_device_library(body)
        self.assertEqual(result, {"ok": True})
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
