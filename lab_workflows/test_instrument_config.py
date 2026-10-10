"""统一设备库、地址迁移、映射批量保存与信号源工厂的无硬件回归。"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import yaml

from signal_generator import DG4000Instrument, DG900Instrument
from lab_workflows import clock_sync, devices, instrument_control
from lab_workflows import instrument_config
from lab_workflows.steps import clock as clock_steps
from lab_workflows.instrument_config import (
    RevisionConflict, load_device_document, public_device_library, public_physical_mappings,
    resolve_mapping, save_device_library, save_physical_mappings, validate_mapping_usage,
)


LAN = "TCPIP0::192.0.2.31::INSTR"
USB = "USB0::0x1AB1::0x0641::DG4E123::INSTR"


class InstrumentConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "params").mkdir()
        self.device_configs = {
            "usb_source": {"instrument": "signal_generator", "model": "DG4000", "label": "USB 信号源",
                           "resource": USB, "connection": {"serial_number": "DG4E123"}, "capabilities": {"channels": [1, 2]}},
            "lan_source": {"instrument": "signal_generator", "model": "DG900", "label": "网络信号源",
                           "resource": LAN, "connection": {"serial_number": "DG9Q999"}, "capabilities": {"channels": [1, 2]}},
            "narrow_source": {"instrument": "signal_generator", "model": "DG900", "label": "单通道",
                              "resource": "TCPIP0::192.0.2.32::INSTR", "capabilities": {"channels": [1]}},
        }
        self.mappings = {
            key: {"instrument": "signal_generator", "device_id": "usb_source", "endpoint": {"kind": "channel", "index": channel}}
            for key, channel in (("Z_magnetic_field", 1), ("sequence", 2), ("shared_sequence", 2))
        }
        self.constraints = {"colocation_groups": {"z_sequence": ["Z_magnetic_field", "sequence"]},
                            "shared_channel_groups": {"timing": ["sequence", "shared_sequence"]}}
        self.write("devices.yaml", {"devices": self.device_configs})
        self.write("mapping.yaml", {"schema_version": 2, "mapping": self.mappings, "constraints": self.constraints})

    def write(self, name, payload):
        (self.root / "params" / name).write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    def test_legacy_targets_migrate_and_old_save_call_preserves_them(self):
        self.write("instrument_discovery.yaml", {"network_resources": [LAN, LAN.replace("::INSTR", "::inst0::INSTR")]})
        library = public_device_library(self.root)
        self.assertEqual(library["network_resources"], [LAN])
        saved = save_device_library(library["devices"], base_revision=library["revision"], root=self.root)
        self.assertEqual(saved["network_resources"], [LAN])
        persisted = yaml.safe_load((self.root / "params" / "devices.yaml").read_text(encoding="utf-8"))
        self.assertEqual(persisted["network_resources"], [LAN])

    def test_present_field_including_empty_list_ignores_legacy_file(self):
        self.write("instrument_discovery.yaml", ["旧配置格式错误但不再读取"])
        for targets in ([], [LAN]):
            self.write("devices.yaml", {"devices": self.device_configs, "network_resources": targets})
            self.assertEqual(load_device_document(self.root)["network_resources"], targets)

    def test_clearing_targets_survives_reload_and_revision_detects_network_edits(self):
        self.write("instrument_discovery.yaml", {"network_resources": [LAN]})
        before = public_device_library(self.root)
        cleared = save_device_library(before["devices"], base_revision=before["revision"], network_resources=[], root=self.root)
        self.assertEqual(cleared["network_resources"], [])
        self.assertNotEqual(before["revision"], cleared["revision"])
        original_bytes = (self.root / "params" / "devices.yaml").read_bytes()
        with self.assertRaises(RevisionConflict):
            save_device_library(before["devices"], base_revision=before["revision"], network_resources=[LAN], root=self.root)
        self.assertEqual((self.root / "params" / "devices.yaml").read_bytes(), original_bytes)

    def test_invalid_address_save_and_referenced_device_deletion_are_atomic(self):
        document = public_device_library(self.root)
        before = (self.root / "params" / "devices.yaml").read_bytes()
        for targets in (["192.0.2.31"], ["ASRL13::INSTR"], [LAN] * 65, None):
            with self.subTest(targets=targets):
                if targets is None:
                    proposed = {key: value for key, value in document["devices"].items() if key != "usb_source"}
                    kwargs = {}
                else:
                    proposed = document["devices"]
                    kwargs = {"network_resources": targets}
                with self.assertRaises(ValueError):
                    save_device_library(proposed, base_revision=document["revision"], root=self.root, **kwargs)
                self.assertEqual((self.root / "params" / "devices.yaml").read_bytes(), before)

    def test_each_network_instrument_has_independent_id_and_serial(self):
        document = public_device_library(self.root)
        proposed = deepcopy(document["devices"])
        for index in range(31, 35):
            proposed[f"dg9q{index}"] = {**self.device_configs["lan_source"], "resource": f"TCPIP0::192.168.124.{index}::INSTR",
                                        "connection": {"serial_number": f"DG9Q{index}"}}
        saved = save_device_library(proposed, base_revision=document["revision"], root=self.root)
        self.assertEqual([saved["devices"][f"dg9q{i}"]["connection"]["serial_number"] for i in range(31, 35)],
                         [f"DG9Q{i}" for i in range(31, 35)])
        self.assertEqual(resolve_mapping(self.root)["Z_magnetic_field"]["resource"], USB)

    def test_whole_group_switch_preserves_endpoints_and_rejects_partial_switch(self):
        document = public_physical_mappings(self.root)
        before = (self.root / "params" / "mapping.yaml").read_bytes()
        partial = deepcopy(document["mapping"])
        partial["Z_magnetic_field"]["device_id"] = "lan_source"
        with self.assertRaises(ValueError):
            save_physical_mappings(partial, document["constraints"], base_revision=document["revision"], root=self.root)
        self.assertEqual((self.root / "params" / "mapping.yaml").read_bytes(), before)
        for config in partial.values():
            config["device_id"] = "lan_source"
        saved = save_physical_mappings(partial, document["constraints"], base_revision=document["revision"], root=self.root)
        self.assertEqual([config["endpoint"]["index"] for config in saved["mapping"].values()], [1, 2, 2])
        self.assertEqual({config["device_id"] for config in saved["mapping"].values()}, {"lan_source"})
        with self.assertRaises(RevisionConflict):
            save_physical_mappings(document["mapping"], document["constraints"], base_revision=document["revision"], root=self.root)

    def test_temperature_control_config_requires_joint_switch_and_keeps_channels(self):
        path = Path(__file__).resolve().parents[1] / "params" / "mapping.yaml"
        configured = yaml.safe_load(path.read_text(encoding="utf-8"))
        members = configured["constraints"]["colocation_groups"].get("temperature_control")
        self.assertEqual(members, ["Heat_Control", "Temp_Switch"])
        self.assertEqual([configured["mapping"][key]["endpoint"]["index"] for key in members], [1, 2])
        self.assertEqual(len({configured["mapping"][key]["device_id"] for key in members}), 1)
        self.mappings.update({key: {**deepcopy(configured["mapping"][key]), "device_id": "usb_source"}
                              for key in members})
        self.constraints["colocation_groups"]["temperature_control"] = members
        self.write("mapping.yaml", {"schema_version": 2, "mapping": self.mappings, "constraints": self.constraints})
        library, document = public_device_library(self.root), public_physical_mappings(self.root)
        paths = [self.root / "params" / name for name in ("devices.yaml", "mapping.yaml")]
        before = [path.read_bytes() for path in paths]
        proposed = deepcopy(document["mapping"])
        proposed["Heat_Control"]["device_id"] = "lan_source"
        with self.assertRaisesRegex(ValueError, "同机组 temperature_control"):
            save_device_library(library["devices"], base_revision=library["revision"], mapping=proposed,
                                physical_mapping_revision=document["revision"], root=self.root)
        self.assertEqual([path.read_bytes() for path in paths], before)
        proposed["Temp_Switch"]["device_id"] = "lan_source"
        saved = save_device_library(library["devices"], base_revision=library["revision"], mapping=proposed,
                                    physical_mapping_revision=document["revision"], root=self.root)
        self.assertEqual(saved["physical_mappings"]["mapping"], proposed)
        resolved = resolve_mapping(self.root)
        self.assertEqual([resolved[key]["resource"] for key in members], [LAN, LAN])
        self.assertEqual([resolved[key]["channel"] for key in members], [1, 2])

    def test_channel_shortage_and_invalid_group_member_do_not_write(self):
        document = public_physical_mappings(self.root)
        before = (self.root / "params" / "mapping.yaml").read_bytes()
        proposed = deepcopy(document["mapping"])
        for config in proposed.values():
            config["device_id"] = "narrow_source"
        with self.assertRaises(ValueError):
            save_physical_mappings(proposed, document["constraints"], base_revision=document["revision"], root=self.root)
        bad_constraints = deepcopy(document["constraints"])
        bad_constraints["colocation_groups"]["z_sequence"] = ["usb_source", "sequence"]
        with self.assertRaises(ValueError):
            save_physical_mappings(document["mapping"], bad_constraints, base_revision=document["revision"], root=self.root)
        self.assertEqual((self.root / "params" / "mapping.yaml").read_bytes(), before)

    def test_network_clock_profile_and_connected_sync_use_library_id(self):
        configs = deepcopy(self.device_configs)
        configs["usb_source"]["reference_clock"] = "EXT"
        configs["lan_source"]["reference_clock"] = "INT"
        self.assertEqual(clock_steps.clock_device_id({"instrument": "signal_generator", "resource": USB}), "DG4E123")
        with self.assertRaisesRegex(ValueError, "设备库 ID"):
            clock_steps.clock_device_id({"instrument": "signal_generator", "resource": LAN})
        for resource in (LAN, "TCPIP0::192.0.2.31::inst0::INSTR", "TCPIP0::192.0.2.31::5025::SOCKET"):
            with self.subTest(resource=resource):
                configs["lan_source"]["resource"] = resource
                self.write("devices.yaml", {"devices": configs})
                for config in self.mappings.values():
                    config["device_id"] = "lan_source"
                self.write("mapping.yaml", {"mapping": self.mappings, "constraints": self.constraints})
                mapping = resolve_mapping(self.root)
                with patch.object(clock_steps, "find_project_root", return_value=self.root):
                    profile = clock_steps.load_clock_profile()
                self.assertEqual(profile, {"__default__": "EXT", "DG4E123": "EXT", "lan_source": "INT"})
                self.assertEqual(clock_steps.clock_device_id(mapping["Z_magnetic_field"]), "lan_source")
                generator = Mock(spec=["get_ref_clock_source", "set_ref_clock_source"])
                generator.get_ref_clock_source.side_effect = ["EXTernal", "INTernal"]
                results = clock_steps.synchronize_connected_clocks(
                    {"z": generator, "timing": generator}, mapping,
                    {"z": "Z_magnetic_field", "timing": "sequence"}, profile=profile, settle_s=0,
                )
                self.assertEqual(set(results), {"z"})
                self.assertEqual(results["z"]["device_id"], "lan_source")
                self.assertTrue(results["z"]["ok"])
                generator.set_ref_clock_source.assert_called_once_with("INTernal")
                generator.get_ref_clock_source.return_value = "EXTernal"
                generator.get_ref_clock_source.side_effect = None
                with self.assertRaisesRegex(RuntimeError, "目标 INT，实际 EXT"):
                    clock_steps.synchronize_connected_clocks(
                        {"z": generator}, mapping, {"z": "Z_magnetic_field"}, profile=profile, settle_s=0,
                    )

    def test_bulk_clock_sync_groups_network_channels_and_keeps_device_failures_independent(self):
        configs = deepcopy(self.device_configs)
        configs["usb_source"]["reference_clock"] = "EXT"
        configs["lan_source"]["reference_clock"] = "INT"
        for channel in (1, 2):
            self.mappings[f"network_ch{channel}"] = {"instrument": "signal_generator", "device_id": "lan_source",
                                                     "endpoint": {"kind": "channel", "index": channel}}
        self.constraints["colocation_groups"]["network"] = ["network_ch1", "network_ch2"]
        self.write("devices.yaml", {"devices": configs})
        self.write("mapping.yaml", {"mapping": self.mappings, "constraints": self.constraints})
        mapping = resolve_mapping(self.root)
        for actual in ("INTernal", "EXTernal"):
            with self.subTest(actual=actual):
                usb, lan = Mock(), Mock()
                usb.connect.side_effect = RuntimeError("测试连接失败")
                lan.get_ref_clock_source.side_effect = ["EXTernal", actual]
                with patch.object(clock_sync, "load_mapping", return_value=mapping), \
                     patch.object(clock_steps, "find_project_root", return_value=self.root), \
                     patch.object(clock_steps.time, "sleep"), \
                     patch.object(clock_sync, "create_lockin") as lockin_factory, \
                     patch.object(clock_sync, "create_signal_generator",
                                  side_effect=lambda config, **kwargs: usb if config["resource"] == USB else lan) as factory:
                    results = clock_sync.synchronize_clocks()
                self.assertEqual(factory.call_count, 2)
                lockin_factory.assert_not_called()
                self.assertEqual([result.device_id for result in results], ["DG4E123", "lan_source"])
                self.assertFalse(results[0].ok)
                self.assertEqual(results[0].error, "测试连接失败")
                self.assertEqual(results[1].target, "INT")
                self.assertEqual(results[1].ok, actual == "INTernal")
                lan.set_ref_clock_source.assert_called_once_with("INTernal")
                lan.connect.assert_called_once_with()
                lan.disconnect.assert_called_once_with()
                usb.disconnect.assert_called_once_with()

    def test_bulk_clock_sync_sets_clock_without_touching_load(self):
        for model, resource in (("DG4000", USB), ("DG900", LAN)):
            config = {"instrument": "signal_generator", "model": model,
                      "resource": resource, "channel": 1, "device_library_id": "test_source"}
            spec = {"type": model, "short": clock_steps.clock_device_id(config),
                    "label": "测试信号源", "channels": {1, 2}, "config": config}
            visa = Mock()
            responses = {
                "*IDN?": "RIGOL,测试信号源",
                ":SYSTem:ROSCillator:SOURce?": "EXTernal",
            }
            visa.query.side_effect = responses.__getitem__
            resource_manager = Mock()
            resource_manager.open_resource.return_value = visa
            with self.subTest(model=model), \
                 patch.object(clock_sync, "_discover_clock_devices", return_value=[spec]), \
                 patch.object(clock_sync, "load_clock_profile", return_value={"__default__": "EXT"}), \
                 patch("pyvisa.ResourceManager", return_value=resource_manager):
                results = clock_sync.synchronize_clocks()
                self.assertTrue(results[0].ok)
                # 时钟同步只写入参考时钟源，完全不碰输出负载。
                self.assertEqual(visa.write.call_args_list, [
                    call(":SYSTem:ROSCillator:SOURce EXTernal"),
                ])
                roscc = call(":SYSTem:ROSCillator:SOURce?")
                self.assertEqual(visa.query.call_args_list.count(roscc), 2)  # before + 统一校验
                self.assertNotIn(call(":OUTPut1:LOAD?"), visa.query.call_args_list)
                self.assertNotIn(call(":OUTPut2:LOAD?"), visa.query.call_args_list)
                visa.close.assert_called_once_with()
                # ResourceManager 是进程级共享后端，断开时不得关闭它
                resource_manager.close.assert_not_called()

    def test_factory_physical_key_follows_binding_and_keeps_dictionary_resource_forms(self):
        mapping = resolve_mapping(self.root)
        with patch.object(devices, "load_mapping", return_value=mapping):
            for config in ("Z_magnetic_field", mapping["Z_magnetic_field"], USB):
                instance = devices.create_signal_generator(config)
                self.assertIsInstance(instance, DG4000Instrument)
                self.assertEqual((instance.resource_string, instance.channel), (USB, 1))
                self.assertIsNone(instance._inst)
            explicit = devices.create_signal_generator("Z_magnetic_field", channel=2)
            self.assertEqual(explicit.channel, 2)
        document = public_physical_mappings(self.root)
        for config in document["mapping"].values():
            config["device_id"] = "lan_source"
        save_physical_mappings(document["mapping"], document["constraints"], base_revision=document["revision"], root=self.root)
        with patch.object(devices, "load_mapping", side_effect=lambda: resolve_mapping(self.root)):
            instance = devices.create_signal_generator("Z_magnetic_field")
        self.assertIsInstance(instance, DG900Instrument)
        manager = Mock()
        manager.open_resource.side_effect = RuntimeError("连接失败")
        with patch("pyvisa.ResourceManager", return_value=manager), self.assertRaisesRegex(RuntimeError, "连接失败"):
            instance.connect()
        manager.open_resource.assert_called_once_with(LAN)
        self.assertEqual(instance.resource_string, LAN)

    def prepare_combined_save(self):
        library = public_device_library(self.root)
        for model, channels in (("HF2", list(range(6))), ("MFLI", list(range(4)))):
            library["devices"][model.lower()] = {"instrument": "lockin_amplifier", "model": model, "label": model,
                "connection": {"device_id": "dev1" if model == "HF2" else "dev2"}, "capabilities": {"channels": channels}}
        self.write("devices.yaml", library)
        mappings = public_physical_mappings(self.root)
        mappings["mapping"].update({
            "lockin_r": {"instrument": "lockin_amplifier", "device_id": "hf2", "endpoint": {"kind": "demod", "index": 0}},
            "lockin_xy": {"instrument": "lockin_amplifier", "device_id": "hf2", "endpoint": {"kind": "demod", "index": 1}},
            "Probe_AOM_Carrier": {"instrument": "signal_generator", "device_id": "lan_source", "endpoint": {"kind": "channel", "index": 1}},
        })
        self.write("mapping.yaml", mappings)
        library = public_device_library(self.root)
        mappings = public_physical_mappings(self.root)
        proposed = deepcopy(mappings["mapping"])
        for key in ("Z_magnetic_field", "sequence", "shared_sequence"):
            proposed[key]["device_id"] = "lan_source"
        for key in ("lockin_r", "lockin_xy"):
            proposed[key]["device_id"] = "mfli"
        return library, mappings, proposed

    def test_combined_save_switches_actual_sources_and_lockin_keeps_endpoints(self):
        library, mappings, proposed = self.prepare_combined_save()
        original_endpoints = {key: value["endpoint"] for key, value in proposed.items()}
        saved = save_device_library(library["devices"], base_revision=library["revision"], mapping=proposed,
                                   physical_mapping_revision=mappings["revision"], network_resources=[], root=self.root)
        self.assertEqual(saved["physical_mappings"]["mapping"], proposed)
        self.assertEqual(saved["physical_mappings"], public_physical_mappings(self.root))
        self.assertEqual(saved["devices"], library["devices"])
        self.assertEqual(saved["network_resources"], [])
        resolved = resolve_mapping(self.root)
        self.assertEqual(resolved["Z_magnetic_field"]["resource"], LAN)
        self.assertEqual(resolved["Probe_AOM_Carrier"]["resource"], LAN)
        self.assertEqual(resolved["lockin_r"]["model"], "MFLI")
        self.assertEqual({key: value["endpoint"] for key, value in saved["physical_mappings"]["mapping"].items()}, original_endpoints)
        self.assertEqual(saved["physical_mappings"]["constraints"], mappings["constraints"])

    def test_combined_save_rejects_stale_revision_missing_revision_and_bad_channel_before_writing(self):
        library, mappings, proposed = self.prepare_combined_save()
        before = [(self.root / "params" / name).read_bytes() for name in ("devices.yaml", "mapping.yaml")]
        cases = [("stale", mappings["revision"], proposed), (library["revision"], "stale", proposed),
                 (library["revision"], None, proposed)]
        invalid = deepcopy(proposed)
        invalid["lockin_xy"]["endpoint"]["index"] = 5
        cases.append((library["revision"], mappings["revision"], invalid))
        for device_revision, mapping_revision, mapping in cases:
            with self.subTest(revisions=(device_revision, mapping_revision)), self.assertRaises(ValueError):
                save_device_library(library["devices"], base_revision=device_revision, mapping=mapping,
                                    physical_mapping_revision=mapping_revision, network_resources=[LAN], root=self.root)
            self.assertEqual([(self.root / "params" / name).read_bytes() for name in ("devices.yaml", "mapping.yaml")], before)

    def test_mapping_write_failure_restores_original_library_bytes(self):
        library, mappings, proposed = self.prepare_combined_save()
        before = [(self.root / "params" / name).read_bytes() for name in ("devices.yaml", "mapping.yaml")]
        original_write = instrument_config._atomic_write
        def fail_mapping_write(path, payload):
            if path.name == "mapping.yaml":
                raise OSError("映射文件不可写")
            return original_write(path, payload)
        with patch.object(instrument_config, "_atomic_write", side_effect=fail_mapping_write), self.assertRaises(OSError):
            save_device_library(library["devices"], base_revision=library["revision"], mapping=proposed,
                                physical_mapping_revision=mappings["revision"], network_resources=[LAN], root=self.root)
        self.assertEqual([(self.root / "params" / name).read_bytes() for name in ("devices.yaml", "mapping.yaml")], before)

    def test_signal_channels_share_across_experiments_but_not_independent_roles_in_one_run(self):
        library, mappings, proposed = self.prepare_combined_save()
        save_device_library(library["devices"], base_revision=library["revision"], mapping=proposed,
                            physical_mapping_revision=mappings["revision"], root=self.root)
        validate_mapping_usage(("Z_magnetic_field", "sequence", "shared_sequence"), self.root)
        validate_mapping_usage(("Probe_AOM_Carrier",), self.root)
        with self.assertRaisesRegex(ValueError, "冲突占用"):
            validate_mapping_usage(("Z_magnetic_field", "Probe_AOM_Carrier"), self.root)

    def test_shared_device_load_policy_follows_role_for_factory_and_control(self):
        library, mappings, proposed = self.prepare_combined_save()
        proposed["Time_sequence_2"] = proposed.pop("sequence")
        constraints = deepcopy(mappings["constraints"])
        constraints["colocation_groups"]["z_sequence"] = ["Z_magnetic_field", "Time_sequence_2"]
        constraints["shared_channel_groups"]["timing"] = ["Time_sequence_2", "shared_sequence"]
        self.write("mapping.yaml", {"mapping": proposed, "constraints": constraints})
        mapping_document = instrument_config.load_mapping_document(self.root)
        with patch.object(devices, "load_mapping", side_effect=lambda: resolve_mapping(self.root)), \
             patch.object(instrument_config, "load_mapping_document", return_value=mapping_document), \
             patch.dict(devices.SIGNAL_GENERATOR_DRIVERS, {"DG900": lambda resource, **kwargs: kwargs}):
            z = devices.create_signal_generator("Z_magnetic_field")
            timing = devices.create_signal_generator(resolve_mapping(self.root)["Time_sequence_2"])
            carrier = devices.create_signal_generator("Probe_AOM_Carrier")
            for mapping_key in ("Z_magnetic_field", "Probe_AOM_Carrier"):
                with self.subTest(mapping_key=mapping_key):
                    driver = Mock()
                    with patch.dict(devices.SIGNAL_GENERATOR_DRIVERS, {"DG900": driver}):
                        instrument_control._connect(SimpleNamespace(type="DG900", resource=LAN, options={"mapping_key": mapping_key}))
                    self.assertEqual(driver.call_args.kwargs["load_policy"], {1: "INF", 2: "INF"})
                    driver.return_value.connect.assert_called_once_with()
        self.assertEqual(z["load_policy"], {1: "INF", 2: "INF"})
        self.assertEqual(timing["load_policy"], {1: "INF", 2: "INF"})
        self.assertEqual(carrier["load_policy"], {1: "INF", 2: "INF"})


if __name__ == "__main__":
    unittest.main()
