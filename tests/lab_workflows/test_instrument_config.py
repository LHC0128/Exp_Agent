from copy import deepcopy
from contextlib import nullcontext
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

import lab_workflows.instrument_config as instrument_config
from lockin_amplifier import HF2Instrument
from lab_workflows.instrument_config import (
    RevisionConflict,
    load_device_document,
    load_mapping_document,
    public_device_library,
    public_physical_mappings,
    resolve_mapping,
    save_device_library,
    save_physical_mappings,
    discover_visa_devices,
)


class InstrumentConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd()
        self.devices = {
            "schema_version": 1,
            "devices": {
                "dg_a": {
                    "instrument": "signal_generator",
                    "model": "DG4000",
                    "label": "A",
                    "resource": "USB0::A::INSTR",
                    "reference_clock": "EXT",
                    "connection": {},
                    "capabilities": {"channels": [1, 2]},
                },
                "dg_b": {
                    "instrument": "signal_generator",
                    "model": "DG900",
                    "label": "B",
                    "resource": "USB0::B::INSTR",
                    "reference_clock": "INT",
                    "connection": {},
                    "capabilities": {"channels": [1, 2]},
                },
                "hf2_lab": {
                    "instrument": "lockin_amplifier",
                    "model": "HF2",
                    "label": "HF2",
                    "resource": None,
                    "reference_clock": "EXT",
                    "connection": {
                        "device_id": "dev18246",
                        "host": "127.0.0.1",
                        "port": 8005,
                    },
                    "capabilities": {"channels": [0, 1, 2, 3, 4, 5]},
                },
            },
        }
        self.mappings = {
            "schema_version": 2,
            "mapping": {
                "x": {
                    "instrument": "signal_generator",
                    "device_id": "dg_a",
                    "endpoint": {"kind": "channel", "index": 1},
                    "label": "X",
                },
                "rf": {
                    "instrument": "signal_generator",
                    "device_id": "dg_a",
                    "endpoint": {"kind": "channel", "index": 2},
                    "label": "RF",
                },
                "lockin_r": {
                    "instrument": "lockin_amplifier",
                    "device_id": "hf2_lab",
                    "endpoint": {"kind": "demod", "index": 0},
                    "label": "R",
                },
            },
            "constraints": {
                "shared_channel_groups": {},
                "colocation_groups": {"xy": ["x", "rf"]},
            },
        }
        self.documents = {
            "devices.yaml": deepcopy(self.devices),
            "mapping.yaml": deepcopy(self.mappings),
        }

        def load_yaml(path: Path):
            return deepcopy(self.documents[path.name])

        def atomic_write(path: Path, payload):
            self.documents[path.name] = deepcopy(dict(payload))

        self.load_patcher = patch.object(
            instrument_config, "_load_yaml", side_effect=load_yaml
        )
        self.lock_patcher = patch.object(
            instrument_config, "_process_config_lock", side_effect=lambda root=None: nullcontext()
        )
        self.original_atomic_write = instrument_config._atomic_write
        self.write_patcher = patch.object(
            instrument_config, "_atomic_write", side_effect=atomic_write
        )
        self.load_patcher.start()
        self.lock_patcher.start()
        self.write_patcher.start()

    def tearDown(self):
        self.write_patcher.stop()
        self.lock_patcher.stop()
        self.load_patcher.stop()

    def test_resolve_mapping_merges_device_and_endpoint(self):
        mapping = resolve_mapping(self.root)
        self.assertEqual(mapping["x"]["resource"], "USB0::A::INSTR")
        self.assertEqual(mapping["x"]["model"], "DG4000")
        self.assertEqual(mapping["x"]["channel"], 1)
        self.assertEqual(mapping["x"]["reference_clock"], "EXT")
        self.assertEqual(mapping["x"]["device_library_id"], "dg_a")
        self.assertEqual(mapping["x"]["device_id"], "dg_a")
        self.assertEqual(mapping["lockin_r"]["device_library_id"], "hf2_lab")
        self.assertEqual(mapping["lockin_r"]["device_id"], "dev18246")
        self.assertEqual(mapping["lockin_r"]["demod_idx"], 0)

    def test_resolved_hf2_id_builds_labone_node_path(self):
        class FakeDAQ:
            def __init__(self):
                self.calls = []

            def setInt(self, path, value):
                self.calls.append((path, value))

            def sync(self):
                self.calls.append(("sync",))

        config = resolve_mapping(self.root)["lockin_r"]
        instrument = HF2Instrument(device_id=config["device_id"])
        daq = FakeDAQ()
        instrument._daq = daq

        instrument.set_extclk(True)

        self.assertEqual(instrument.device_id, "dev18246")
        self.assertEqual(
            daq.calls,
            [("/dev18246/system/extclk", 1), ("sync",)],
        )

    def test_stale_revision_is_rejected(self):
        with self.assertRaises(RevisionConflict):
            save_device_library(
                self.devices["devices"],
                base_revision="stale",
                root=self.root,
            )

    def test_referenced_device_cannot_be_deleted(self):
        current = public_device_library(self.root)
        with self.assertRaisesRegex(ValueError, "未知设备"):
            save_device_library(
                {"dg_b": self.devices["devices"]["dg_b"]},
                base_revision=current["revision"],
                root=self.root,
            )

    def test_colocation_group_rejects_split_devices(self):
        current = public_physical_mappings(self.root)
        mapping = current["mapping"]
        mapping["rf"]["device_id"] = "dg_b"
        with self.assertRaisesRegex(ValueError, "同机组"):
            save_physical_mappings(
                mapping,
                current["constraints"],
                base_revision=current["revision"],
                root=self.root,
            )

    def test_shared_channel_requires_explicit_group(self):
        current = public_physical_mappings(self.root)
        mapping = current["mapping"]
        mapping["rf"]["endpoint"] = {"kind": "channel", "index": 1}
        with self.assertRaisesRegex(ValueError, "冲突占用"):
            save_physical_mappings(
                mapping,
                current["constraints"],
                base_revision=current["revision"],
                root=self.root,
            )
        constraints = current["constraints"]
        constraints["shared_channel_groups"] = {"shared": ["x", "rf"]}
        saved = save_physical_mappings(
            mapping,
            constraints,
            base_revision=current["revision"],
            root=self.root,
        )
        self.assertIn("revision", saved)
        self.assertEqual(load_mapping_document(self.root)["schema_version"], 2)
        self.assertEqual(load_device_document(self.root)["schema_version"], 1)

    def test_legacy_inline_mapping_still_resolves(self):
        self.documents["mapping.yaml"] = {
                "mapping": {
                    "legacy": {
                        "instrument": "signal_generator",
                        "model": "DG4000",
                        "resource": "USB0::LEGACY::INSTR",
                        "channel": 2,
                    }
                }
            }
        mapping = resolve_mapping(self.root)
        self.assertEqual(mapping["legacy"]["channel"], 2)
        self.assertEqual(mapping["legacy"]["model"], "DG4000")

    def test_atomic_write_uses_temporary_replace(self):
        path = MagicMock(spec=Path)
        temporary = MagicMock(spec=Path)
        path.with_suffix.return_value = temporary
        path.suffix = ".yaml"
        content = yaml.safe_dump({"schema_version": 1}, sort_keys=False)
        with patch("lab_workflows.instrument_config.yaml.safe_dump", return_value=content):
            self.original_atomic_write(path, {"schema_version": 1})
        temporary.write_text.assert_called_once_with(content, encoding="utf-8")
        temporary.replace.assert_called_once_with(path)

    def test_visa_discovery_recognizes_keithley_6221(self):
        resource = MagicMock()
        resource.query.return_value = (
            "KEITHLEY INSTRUMENTS INC.,MODEL 6221,1234567,A13"
        )
        manager = MagicMock()
        manager.list_resources.return_value = ("USB0::6221::INSTR",)
        manager.open_resource.return_value = resource
        with patch("pyvisa.ResourceManager", return_value=manager):
            result = discover_visa_devices()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["devices"][0]["instrument"], "keithley_6221")
        self.assertEqual(result["devices"][0]["model"], "6221")
        resource.close.assert_called_once()
        manager.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
