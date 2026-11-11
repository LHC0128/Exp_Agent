"""装置登记 API 的无硬件、临时根目录回归。"""

from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from GUI.backend import main
from GUI.backend.jobs import Job, JobManager
from lab_workflows.setups import SETUP_ENV_VAR


class SetupsAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {SETUP_ENV_VAR: ""}))
        self.stack.enter_context(patch.object(main, "ROOT", self.root))
        self.manager = JobManager()
        self.stack.enter_context(patch.object(main, "manager", self.manager))
        self.addCleanup(self.manager.wait_for_shutdown)
        params = self.root / "params"
        params.mkdir()
        (params / "devices.yaml").write_text("devices: {}\n", encoding="utf-8")
        (params / "mapping.yaml").write_text(
            "mapping:\n  Z_magnetic_field:\n    instrument: signal_generator\n"
            "    model: DG4000\n    resource: FAKE_A\n    channel: 1\n",
            encoding="utf-8",
        )
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def registry(self):
        response = self.client.get("/api/setups")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_setup(self, label="磁力仪 B"):
        response = self.client.post("/api/setups", json={
            "label": label, "copy_from": "default", "base_revision": self.registry()["revision"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        registry = response.json()
        setup_id = next(item["id"] for item in registry["setups"] if item["label"] == label)
        return registry, setup_id

    def test_get_missing_registry_returns_implicit_default_without_writing(self):
        before = set(self.root.rglob("*"))
        registry = self.registry()
        self.assertEqual(registry["active"], "default")
        self.assertFalse(registry["stored"])
        self.assertEqual(registry["setups"], [{"id": "default", "label": "默认装置", "archived": False}])
        self.assertEqual(len(registry["revision"]), 64)
        self.assertEqual(self.registry(), registry)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_post_creates_visible_setup_and_keeps_active(self):
        before_mapping = (self.root / "params" / "mapping.yaml").read_bytes()
        registry, setup_id = self.create_setup()
        self.assertEqual(registry["active"], "default")
        self.assertTrue(registry["stored"])
        self.assertIn({"id": setup_id, "label": "磁力仪 B", "archived": False}, registry["setups"])
        self.assertEqual(self.registry(), registry)
        self.assertEqual((self.root / "params" / "mapping.yaml").read_bytes(), before_mapping)
        self.assertEqual((self.root / "params" / "setups" / setup_id / "mapping.yaml").read_bytes(), before_mapping)

    def test_put_active_updates_mapping_and_wiring_setup_context(self):
        initial_mapping_response = self.client.get("/api/physical-mappings")
        self.assertEqual(initial_mapping_response.status_code, 200, initial_mapping_response.text)
        initial_mapping = initial_mapping_response.json()
        self.assertEqual((initial_mapping["setup_id"], initial_mapping["setup_label"]), ("default", "默认装置"))
        initial_wiring = self.client.get("/api/wiring")
        self.assertEqual(initial_wiring.status_code, 200, initial_wiring.text)
        self.assertEqual(initial_wiring.json()["setup_id"], "default")
        registry, setup_id = self.create_setup()
        copied_mapping = self.client.get("/api/physical-mappings").json()
        self.assertEqual(copied_mapping["revision"], initial_mapping["revision"])
        response = self.client.put("/api/setups/active", json={
            "setup_id": setup_id, "base_revision": registry["revision"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["active"], setup_id)
        self.assertEqual(self.registry(), response.json())
        mapping_response = self.client.get("/api/physical-mappings")
        self.assertEqual(mapping_response.status_code, 200, mapping_response.text)
        mapping = mapping_response.json()
        self.assertEqual((mapping["setup_id"], mapping["setup_label"]), (setup_id, "磁力仪 B"))
        self.assertEqual(mapping["revision"], initial_mapping["revision"])
        self.assertEqual(mapping["mapping"], initial_mapping["mapping"])
        wiring = self.client.get("/api/wiring")
        self.assertEqual(wiring.status_code, 200, wiring.text)
        self.assertEqual(wiring.json()["setup_id"], setup_id)
        saved_wiring = self.client.put("/api/wiring", json={
            "base_revision": wiring.json()["revision"], "site": {"nodes": [], "edges": []}, "targets": {},
        })
        self.assertEqual(saved_wiring.status_code, 200, saved_wiring.text)
        self.assertEqual(saved_wiring.json()["setup_id"], setup_id)

    def test_patch_renames_archives_and_restores_setup(self):
        registry, setup_id = self.create_setup()
        response = self.client.patch(f"/api/setups/{setup_id}", json={
            "base_revision": registry["revision"], "label": "备用装置", "archived": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["active"], "default")
        self.assertIn({"id": setup_id, "label": "备用装置", "archived": True}, response.json()["setups"])
        self.assertEqual(self.registry(), response.json())
        restored = self.client.patch(f"/api/setups/{setup_id}", json={
            "base_revision": response.json()["revision"], "archived": False,
        })
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIn({"id": setup_id, "label": "备用装置", "archived": False}, restored.json()["setups"])
        self.assertEqual(self.registry(), restored.json())

    def test_patch_requires_label_or_archived(self):
        registry, setup_id = self.create_setup()
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        for fields in ({}, {"label": None}, {"archived": None}, {"label": None, "archived": None}):
            with self.subTest(fields=fields):
                response = self.client.patch(f"/api/setups/{setup_id}", json={
                    "base_revision": registry["revision"], **fields,
                })
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.registry(), registry)

    def assert_writes_busy(self, registry, setup_id):
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes(), path.stat().st_mtime_ns
        files_before = {item: item.read_bytes() for item in (self.root / "params").rglob("*") if item.is_file()}
        requests = (
            ("PUT", "/api/setups/active", {"setup_id": setup_id}),
            ("POST", "/api/setups", {"label": "磁力仪 C", "copy_from": "default"}),
            ("PATCH", f"/api/setups/{setup_id}", {"label": "备用装置"}),
        )
        for method, url, fields in requests:
            with self.subTest(method=method):
                response = self.client.request(method, url, json={"base_revision": registry["revision"], **fields})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.headers["X-Error-Code"], "hardware_busy")
                self.assertIsInstance(response.json()["detail"], str)
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
                self.assertEqual({item: item.read_bytes() for item in (self.root / "params").rglob("*") if item.is_file()},
                                 files_before)
        self.assertEqual(self.registry(), registry)

    def test_held_hardware_lock_rejects_all_setup_writes(self):
        registry, setup_id = self.create_setup()
        self.assertTrue(self.manager.hardware_lock.acquire(blocking=False))
        try:
            self.assert_writes_busy(registry, setup_id)
            self.assertTrue(self.manager.hardware_lock.locked())
        finally:
            self.manager.hardware_lock.release()

    def test_queued_hardware_rejects_all_setup_writes_even_with_free_lock(self):
        registry, setup_id = self.create_setup()
        queued = Job(id="queued", kind="fake-hardware")
        with self.manager._hardware_condition:
            self.manager._hardware_queue.append(queued)
        try:
            self.assertFalse(self.manager.hardware_lock.locked())
            self.assert_writes_busy(registry, setup_id)
            self.assertEqual(self.manager.queued_hardware_count(), 1)
            self.assertFalse(self.manager.hardware_lock.locked())
        finally:
            with self.manager._hardware_condition:
                self.manager._hardware_queue.clear()

    def test_stale_revision_rejects_all_setup_writes(self):
        stale_revision = self.registry()["revision"]
        registry, setup_id = self.create_setup()
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        for method, url, fields in (
            ("PUT", "/api/setups/active", {"setup_id": setup_id}),
            ("POST", "/api/setups", {"label": "磁力仪 C", "copy_from": "default"}),
            ("PATCH", f"/api/setups/{setup_id}", {"label": "备用装置"}),
        ):
            with self.subTest(method=method):
                response = self.client.request(method, url, json={"base_revision": stale_revision, **fields})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.headers["X-Error-Code"], "revision_conflict")
                self.assertEqual(response.json(), {"detail": "装置登记已被其他操作修改，请刷新后重试"})
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(self.manager.hardware_lock.locked())
        self.assertEqual(self.registry(), registry)

    def test_current_setup_archive_duplicate_names_and_unknown_source_are_422(self):
        registry, setup_id = self.create_setup()
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        cases = (
            ("PATCH", "/api/setups/default", {"archived": True}, "当前装置不能归档，请先切换到其他装置"),
            ("PATCH", f"/api/setups/{setup_id}", {"label": "默认装置"}, "装置名称“默认装置”已被使用"),
            ("POST", "/api/setups", {"label": "磁力仪 B", "copy_from": "default"}, "装置名称“磁力仪 B”已被使用"),
            ("POST", "/api/setups", {"label": "磁力仪 C", "copy_from": "missing"}, "复制来源装置 missing 未登记"),
            ("PUT", "/api/setups/active", {"setup_id": "missing"}, "装置 missing 未登记"),
            ("PATCH", "/api/setups/missing", {"label": "未知装置"}, "装置 missing 未登记"),
        )
        for method, url, fields, message in cases:
            with self.subTest(method=method, fields=fields):
                response = self.client.request(method, url, json={"base_revision": registry["revision"], **fields})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {"detail": message})
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(self.manager.hardware_lock.locked())
        self.assertEqual(self.registry(), registry)

    def test_archived_setup_cannot_become_active(self):
        registry, setup_id = self.create_setup()
        archived = self.client.patch(f"/api/setups/{setup_id}", json={
            "base_revision": registry["revision"], "archived": True,
        })
        self.assertEqual(archived.status_code, 200, archived.text)
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        response = self.client.put("/api/setups/active", json={
            "setup_id": setup_id, "base_revision": archived.json()["revision"],
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json(), {"detail": f"装置 {setup_id} 已归档，不能设为当前装置"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.registry(), archived.json())

    def test_strict_bodies_reject_unknown_fields_and_non_boolean_archive(self):
        registry, setup_id = self.create_setup()
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        cases = [
            ("PUT", "/api/setups/active", {"setup_id": setup_id, "unknown": True}),
            ("POST", "/api/setups", {"label": "磁力仪 C", "copy_from": "default", "unknown": True}),
            ("PATCH", f"/api/setups/{setup_id}", {"label": "备用装置", "unknown": True}),
        ]
        cases.extend(("PATCH", f"/api/setups/{setup_id}", {"archived": value}) for value in ("true", "false", 0, 1))
        for method, url, fields in cases:
            with self.subTest(method=method, fields=fields):
                response = self.client.request(method, url, json={"base_revision": registry["revision"], **fields})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.registry(), registry)

    def test_invalid_registry_get_returns_chinese_422_detail(self):
        (self.root / "params" / "setups.yaml").write_text("schema_version: 99\n", encoding="utf-8")
        response = self.client.get("/api/setups")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json(), {"detail": "setups.yaml 的 schema_version 必须为 1"})

    def test_delete_is_not_available(self):
        registry, setup_id = self.create_setup()
        path = self.root / "params" / "setups.yaml"
        before = path.read_bytes()
        for url in ("/api/setups", f"/api/setups/{setup_id}"):
            with self.subTest(url=url):
                response = self.client.delete(url)
                self.assertEqual(response.status_code, 405, response.text)
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.registry(), registry)


if __name__ == "__main__":
    unittest.main()
