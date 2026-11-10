"""设备端口真实 API 的无硬件、临时目录定向测试。"""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from GUI.backend import main


class DevicePortsAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch.object(main, "ROOT", self.root))
        self.hardware_guards = []
        for name in (
            "discover_devices", "discover_visa_devices", "read_device", "read_control_target",
            "read_control_targets", "apply_generator_channel", "apply_current_source",
            "apply_laser_emission", "apply_laser_settings", "apply_scope",
            "save_device_library", "save_physical_mappings", "public_device_library",
            "public_physical_mappings", "load_mapping", "initialize_output_loads", "synchronize_clocks",
        ):
            guard = self.stack.enter_context(patch.object(main, name, side_effect=AssertionError(f"不应调用硬件或配置：{name}")))
            self.hardware_guards.append(guard)
        for name in ("try_acquire_short_hardware", "create"):
            guard = self.stack.enter_context(patch.object(main.manager, name, side_effect=AssertionError(f"不应使用硬件任务：{name}")))
            self.hardware_guards.append(guard)
        lock = Mock()
        lock.acquire.side_effect = AssertionError("不应获取硬件锁")
        lock.release.side_effect = AssertionError("不应释放硬件锁")
        self.stack.enter_context(patch.object(main.manager, "hardware_lock", lock))
        self.hardware_guards.append(lock)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def tearDown(self):
        for guard in self.hardware_guards:
            self.assertEqual(guard.mock_calls, [])

    def test_get_missing_and_put_roundtrip_use_only_independent_port_archive(self):
        response = self.client.get("/api/device-ports")
        self.assertEqual(response.status_code, 200, response.text)
        initial = response.json()
        self.assertEqual(set(initial), {"schema_version", "revision", "devices", "presets"})
        self.assertEqual(initial["schema_version"], 1)
        self.assertEqual(initial["devices"], [])
        self.assertEqual(list(self.root.iterdir()), [])
        preset = initial["presets"][0]
        device = {
            "id": "stable-device", "name": "试验信号源", "model": preset["model"],
            "device_library_id": "removed-library-id", "ports": preset["ports"],
        }
        body = {"base_revision": initial["revision"], "devices": [device]}
        saved = self.client.put("/api/device-ports", json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["devices"], [device])
        self.assertEqual(saved.json(), self.client.get("/api/device-ports").json())
        self.assertNotEqual(saved.json()["revision"], initial["revision"])
        self.assertEqual([path.name for path in (self.root / "params").iterdir()], ["device_ports.yaml"])

    def test_mod_presets_preserve_ids_and_expose_single_direction_am_and_fm(self):
        response = self.client.get("/api/device-ports")
        self.assertEqual(response.status_code, 200, response.text)
        presets = {preset["id"]: preset for preset in response.json()["presets"]}
        for channel in (1, 2):
            external = next(port for port in presets["dg4000"]["ports"] if port["id"] == f"ch{channel}_external")
            self.assertEqual(external["name"], f"CH{channel} External")
            self.assertIn("MOD（AM / FM）", external["purpose"])
            self.assertEqual((external["direction"], external["modes"], external["selected_mode"]), ("in", [], None))
            multi = next(port for port in presets["dg900"]["ports"] if port["id"] == f"ch{channel}_multi")
            self.assertEqual(multi["name"], f"CH{channel} Multi")
            self.assertIn("MOD（AM / FM）", multi["purpose"])
            self.assertEqual([(mode["id"], mode["name"], mode["direction"]) for mode in multi["modes"]], [
                ("sync", "同步", "out"), ("trigger", "触发", "in"), ("gate", "门控", "in"),
                ("am", "AM", "in"), ("fm", "FM", "in"),
            ])
            self.assertEqual(multi["direction"], "switchable")
            self.assertIsNone(multi["selected_mode"])
        self.assertTrue(all(port["direction"] != "inout" and all(mode["direction"] != "inout" for mode in port["modes"])
                            for preset in presets.values() for port in preset["ports"]))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_legacy_inout_port_and_mode_get_put_roundtrip_stays_compatible(self):
        initial = self.client.get("/api/device-ports").json()
        switchable = deepcopy(initial["presets"][0]["ports"][-1])
        switchable["modes"][0].update(name="旧双向模式", direction="inout")
        switchable["selected_mode"] = switchable["modes"][0]["id"]
        fixed = deepcopy(switchable)
        fixed.update(id="legacy-inout", name="旧双向端口", direction="inout", modes=[], selected_mode=None)
        device = {"id": "legacy-device", "name": "旧设备", "model": "", "device_library_id": None,
                  "ports": [switchable, fixed]}
        response = self.client.put("/api/device-ports", json={"base_revision": initial["revision"], "devices": [device]})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["devices"], [device])
        path = self.root / "params" / "device_ports.yaml"
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(self.client.get("/api/device-ports").json(), saved)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        response = self.client.put("/api/device-ports", json={"base_revision": saved["revision"], "devices": saved["devices"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), saved)
        self.assertEqual(path.read_bytes(), before[0])
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_requests_are_422_without_creating_archive(self):
        initial = self.client.get("/api/device-ports").json()
        device = {
            "id": "device-1", "name": "仪器", "model": "", "device_library_id": None,
            "ports": [initial["presets"][0]["ports"][-1]],
        }
        valid = {"base_revision": initial["revision"], "devices": [device]}
        candidates = [
            {**valid, "unknown": True},
            {"base_revision": 1, "devices": []},
            {"base_revision": initial["revision"], "devices": "invalid"},
        ]
        for field, value in (("name", " "), ("model", False), ("device_library_id", ""), ("ports", {})):
            candidate = deepcopy(valid)
            candidate["devices"][0][field] = value
            candidates.append(candidate)
        duplicate_device = deepcopy(valid)
        duplicate_device["devices"].append(deepcopy(device))
        candidates.append(duplicate_device)
        duplicate_library = deepcopy(duplicate_device)
        duplicate_library["devices"][0]["device_library_id"] = "same-library-id"
        duplicate_library["devices"][1].update(id="device-2", device_library_id="same-library-id")
        candidates.append(duplicate_library)
        for field, value in (("direction", "out"), ("modes", []), ("selected_mode", "missing"), ("connector", 1), ("unknown", True)):
            candidate = deepcopy(valid)
            candidate["devices"][0]["ports"][0][field] = value
            candidates.append(candidate)
        duplicate_port = deepcopy(valid)
        duplicate_port["devices"][0]["ports"].append(deepcopy(device["ports"][0]))
        candidates.append(duplicate_port)
        duplicate_mode = deepcopy(valid)
        duplicate_mode["devices"][0]["ports"][0]["modes"].append(deepcopy(device["ports"][0]["modes"][0]))
        candidates.append(duplicate_mode)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                response = self.client.put("/api/device-ports", json=candidate)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stale_revision_has_explicit_conflict_header(self):
        initial = self.client.get("/api/device-ports").json()
        request = {"base_revision": initial["revision"], "devices": []}
        saved = self.client.put("/api/device-ports", json=request)
        self.assertEqual(saved.status_code, 200, saved.text)
        conflict = self.client.put("/api/device-ports", json=request)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.headers["X-Error-Code"], "revision_conflict")
        self.assertEqual(self.client.get("/api/device-ports").json(), saved.json())

    def test_corrupt_and_unknown_version_get_put_report_storage_errors_and_preserve_bytes(self):
        initial = self.client.get("/api/device-ports").json()
        path = self.root / "params" / "device_ports.yaml"
        path.parent.mkdir()
        for content in (b"devices: [", b"schema_version: 99\ndevices: []\n"):
            with self.subTest(content=content):
                path.write_bytes(content)
                get_response = self.client.get("/api/device-ports")
                put_response = self.client.put("/api/device-ports", json={"base_revision": initial["revision"], "devices": []})
                for response in (get_response, put_response):
                    self.assertEqual(response.status_code, 500, response.text)
                    self.assertIn("设备端口档案", response.json()["detail"])
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_external_change_between_revision_check_and_replace_returns_409(self):
        initial = self.client.get("/api/device-ports").json()
        saved = self.client.put("/api/device-ports", json={"base_revision": initial["revision"], "devices": []})
        self.assertEqual(saved.status_code, 200, saved.text)
        path = self.root / "params" / "device_ports.yaml"
        external = path.read_bytes() + b"\n# external edit during save\n"
        real_read = Path.read_bytes
        reads = 0

        def change_after_revision_check(candidate):
            nonlocal reads
            if candidate == path:
                reads += 1
                if reads == 2:
                    path.write_bytes(external)
            return real_read(candidate)

        with patch.object(Path, "read_bytes", change_after_revision_check):
            conflict = self.client.put("/api/device-ports", json={"base_revision": saved.json()["revision"], "devices": []})
        self.assertEqual(reads, 2)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.headers["X-Error-Code"], "revision_conflict")
        self.assertIn("保存期间被外部修改", conflict.json()["detail"])
        self.assertEqual(path.read_bytes(), external)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_write_error_is_500_and_leaves_existing_archive_unchanged(self):
        initial = self.client.get("/api/device-ports").json()
        saved = self.client.put("/api/device-ports", json={"base_revision": initial["revision"], "devices": []}).json()
        path = self.root / "params" / "device_ports.yaml"
        before = path.read_bytes()
        with patch("lab_workflows.device_ports.os.replace", side_effect=OSError("模拟磁盘写入失败")):
            response = self.client.put("/api/device-ports", json={"base_revision": saved["revision"], "devices": []})
        self.assertEqual(response.status_code, 500, response.text)
        self.assertIn("写入失败", response.json()["detail"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
