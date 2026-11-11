"""装置登记与生效装置解析的无硬件回归：惰性默认、任务绑定、写入保护与跨装置设备校验。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
import os
import unittest

import yaml

from lab_workflows import setups
from lab_workflows.instrument_config import (
    RevisionConflict, instrument_config_snapshot, physical_mapping_path, public_device_library,
    public_physical_mappings, resolve_mapping, save_device_library, save_physical_mappings,
)
from lab_workflows.setups import (
    SETUP_ENV_VAR, SetupConfigError, bound_setup, bound_setup_id, capture_setup,
    create_setup, effective_setup, load_setup_registry, set_active_setup,
    subprocess_environment, update_setup,
)
from lab_workflows.wiring import WiringStorageError, read_wiring_snapshot


class SetupRegistryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(SETUP_ENV_VAR, None)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "params").mkdir()
        self.devices = {
            "source_a": {"instrument": "signal_generator", "model": "DG4000", "label": "A 信号源",
                         "resource": "TCPIP0::192.0.2.31::INSTR", "capabilities": {"channels": [1, 2]}},
            "source_b": {"instrument": "signal_generator", "model": "DG4000", "label": "B 信号源",
                         "resource": "TCPIP0::192.0.2.32::INSTR", "capabilities": {"channels": [1, 2]}},
        }
        self.write("devices.yaml", {"devices": self.devices})
        self.write("mapping.yaml", self.mapping("source_a"))

    def write(self, name, payload):
        path = self.root / "params" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    @staticmethod
    def mapping(device_id):
        return {"schema_version": 2, "constraints": {},
                "mapping": {"Z_magnetic_field": {"instrument": "signal_generator", "device_id": device_id,
                                                 "endpoint": {"kind": "channel", "index": 1}}}}

    def second_setup(self):
        registry = create_setup("磁力仪 B", copy_from="default", setup_id="mag_b",
                                base_revision=load_setup_registry(self.root).revision, root=self.root)
        path = self.root / "params" / "setups" / "mag_b" / "mapping.yaml"
        path.write_text(yaml.safe_dump(self.mapping("source_b"), allow_unicode=True), encoding="utf-8")
        return registry

    def test_without_registry_everything_resolves_to_legacy_mapping_unchanged(self):
        registry = load_setup_registry(self.root)
        self.assertFalse(registry.stored)
        self.assertEqual(registry.active, "default")
        self.assertEqual(physical_mapping_path(self.root), self.root / "params" / "mapping.yaml")
        self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"], "source_a")
        self.assertFalse((self.root / "params" / "setups.yaml").exists())

    def test_create_copies_mapping_writes_registry_and_keeps_active(self):
        before = (self.root / "params" / "mapping.yaml").read_bytes()
        registry = create_setup("磁力仪 B", copy_from="default", setup_id="mag_b",
                                base_revision=load_setup_registry(self.root).revision, root=self.root)
        self.assertTrue(registry.stored)
        self.assertEqual(registry.active, "default")
        self.assertEqual(set(registry.setups), {"default", "mag_b"})
        self.assertEqual(registry.setups["default"].mapping_file, "params/mapping.yaml")
        copied = self.root / "params" / "setups" / "mag_b" / "mapping.yaml"
        self.assertEqual(copied.read_bytes(), before)
        self.assertEqual((self.root / "params" / "mapping.yaml").read_bytes(), before)

    def test_switching_active_changes_resolution_and_snapshot(self):
        registry = self.second_setup()
        set_active_setup("mag_b", base_revision=registry.revision, root=self.root)
        self.assertEqual(effective_setup(self.root).id, "mag_b")
        self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"], "source_b")
        snapshot = instrument_config_snapshot(self.root)
        self.assertEqual(snapshot["resolved_mapping"]["Z_magnetic_field"]["device_library_id"], "source_b")

    def test_bound_setup_wins_over_active_and_is_isolated_per_thread(self):
        registry = self.second_setup()
        set_active_setup("mag_b", base_revision=registry.revision, root=self.root)
        seen = {}
        with bound_setup("default"):
            self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"], "source_a")
            # 新线程不继承上下文：未绑定的线程按当前装置解析，任务线程须显式绑定。
            worker = Thread(target=lambda: seen.update(
                device=resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"]))
            worker.start(); worker.join()
        self.assertEqual(seen["device"], "source_b")
        self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"], "source_b")

    def test_environment_binding_overrides_active_and_trims_whitespace(self):
        self.second_setup()
        with patch.dict(os.environ, {SETUP_ENV_VAR: "  mag_b \t"}):
            self.assertEqual(bound_setup_id(), "mag_b")
            self.assertEqual(capture_setup(self.root).id, "mag_b")
            self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["device_library_id"], "source_b")
        self.assertEqual(effective_setup(self.root).id, "default")

    def test_context_binding_overrides_environment_and_restores_it(self):
        self.second_setup()
        with patch.dict(os.environ, {SETUP_ENV_VAR: "mag_b"}):
            with bound_setup("default"):
                self.assertEqual(bound_setup_id(), "default")
                self.assertEqual(effective_setup(self.root).id, "default")
            self.assertEqual(effective_setup(self.root).id, "mag_b")

    def test_unknown_environment_binding_fails_instead_of_falling_back(self):
        for setup_id in ("missing", "not a valid id"):
            with self.subTest(setup_id=setup_id), patch.dict(os.environ, {SETUP_ENV_VAR: setup_id}):
                with self.assertRaisesRegex(SetupConfigError, "未登记"):
                    effective_setup(self.root)

    def test_blank_environment_is_unbound(self):
        with patch.dict(os.environ, {SETUP_ENV_VAR: " \t "}):
            self.assertIsNone(bound_setup_id())
            self.assertEqual(effective_setup(self.root).id, "default")

    def test_subprocess_environment_copies_and_overrides_with_binding(self):
        base = {"PATH": "keep", SETUP_ENV_VAR: "inherited"}
        with bound_setup("mag_b"):
            environment = subprocess_environment(base)
        self.assertEqual(environment, {"PATH": "keep", SETUP_ENV_VAR: "mag_b"})
        self.assertEqual(base[SETUP_ENV_VAR], "inherited")
        self.assertIsNot(environment, base)
        with patch.dict(os.environ, {SETUP_ENV_VAR: " mag_b "}):
            self.assertEqual(subprocess_environment({})[SETUP_ENV_VAR], "mag_b")

    def test_subprocess_environment_without_binding_preserves_base(self):
        for base in ({"PATH": "keep"}, {SETUP_ENV_VAR: "inherited"}):
            with self.subTest(base=base):
                environment = subprocess_environment(base)
                self.assertEqual(environment, base)
                self.assertIsNot(environment, base)

    def test_snapshot_resolves_setup_once_and_keeps_existing_values(self):
        registry = self.second_setup()
        expected = instrument_config_snapshot(self.root)
        with patch.object(setups, "effective_setup", side_effect=[
            registry.setups["default"], registry.setups["mag_b"],
        ]) as resolve:
            snapshot = instrument_config_snapshot(self.root)
        resolve.assert_called_once_with(self.root)
        self.assertEqual(snapshot, expected)
        self.assertEqual(snapshot["setup_id"], "default")
        self.assertEqual(snapshot["setup_label"], "默认装置")
        with bound_setup("mag_b"):
            snapshot = instrument_config_snapshot(self.root)
        self.assertEqual(snapshot["setup_id"], "mag_b")
        self.assertEqual(snapshot["setup_label"], "磁力仪 B")
        self.assertEqual(snapshot["physical_mappings"]["mapping"]["Z_magnetic_field"]["device_id"], "source_b")
        self.assertEqual(snapshot["resolved_mapping"]["Z_magnetic_field"]["device_library_id"], "source_b")

    def test_bound_unknown_setup_fails_instead_of_falling_back(self):
        with bound_setup("missing"):
            with self.assertRaisesRegex(SetupConfigError, "missing"):
                resolve_mapping(self.root)

    def test_mapping_save_writes_only_effective_setup_file(self):
        registry = self.second_setup()
        set_active_setup("mag_b", base_revision=registry.revision, root=self.root)
        legacy = (self.root / "params" / "mapping.yaml").read_bytes()
        current = public_physical_mappings(self.root)
        current["mapping"]["Z_magnetic_field"]["endpoint"] = {"kind": "channel", "index": 2}
        save_physical_mappings(current["mapping"], current["constraints"],
                               base_revision=current["revision"], root=self.root)
        self.assertEqual((self.root / "params" / "mapping.yaml").read_bytes(), legacy)
        self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["channel"], 2)

    def test_device_library_change_must_keep_every_setup_valid(self):
        self.second_setup()  # 当前装置仍为 default，B 装置引用 source_b
        library = public_device_library(self.root)
        devices = dict(library["devices"]); devices.pop("source_b")
        with self.assertRaisesRegex(ValueError, "装置 mag_b"):
            save_device_library(devices, base_revision=library["revision"], root=self.root)
        self.assertIn("source_b", public_device_library(self.root)["devices"])

    def test_revision_conflicts_and_archive_rules(self):
        registry = self.second_setup()
        with self.assertRaises(RevisionConflict):
            set_active_setup("mag_b", base_revision="stale", root=self.root)
        with self.assertRaisesRegex(SetupConfigError, "当前装置不能归档"):
            update_setup("default", archived=True, base_revision=registry.revision, root=self.root)
        registry = update_setup("mag_b", archived=True, label="磁力仪 B（停用）",
                                base_revision=registry.revision, root=self.root)
        self.assertTrue(registry.setups["mag_b"].archived)
        with self.assertRaisesRegex(SetupConfigError, "已归档"):
            set_active_setup("mag_b", base_revision=registry.revision, root=self.root)
        self.assertTrue((self.root / "params" / "setups" / "mag_b" / "mapping.yaml").exists())

    def test_create_rejects_duplicates_and_existing_files(self):
        registry = self.second_setup()
        with self.assertRaisesRegex(SetupConfigError, "已被使用"):
            create_setup("磁力仪 B", copy_from="default", base_revision=registry.revision, root=self.root)
        stray = self.root / "params" / "setups" / "mag_c" / "mapping.yaml"
        stray.parent.mkdir(parents=True); stray.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(SetupConfigError, "拒绝覆盖"):
            create_setup("磁力仪 C", copy_from="default", setup_id="mag_c",
                         base_revision=registry.revision, root=self.root)
        self.assertEqual(stray.read_text(encoding="utf-8"), "keep")
        self.assertEqual(set(load_setup_registry(self.root).setups), {"default", "mag_b"})

    def test_corrupt_or_unsafe_registry_is_reported_not_defaulted(self):
        cases = [
            b"[",
            yaml.safe_dump({"schema_version": 1, "active": "a", "setups": {"a": {"label": "A", "mapping_file": "../x.yaml"}}}).encode(),
            yaml.safe_dump({"schema_version": 1, "active": "a", "setups": {"a": {"label": "A", "mapping_file": "C:/x.yaml"}}}).encode(),
            yaml.safe_dump({"schema_version": 1, "active": "b", "setups": {"a": {"label": "A", "mapping_file": "params/mapping.yaml"}}}).encode(),
            yaml.safe_dump({"schema_version": 1, "active": "a", "setups": {
                "a": {"label": "A", "mapping_file": "params/mapping.yaml"},
                "b": {"label": "B", "mapping_file": "params/MAPPING.yaml"}}}).encode(),
        ]
        for raw in cases:
            with self.subTest(raw=raw[:60]):
                (self.root / "params" / "setups.yaml").write_bytes(raw)
                with self.assertRaises(SetupConfigError):
                    physical_mapping_path(self.root)

    def test_wiring_follow_reads_effective_setup_mapping_and_reports_bad_registry(self):
        legacy = read_wiring_snapshot(self.root).revision
        registry = self.second_setup()
        self.assertEqual(read_wiring_snapshot(self.root).revision, legacy)
        set_active_setup("mag_b", base_revision=registry.revision, root=self.root)
        self.assertNotEqual(read_wiring_snapshot(self.root).revision, legacy)
        (self.root / "params" / "setups.yaml").write_bytes(b"[")
        with self.assertRaises(WiringStorageError):
            read_wiring_snapshot(self.root)


if __name__ == "__main__":
    unittest.main()
