"""设备端口档案的纯模型与临时目录保存回归。"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import yaml

from pydantic import ValidationError

from lab_workflows.device_ports import (
    DevicePortProfile, DevicePortsBody, DevicePortsResponse, DevicePortsStorageError, PhysicalPort,
    RevisionConflict, device_port_presets, effective_port_direction,
    read_device_ports, save_device_ports,
)


def profile_data() -> dict:
    """构造与设备库解绑也合法的设备档案。"""
    return {
        "id": "device-stable", "name": "信号源", "model": "DG4000",
        "device_library_id": "removed-library-device",
        "ports": [device_port_presets()[0].ports[-1].model_dump()],
    }


class DevicePortPresetTests(unittest.TestCase):
    def test_confirmed_presets_have_only_signal_ports_and_expected_directions(self):
        presets = {item.model: item for item in device_port_presets()}
        self.assertEqual(set(presets), {"DG4000", "DG900", "HF2", "MFLI"})
        self.assertEqual({key: len(item.ports) for key, item in presets.items()}, {
            "DG4000": 7, "DG900": 5, "HF2": 16, "MFLI": 13,
        })
        for preset in presets.values():
            for port in preset.ports:
                connector = "" if preset.model == "MFLI" else "BNC"
                self.assertEqual((port.connector, port.signal_type), (connector, "electrical"))
                self.assertIsNotNone(port.direction)
        dg4000 = {port.id: port for port in presets["DG4000"].ports}
        self.assertEqual({key: port.direction for key, port in dg4000.items()}, {
            "ch1_out": "out", "ch2_out": "out", "ch1_sync": "out", "ch2_sync": "out",
            "ch1_external": "in", "ch2_external": "in", "clock_10mhz": "switchable",
        })
        for model in ("DG4000", "DG900"):
            clock = next(port for port in presets[model].ports if port.id == "clock_10mhz")
            self.assertEqual([(mode.id, mode.name, mode.direction) for mode in clock.modes], [
                ("internal", "内部参考时钟", "out"), ("external", "外部参考时钟", "in"),
            ])
            self.assertIsNone(clock.selected_mode)
            self.assertIsNone(effective_port_direction(clock))
        for port in presets["DG900"].ports:
            if port.id in {"ch1_multi", "ch2_multi"}:
                self.assertEqual(port.direction, "switchable")
                self.assertEqual([(mode.id, mode.name, mode.direction) for mode in port.modes], [
                    ("sync", "同步", "out"), ("trigger", "触发", "in"),
                    ("gate", "门控", "in"), ("am", "AM", "in"), ("fm", "FM", "in"),
                ])
                self.assertIsNone(port.selected_mode)
        hf2 = {port.id: port for port in presets["HF2"].ports}
        self.assertEqual(Counter(port.direction for port in hf2.values()), {"in": 7, "out": 9})
        self.assertEqual((hf2["clock_in"].direction, hf2["clock_out"].direction), ("in", "out"))
        for channel in (1, 2):
            group = [port for port in hf2.values() if port.group == f"signal_output_{channel}"]
            self.assertEqual({port.name: port.direction for port in group}, {
                "ADD": "in", "Output": "out", "Sync": "out",
            })
            self.assertEqual(next(port.purpose for port in group if port.name == "Output"), "内部设置输出＋ADD输入")
            inputs = [port for port in hf2.values() if port.group == f"signal_input_{channel}"]
            self.assertEqual({port.name: port.direction for port in inputs}, {"IN": "in", "Diff": "in"})

    def test_mod_am_fm_presets_keep_stable_ids_and_select_one_direction(self):
        presets = {preset.id: preset for preset in device_port_presets()}
        for channel in (1, 2):
            external = next(port for port in presets["dg4000"].ports if port.id == f"ch{channel}_external")
            self.assertEqual(external.name, f"CH{channel} External")
            self.assertIn("MOD（AM / FM）", external.purpose)
            self.assertEqual((external.direction, external.modes, external.selected_mode), ("in", [], None))
            multi = next(port for port in presets["dg900"].ports if port.id == f"ch{channel}_multi")
            self.assertEqual(multi.name, f"CH{channel} Multi")
            self.assertIn("MOD（AM / FM）", multi.purpose)
            self.assertEqual([(mode.id, mode.name, mode.direction) for mode in multi.modes], [
                ("sync", "同步", "out"), ("trigger", "触发", "in"), ("gate", "门控", "in"),
                ("am", "AM", "in"), ("fm", "FM", "in"),
            ])
            self.assertIsNone(effective_port_direction(multi))
            for mode in multi.modes:
                multi.selected_mode = mode.id
                self.assertEqual(effective_port_direction(multi), mode.direction)
                self.assertIn(effective_port_direction(multi), {"in", "out"})
            self.assertEqual(multi.id, f"ch{channel}_multi")
            self.assertTrue(all(port.direction != "inout" and all(mode.direction != "inout" for mode in port.modes)
                                for preset in presets.values() for port in preset.ports))

    def test_mfli_has_only_thirteen_confirmed_electrical_ports(self):
        mfli = next(item for item in device_port_presets() if item.id == "mfli")
        self.assertEqual((mfli.name, mfli.model), ("MFLI", "MFLI"))
        self.assertEqual(len(mfli.ports), 13)
        self.assertEqual(Counter(port.direction for port in mfli.ports), {"in": 6, "out": 7})
        self.assertEqual({port.name: port.direction for port in mfli.ports if port.group == "参考时钟"}, {
            "10 MHz In": "in", "10 MHz Out": "out",
        })
        self.assertEqual({port.name: port.direction for port in mfli.ports if port.group == "Signal Input"}, {
            "I in": "in", "V": "in", "Vdiff": "in",
        })
        self.assertEqual({port.name: port.direction for port in mfli.ports if port.group == "Signal Output"}, {
            "V": "out", "Vdiff": "out",
        })
        self.assertEqual({port.name for port in mfli.ports if port.group == "辅助输入"}, {"Aux Input 1", "Aux Input 2"})
        self.assertEqual({port.name for port in mfli.ports if port.group == "辅助输出"}, {
            "Aux Out 1", "Aux Out 2", "Aux Out 3", "Aux Out 4",
        })
        for port in mfli.ports:
            self.assertEqual((port.signal_type, port.connector, port.modes, port.selected_mode), ("electrical", "", [], None))


class DevicePortModelTests(unittest.TestCase):
    def test_unknown_information_and_manual_modes_preserve_stable_ids(self):
        payload = profile_data()
        payload["ports"][0].update(direction=None, signal_type=None, modes=[], selected_mode=None)
        payload["model"] = ""
        payload["ports"][0].update(group="", connector="", purpose="")
        item = DevicePortProfile.model_validate(payload)
        self.assertIsNone(effective_port_direction(item.ports[0]))
        item.name = "新名称"
        item.ports[0].name = "新端口名"
        self.assertEqual((item.id, item.ports[0].id, item.device_library_id), (
            "device-stable", "clock_10mhz", "removed-library-device",
        ))
        switchable = PhysicalPort.model_validate(profile_data()["ports"][0])
        self.assertIsNone(effective_port_direction(switchable))
        switchable.selected_mode = "external"
        self.assertEqual(effective_port_direction(switchable), "in")
        switchable.selected_mode = "internal"
        self.assertEqual(effective_port_direction(switchable.model_dump()), "out")
        self.assertEqual(switchable.selected_mode, "internal")

    def test_unknown_fields_types_duplicate_ids_and_invalid_modes_are_rejected(self):
        valid = {"base_revision": "revision", "devices": [profile_data()]}
        self.assertEqual(DevicePortsBody.model_validate(valid).devices[0].id, "device-stable")
        invalid = []

        def add_change(path, value):
            candidate = deepcopy(valid)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            invalid.append(candidate)

        add_change(["unknown"], True)
        add_change(["base_revision"], 3)
        add_change(["devices"], "not-a-list")
        for field, value in (("unknown", "x"), ("id", 4), ("id", " "), ("name", ""),
                             ("model", 4), ("device_library_id", ""), ("device_library_id", 3)):
            add_change(["devices", 0, field], value)
        for field, value in (("unknown", "x"), ("name", " "), ("group", 1),
                             ("signal_type", "unknown"), ("direction", "unknown"),
                             ("connector", False), ("purpose", {}), ("modes", []),
                             ("selected_mode", "missing"), ("selected_mode", 1)):
            add_change(["devices", 0, "ports", 0, field], value)
        for field, value in (("unknown", "x"), ("name", ""), ("direction", "switchable"),
                             ("id", 1), ("name", False)):
            add_change(["devices", 0, "ports", 0, "modes", 0, field], value)
        add_change(["devices", 0, "ports", 0, "direction"], "out")
        duplicated = deepcopy(valid)
        duplicated["devices"].append(deepcopy(duplicated["devices"][0]))
        invalid.append(duplicated)
        duplicate_library = deepcopy(duplicated)
        duplicate_library["devices"][1]["id"] = "other-device"
        invalid.append(duplicate_library)
        add_change(["devices", 0, "ports"], [valid["devices"][0]["ports"][0]] * 2)
        add_change(["devices", 0, "ports", 0, "modes"], [valid["devices"][0]["ports"][0]["modes"][0]] * 2)
        missing_required = deepcopy(valid)
        del missing_required["devices"][0]["ports"][0]["selected_mode"]
        invalid.append(missing_required)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                DevicePortsBody.model_validate(candidate)

    def test_effective_direction_skips_revalidation_for_existing_models(self):
        switchable = PhysicalPort.model_validate(profile_data()["ports"][0])
        with patch.object(PhysicalPort, "model_validate", side_effect=AssertionError("不应重复校验已校验模型")):
            self.assertIsNone(effective_port_direction(switchable))
            switchable.selected_mode = "external"
            self.assertEqual(effective_port_direction(switchable), "in")
        self.assertEqual(effective_port_direction(switchable.model_dump()), "in")

    def test_response_schema_version_does_not_coerce_boolean_or_float(self):
        valid = {"schema_version": 1, "revision": "revision", "devices": [], "presets": []}
        self.assertEqual(DevicePortsResponse.model_validate(valid).schema_version, 1)
        for value in (True, 1.0, "1", 2):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                DevicePortsResponse.model_validate({**valid, "schema_version": value})

    def test_presets_and_caller_owned_profiles_are_independent(self):
        first = device_port_presets()
        first[0].ports[-1].modes[0].name = "修改模式名称"
        first[0].ports.pop(0)
        second = device_port_presets()
        self.assertEqual(len(second[0].ports), 7)
        self.assertEqual(second[0].ports[-1].modes[0].name, "内部参考时钟")
        self.assertEqual(second[1].ports[-1].modes[0].name, "内部参考时钟")
        data = profile_data()
        profile = DevicePortProfile.model_validate(data)
        data["ports"][0]["modes"][0]["name"] = "调用方修改"
        self.assertEqual(profile.ports[0].modes[0].name, "内部参考时钟")


class DevicePortStorageTests(unittest.TestCase):
    def test_missing_read_does_not_create_files_and_roundtrip_preserves_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = read_device_ports(root)
            self.assertEqual(initial.devices, [])
            self.assertEqual(initial.schema_version, 1)
            self.assertEqual(initial.revision, hashlib.sha256(b"").hexdigest())
            self.assertEqual(list(root.iterdir()), [])
            request = DevicePortsBody(base_revision=initial.revision, devices=[profile_data()])
            saved = save_device_ports(root, request)
            path = root / "params" / "device_ports.yaml"
            self.assertTrue(path.is_file())
            self.assertEqual(saved, read_device_ports(root))
            self.assertEqual(saved.revision, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(saved.devices[0].device_library_id, "removed-library-device")
            original_ids = (saved.devices[0].id, saved.devices[0].ports[0].id)
            saved.devices[0].name = "重命名设备"
            saved.devices[0].ports[0].name = "重命名端口"
            saved.devices[0].ports[0].selected_mode = "external"
            updated = save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=saved.devices))
            self.assertNotEqual(updated.revision, saved.revision)
            self.assertEqual((updated.devices[0].id, updated.devices[0].ports[0].id), original_ids)
            self.assertEqual(updated.devices[0].ports[0].selected_mode, "external")
            saved.devices[0].name = "只修改旧响应"
            self.assertEqual(read_device_ports(root).devices[0].name, "重命名设备")

    def test_mfli_preset_does_not_replace_existing_user_profile(self):
        with TemporaryDirectory() as directory:
            custom = profile_data()
            custom.update(id="mfli", name="用户原有 MFLI", model="MFLI")
            custom["ports"][0]["name"] = "用户人工端口"
            saved = save_device_ports(directory, DevicePortsBody(
                base_revision=read_device_ports(directory).revision, devices=[custom],
            ))
            loaded = read_device_ports(directory)
            self.assertEqual(loaded, saved)
            self.assertEqual([device.model_dump() for device in loaded.devices], [custom])
            self.assertEqual(len(next(preset for preset in loaded.presets if preset.id == "mfli").ports), 13)

    def test_legacy_inout_port_and_mode_roundtrip_are_not_tightened(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = profile_data()
            switchable = legacy["ports"][0]
            switchable["modes"][0].update(name="旧双向模式", direction="inout")
            switchable["selected_mode"] = "internal"
            fixed = deepcopy(switchable)
            fixed.update(id="legacy-inout", name="旧双向端口", direction="inout", modes=[], selected_mode=None)
            legacy["ports"].append(fixed)
            saved = save_device_ports(root, {"base_revision": read_device_ports(root).revision, "devices": [legacy]})
            loaded = read_device_ports(root)
            self.assertEqual(loaded, saved)
            self.assertEqual(loaded.devices[0].model_dump(), legacy)
            self.assertEqual([effective_port_direction(port) for port in loaded.devices[0].ports], ["inout", "inout"])
            path = root / "params" / "device_ports.yaml"
            before = path.read_bytes(), path.stat().st_mtime_ns
            self.assertEqual(read_device_ports(root), loaded)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            rewritten = save_device_ports(root, {"base_revision": loaded.revision, "devices": loaded.devices})
            self.assertEqual(rewritten.devices, loaded.devices)
            self.assertEqual(path.read_bytes(), before[0])

    def test_stale_revision_and_concurrent_updates_have_one_winner(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = read_device_ports(root)
            saved = save_device_ports(root, DevicePortsBody(base_revision=initial.revision, devices=[profile_data()]))
            with self.assertRaises(RevisionConflict):
                save_device_ports(root, DevicePortsBody(base_revision=initial.revision, devices=[]))
            gate = threading.Barrier(2)

            def save_competing(name):
                candidate = profile_data()
                candidate["name"] = name
                gate.wait(timeout=5)
                try:
                    return save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=[candidate]))
                except RevisionConflict as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(save_competing, ["并发甲", "并发乙"]))
            self.assertEqual(sum(isinstance(item, RevisionConflict) for item in results), 1)
            winner = next(item for item in results if not isinstance(item, RevisionConflict))
            self.assertEqual(read_device_ports(root), winner)

    def test_external_change_between_revision_check_and_replace_is_conflict(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_device_ports(root, DevicePortsBody(base_revision=read_device_ports(root).revision, devices=[profile_data()]))
            path = root / "params" / "device_ports.yaml"
            external = profile_data()
            external.update(id="external-device", name="外部写入")
            external_bytes = yaml.safe_dump(
                {"schema_version": 1, "devices": [external]}, allow_unicode=True, sort_keys=False,
            ).encode("utf-8")
            real_read = Path.read_bytes
            reads = 0

            def change_after_revision_check(candidate):
                nonlocal reads
                if candidate == path:
                    reads += 1
                    if reads == 2:
                        path.write_bytes(external_bytes)
                return real_read(candidate)

            with patch.object(Path, "read_bytes", change_after_revision_check):
                with self.assertRaisesRegex(RevisionConflict, "保存期间被外部修改，请重新载入"):
                    save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=[]))
            self.assertEqual(reads, 2)
            self.assertEqual(path.read_bytes(), external_bytes)
            self.assertEqual(set(path.parent.iterdir()), {path})
            self.assertEqual(read_device_ports(root).devices[0].id, "external-device")

    def test_external_delete_between_revision_check_and_replace_is_conflict(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_device_ports(root, DevicePortsBody(base_revision=read_device_ports(root).revision, devices=[profile_data()]))
            path = root / "params" / "device_ports.yaml"
            real_read = Path.read_bytes
            reads = 0

            def delete_after_revision_check(candidate):
                nonlocal reads
                if candidate == path:
                    reads += 1
                    if reads == 2:
                        path.unlink()
                return real_read(candidate)

            with patch.object(Path, "read_bytes", delete_after_revision_check):
                with self.assertRaisesRegex(RevisionConflict, "保存期间被外部修改"):
                    save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=[]))
            self.assertEqual(reads, 2)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.iterdir()), [])

    def test_atomic_replace_failure_keeps_old_archive_and_cleans_only_own_temporary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_device_ports(root, DevicePortsBody(base_revision=read_device_ports(root).revision, devices=[profile_data()]))
            path = root / "params" / "device_ports.yaml"
            before = path.read_bytes()
            other_temporary = path.parent / ".device_ports.yaml.other-writer.tmp"
            other_temporary.write_bytes(b"unrelated")
            observed_temporary = []

            def fail_replace(source, target):
                source, target = Path(source), Path(target)
                self.assertEqual(source.parent, target.parent)
                self.assertNotEqual(source, other_temporary)
                self.assertTrue(source.is_file())
                self.assertEqual(target, path)
                observed_temporary.append(source)
                raise OSError("模拟原子替换失败")

            with patch("lab_workflows.device_ports.os.replace", side_effect=fail_replace):
                with self.assertRaisesRegex(DevicePortsStorageError, "写入失败"):
                    save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=[]))
            self.assertEqual(len(observed_temporary), 1)
            self.assertFalse(observed_temporary[0].exists())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(other_temporary.read_bytes(), b"unrelated")
            self.assertEqual(set(path.parent.iterdir()), {path, other_temporary})
            self.assertEqual(read_device_ports(root), saved)

    def test_cleanup_error_is_explicit_and_preserves_original_write_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_device_ports(root, DevicePortsBody(base_revision=read_device_ports(root).revision, devices=[]))
            path = root / "params" / "device_ports.yaml"
            before = path.read_bytes()
            with patch("lab_workflows.device_ports.os.replace", side_effect=OSError("原始替换错误")):
                with patch.object(Path, "unlink", side_effect=OSError("清理错误")):
                    with self.assertRaisesRegex(DevicePortsStorageError, "原始替换错误.*清理错误") as caught:
                        save_device_ports(root, DevicePortsBody(base_revision=saved.revision, devices=[]))
            self.assertIn("原始替换错误", str(caught.exception.__cause__))
            self.assertEqual(path.read_bytes(), before)

    def test_corrupt_unknown_version_and_invalid_disk_data_cannot_be_overwritten(self):
        malformed_documents = [
            b"devices: [", b"", b"\xff", b"[]\n",
            b"schema_version: 2\ndevices: []\n",
            b"schema_version: true\ndevices: []\n",
            b"schema_version: 1.0\ndevices: []\n",
            b"schema_version: 1\ndevices: []\nunknown: true\n",
        ]
        duplicated = {"schema_version": 1, "devices": [profile_data(), profile_data()]}
        malformed_documents.append(yaml.safe_dump(duplicated).encode())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            base_revision = read_device_ports(root).revision
            path = root / "params" / "device_ports.yaml"
            path.parent.mkdir()
            for content in malformed_documents:
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(DevicePortsStorageError):
                        read_device_ports(root)
                    with self.assertRaises(DevicePortsStorageError):
                        save_device_ports(root, DevicePortsBody(base_revision=base_revision, devices=[]))
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
