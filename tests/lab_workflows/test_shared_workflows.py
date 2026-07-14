import unittest
import subprocess
import sys
from pathlib import Path

from lab_workflows.clock_sync import load_clock_profile
from lab_workflows.devices import discover_devices
from lab_workflows.static_sensitivity import (
    StaticSensitivityParams,
    preflight_static_sensitivity,
)
from lab_workflows.static_sensitivity.workflow import _build_subprocess_environment


class SharedWorkflowTests(unittest.TestCase):
    def test_device_discovery_groups_physical_devices(self):
        devices = discover_devices()
        self.assertEqual(len(devices), 7)
        keys = {
            channel.mapping_key
            for device in devices
            for channel in device.channels
        }
        self.assertNotIn("rf_coil", keys)
        self.assertIn("Y_magnetic_field", keys)
        heat = next(
            channel
            for device in devices
            for channel in device.channels
            if channel.mapping_key == "Heat_Control"
        )
        self.assertFalse(heat.read_only)
        labels = {device.id: device.label for device in devices}
        self.assertEqual(labels["DG9Q280100002"], "光功率")
        self.assertEqual(labels["DG4E231500376"], "XY方向AM信号")
        self.assertEqual(labels["DG4E234902522"], "XY方向磁场")

    def test_static_defaults_pass_preflight(self):
        params = StaticSensitivityParams.from_yaml()
        self.assertIsInstance(params.main_magnetic_field, float)
        self.assertEqual(preflight_static_sensitivity(params), [])
        self.assertGreater(len(StaticSensitivityParams.schema(params)["fields"]), 30)

    def test_invalid_static_range_is_rejected(self):
        params = StaticSensitivityParams.from_yaml()
        params.ramp_low = 1
        params.ramp_high = -1
        self.assertIn("Z 扫场下限必须小于上限", preflight_static_sensitivity(params))

    def test_static_defaults_round_trip_yaml(self):
        params = StaticSensitivityParams.from_yaml()
        params.run_tag = "saved-default"
        path = Path.cwd() / "tests" / ".static_sensitivity_defaults_test.yaml"
        try:
            params.to_yaml(path)
            restored = StaticSensitivityParams.from_yaml(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(restored.run_tag, "saved-default")
        self.assertEqual(restored.gs200_current_ranges, params.gs200_current_ranges)

    def test_static_subprocess_uses_utf8_output(self):
        params = StaticSensitivityParams.from_yaml()
        environment = _build_subprocess_environment(
            params, Path.cwd() / "data" / ".test_cancel"
        )
        completed = subprocess.run(
            [sys.executable, "-c", 'print("实验输出\\u2705")'],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(completed.stdout.strip(), "实验输出\u2705")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_clock_profile_preserves_internal_pump_clock(self):
        profile = load_clock_profile()
        self.assertEqual(profile["DG4E222800868"], "INT")
        self.assertEqual(profile["dev18246"], "EXT")


if __name__ == "__main__":
    unittest.main()
