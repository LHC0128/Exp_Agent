"""实验目录归档与加载优化的无硬件回归。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import yaml

from GUI.backend import main
from lab_workflows.experiments import catalog
from lab_workflows.experiments.contracts import ExperimentDefinition


class ExperimentCatalogTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        self.path = self.root / "experiment_catalog.yaml"
        self.definitions = [
            ExperimentDefinition(
                id="fake-a", title="实验甲", category="measurement", family="test",
                variant="test", description="测试实验", data_type="FakeA",
                required_devices=("静态设备甲",), required_mapping_keys=("Y", "Z"),
            ),
            ExperimentDefinition(
                id="fake-b", title="实验乙", category="calibration", family="test",
                variant="test", description="测试实验", data_type="FakeB",
                required_devices=("静态设备乙",), required_mapping_keys=("Y",),
            ),
            ExperimentDefinition(
                id="fake-legacy", title="旧模式实验", category="measurement", family="test",
                variant="test", description="测试实验", data_type="FakeLegacy",
                required_devices=("静态旧设备",), execution_mode="legacy_script",
                experiment_runner=Mock(return_value={"ok": True}),
            ),
        ]
        self.categories = {item.id: item.category for item in self.definitions}
        self.by_id = {item.id: item for item in self.definitions}
        self.mapping = {
            key: {"device_id": "shared-dg", "label": "控制源", "model": "DG4000",
                  "instrument": "signal_generator", "channel": index}
            for index, key in enumerate(("Y", "Z"), 1)
        }
        self.original = {
            "schema_version": 2,
            "assignments": {"fake-a": "calibration"},
            "experiment_titles": {"fake-a": "保存的名称"},
            "experiment_descriptions": {"fake-a": "保存的介绍"},
            "parameter_layouts": {"fake-a": {"basic": ["TEMP"], "advanced": ["SCAN"]}},
        }
        self.path.write_text(yaml.safe_dump(self.original, allow_unicode=True), encoding="utf-8")
        stack.enter_context(patch.object(catalog, "catalog_path", return_value=self.path))
        stack.enter_context(patch.object(main, "ROOT", self.root))
        stack.enter_context(patch.object(main, "list_experiments", return_value=self.definitions))
        stack.enter_context(patch.object(main, "get_experiment", side_effect=self.by_id.__getitem__))
        self.mapping_loader = stack.enter_context(patch.object(main, "load_mapping", return_value=self.mapping))
        stack.enter_context(patch("lab_workflows.common.load_mapping", return_value=self.mapping))
        self.client = stack.enter_context(TestClient(main.app))

    def test_old_config_and_archive_restore_preserve_metadata(self):
        old_bytes = self.path.read_bytes()
        loaded = catalog.load_catalog(self.categories)
        self.assertEqual(loaded["schema_version"], 3)
        self.assertEqual(loaded["archived_experiments"], [])
        self.assertEqual(self.path.read_bytes(), old_bytes)
        catalog.set_experiment_archived("fake-a", True, self.categories)
        first_save = self.path.read_bytes()
        catalog.set_experiment_archived("fake-a", True, self.categories)
        self.assertEqual(self.path.read_bytes(), first_save)
        saved = catalog.load_catalog(self.categories)
        self.assertEqual(saved["archived_experiments"], ["fake-a"])
        for key in ("assignments", "experiment_titles", "experiment_descriptions", "parameter_layouts"):
            self.assertEqual(saved[key], self.original[key])
        catalog.set_experiment_archived("fake-a", False, self.categories)
        self.assertEqual(catalog.load_catalog(self.categories)["archived_experiments"], [])
        self.assertEqual(self.client.get("/api/experiments/fake-a").json()["category"], "calibration")

    def test_normalize_archive_ids_and_reject_invalid_structure(self):
        payload = {**self.original, "archived_experiments": ["fake-b", "unknown", "fake-b"]}
        self.assertEqual(catalog._normalize(payload, self.categories)["archived_experiments"], ["fake-b"])
        for invalid in (None, "fake-a", {}, [1]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                catalog._normalize({**payload, "archived_experiments": invalid}, self.categories)
        with self.assertRaises(ValueError):
            catalog.set_experiment_archived("fake-a", "true", self.categories)

    def test_archive_api_validation_and_unknown_id(self):
        base = "/api/experiments/fake-a/archive"
        for body in ({}, {"archived": None}, {"archived": "true"}, {"archived": 1},
                     {"archived": []}, {"archived": True, "unexpected": True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put(base, json=body).status_code, 422)
        self.assertEqual(self.client.put("/api/experiments/unknown/archive", json={"archived": True}).status_code, 404)
        response = self.client.put(base, json={"archived": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["experiment"]["archived"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(self.client.get("/api/experiment-tags").json()["archived_experiments"], ["fake-a"])

    def test_atomic_save_failure_keeps_old_file_and_state(self):
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("测试替换失败")):
            response = self.client.put("/api/experiments/fake-a/archive", json={"archived": True})
        self.assertEqual(response.status_code, 500)
        self.assertIn("保存归档状态失败", response.json()["detail"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix(".yaml.tmp").exists())
        self.assertFalse(self.client.get("/api/experiments/fake-a").json()["archived"])

    def test_concurrent_archive_and_metadata_writes_do_not_overwrite(self):
        save = catalog.save_catalog

        def slow_save(*args):
            sleep(0.03)
            save(*args)

        with patch.object(catalog, "save_catalog", side_effect=slow_save), ThreadPoolExecutor(max_workers=2) as pool:
            archive = pool.submit(catalog.set_experiment_archived, "fake-a", True, self.categories)
            rename = pool.submit(catalog.set_experiment_metadata, "fake-a", "新名称", "新介绍", self.categories)
            archive.result(timeout=5)
            rename.result(timeout=5)
        saved = catalog.load_catalog(self.categories)
        self.assertEqual(saved["archived_experiments"], ["fake-a"])
        self.assertEqual(saved["experiment_titles"]["fake-a"], "新名称")
        self.assertEqual(saved["parameter_layouts"], self.original["parameter_layouts"])

    def test_list_reuses_one_mapping_and_still_includes_archived(self):
        catalog.set_experiment_archived("fake-a", True, self.categories)
        with patch("lab_workflows.common.load_mapping", side_effect=AssertionError("不应逐项重复加载")) as repeated:
            response = self.client.get("/api/experiments")
        self.assertEqual(response.status_code, 200)
        self.mapping_loader.assert_called_once_with()
        repeated.assert_not_called()
        items = response.json()
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["archived"])
        self.assertEqual(items[0]["required_devices"], ["控制源 (DG4000)"])
        self.assertEqual(items[2]["required_devices"], ["静态旧设备"])
        self.mapping_loader.reset_mock()
        self.client.get("/api/experiments")
        self.mapping_loader.assert_called_once_with()

    def test_mapping_failure_falls_back_without_retrying_each_experiment(self):
        self.mapping_loader.side_effect = ValueError("测试无效映射")
        with patch("lab_workflows.common.load_mapping", side_effect=AssertionError("不应重复重试")) as repeated:
            response = self.client.get("/api/experiments")
        self.assertEqual(response.status_code, 200)
        self.mapping_loader.assert_called_once_with()
        repeated.assert_not_called()
        self.assertEqual(response.json()[0]["required_devices"], ["静态设备甲"])
        self.assertEqual(response.json()[1]["required_devices"], ["静态设备乙"])

    def test_archived_experiment_can_open_preflight_run_and_view_history(self):
        self.client.put("/api/experiments/fake-legacy/archive", json={"archived": True})
        self.assertEqual(self.client.get("/api/experiments/fake-legacy/schema").status_code, 200)
        self.assertTrue(self.client.post("/api/experiments/fake-legacy/preflight", json={"parameters": {}}).json()["ok"])
        fake_job = SimpleNamespace(public=lambda: {"id": "fake-job", "status": "queued"})
        with patch.object(main.manager, "create", return_value=fake_job) as create:
            response = self.client.post("/api/experiments/fake-legacy/runs", json={"parameters": {}})
            self.assertEqual(response.status_code, 200)
            kind, run = create.call_args.args
            self.assertEqual(kind, "experiment:fake-legacy")
            self.assertEqual(run(SimpleNamespace(cancellation=None)), {"ok": True})
        self.by_id["fake-legacy"].experiment_runner.assert_called_once()
        self.assertEqual(self.client.get("/api/runs?experiment_id=fake-legacy").status_code, 200)


if __name__ == "__main__":
    unittest.main()
