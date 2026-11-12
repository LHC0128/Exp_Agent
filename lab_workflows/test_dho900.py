"""DHO900 设备登记、身份扫描与保存的无硬件回归。"""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

import yaml

from hardware_gate import HardwareAccessDenied
from lab_workflows import instrument_config
from lab_workflows.common import WorkflowCancelled
from lab_workflows.setups import bound_setup


LAN = "TCPIP0::192.0.2.21::INSTR"
DEVICE_ID = "rigol_dho900_test"
PENDING_DEVICE = {
    "instrument": "oscilloscope",
    "model": "DHO900",
    "label": "RIGOL DHO900 示波器（子型号/序列号待确认）",
    "resource": LAN,
    "reference_clock": None,
    "connection": {
        "host": "192.0.2.21",
        "web_url": "http://192.0.2.21/",
        "identity_verified": False,
    },
    "capabilities": {"channels": [1, 2, 3, 4]},
}


class DHO900ConfigurationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        (self.root / "params").mkdir()
        stack.enter_context(bound_setup("default"))
        self.write("devices.yaml", {"schema_version": 1, "network_resources": [], "devices": {}})
        self.write("mapping.yaml", {
            "schema_version": 2,
            "mapping": {},
            "constraints": {"colocation_groups": {}, "shared_channel_groups": {}},
        })

        # 所有通信入口均为替身；门控只在本测试上下文内替换，不创建运行目录标记。
        self.gate = stack.enter_context(patch.object(instrument_config, "require_hardware_access"))
        self.cancel = stack.enter_context(patch("lab_workflows.experiment_runtime.check_cancelled"))
        self.visa = Mock()
        self.visa.list_resources.return_value = []
        self.instruments = {}
        self.visa.open_resource.side_effect = lambda resource, **kwargs: self.instruments[resource]
        self.manager_factory = stack.enter_context(patch("pyvisa.ResourceManager", return_value=self.visa))
        self.server = Mock()
        self.server.listNodes.return_value = []
        self.server.getString.return_value = "{}"
        self.server_factory = stack.enter_context(patch("zhinst.core.ziDAQServer", return_value=self.server))

    def write(self, name, payload):
        path = self.root / "params" / name
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def add_identity(self, resource, idn):
        instrument = Mock(spec=["query", "close"])
        instrument.query.side_effect = lambda command: idn if command == "*IDN?" else self.fail(
            f"身份扫描执行了额外查询：{command}"
        )
        self.instruments[resource] = instrument
        return instrument

    def prepare_existing_sds(self):
        self.write("devices.yaml", {"schema_version": 1, "network_resources": [], "devices": {
            "siglent_scope": {
                "instrument": "sds_acquisition", "model": "SDS", "label": "现用 Siglent SDS",
                "resource": "USB0::0xF4EC::0x1015::SDSFAKE::INSTR", "reference_clock": None,
                "connection": {}, "capabilities": {"channels": [1, 2, 3, 4]},
            },
        }})
        self.write("mapping.yaml", {
            "schema_version": 2,
            "mapping": {"sds": {"instrument": "sds_acquisition", "device_id": "siglent_scope"}},
            "constraints": {"colocation_groups": {}, "shared_channel_groups": {}},
        })
        return instrument_config.public_device_library(self.root)

    def assert_no_communication(self):
        self.gate.assert_not_called()
        self.manager_factory.assert_not_called()
        self.server_factory.assert_not_called()

    def test_repository_entry_keeps_identity_pending_without_a_fabricated_serial(self):
        path = Path(__file__).resolve().parents[1] / "params" / "devices.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        device = document["devices"]["rigol_dho900_192_168_124_21"]
        self.assertEqual(device, {
            **PENDING_DEVICE,
            "resource": "TCPIP0::192.168.124.21::INSTR",
            "connection": {
                "host": "192.168.124.21", "web_url": "http://192.168.124.21/",
                "identity_verified": False,
            },
        })
        self.assertEqual(document["network_resources"].count(device["resource"]), 1)
        self.assert_no_communication()

    def test_save_pending_library_preserves_sds_mapping_bytes_without_communication(self):
        library = self.prepare_existing_sds()
        mapping_path = self.root / "params" / "mapping.yaml"
        before = mapping_path.read_bytes()
        proposed = {**library["devices"], DEVICE_ID: deepcopy(PENDING_DEVICE)}
        saved = instrument_config.save_device_library(
            proposed, base_revision=library["revision"], network_resources=[LAN], root=self.root,
        )
        self.assertEqual(mapping_path.read_bytes(), before)
        self.assertEqual(saved["devices"][DEVICE_ID], PENDING_DEVICE)
        self.assertEqual(saved["network_resources"], [LAN])
        self.assertEqual(instrument_config.public_device_library(self.root), saved)
        resolved = instrument_config.resolve_mapping(self.root)
        self.assertEqual((resolved["sds"]["instrument"], resolved["sds"]["model"]), ("sds_acquisition", "SDS"))
        self.assertEqual(resolved["sds"]["device_library_id"], "siglent_scope")
        self.assert_no_communication()

    def test_combined_library_save_keeps_current_sds_binding(self):
        library = self.prepare_existing_sds()
        mappings = instrument_config.public_physical_mappings(self.root)
        saved = instrument_config.save_device_library(
            {**library["devices"], DEVICE_ID: deepcopy(PENDING_DEVICE)},
            base_revision=library["revision"], network_resources=[LAN],
            mapping=mappings["mapping"], physical_mapping_revision=mappings["revision"], root=self.root,
        )
        self.assertEqual(saved["physical_mappings"]["mapping"], mappings["mapping"])
        self.assertEqual(saved["physical_mappings"]["constraints"], mappings["constraints"])
        self.assertEqual(saved["devices"][DEVICE_ID], PENDING_DEVICE)
        self.assertEqual(instrument_config.resolve_mapping(self.root)["sds"]["device_library_id"], "siglent_scope")
        self.assert_no_communication()

    def test_stale_save_keeps_original_library_and_mapping(self):
        library = self.prepare_existing_sds()
        paths = [self.root / "params" / name for name in ("devices.yaml", "mapping.yaml")]
        before = [path.read_bytes() for path in paths]
        with self.assertRaises(instrument_config.RevisionConflict):
            instrument_config.save_device_library(
                {**library["devices"], DEVICE_ID: deepcopy(PENDING_DEVICE)},
                base_revision="stale-revision", network_resources=[LAN], root=self.root,
            )
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assert_no_communication()

    def test_scan_recognizes_exact_dho900_variants_and_only_queries_identity(self):
        variants = ["DHO914", "DHO914S", "DHO924", "dho924s"]
        resources = [f"TCPIP0::192.0.2.{index}::INSTR" for index in range(21, 25)]
        self.write("devices.yaml", {"schema_version": 1, "network_resources": resources, "devices": {}})
        self.visa.list_resources.return_value = [resource.replace("::INSTR", "::inst0::INSTR") for resource in resources]
        for index, (resource, variant) in enumerate(zip(resources, variants), 1):
            self.add_identity(resource, f" RIGOL TECHNOLOGIES , {variant} , DHOFAKE{index} ,0")
        paths = [self.root / "params" / name for name in ("devices.yaml", "mapping.yaml")]
        before = [path.read_bytes() for path in paths]
        result = instrument_config.discover_visa_devices(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual([item["resource"] for item in result["devices"]], resources)
        self.assertEqual([(item["instrument"], item["model"]) for item in result["devices"]], [("oscilloscope", "DHO900")] * 4)
        self.assertEqual([item["serial_number"] for item in result["devices"]], [f"DHOFAKE{i}" for i in range(1, 5)])
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.gate.assert_called_once_with("设备发现（VISA 与 LabOne）")
        self.visa.list_resources.assert_called_once_with("?*")
        self.assertEqual(self.visa.open_resource.call_args_list, [call(resource, open_timeout=2000) for resource in resources])
        for instrument in self.instruments.values():
            self.assertEqual(instrument.method_calls, [call.query("*IDN?"), call.close()])
        self.visa.close.assert_called_once_with()
        self.server.disconnect.assert_called_once_with()
        self.server.connectDevice.assert_not_called()
        self.server.setInt.assert_not_called()
        self.server.setDouble.assert_not_called()

    def test_scan_does_not_classify_dg900_or_unrelated_identity_as_dho900(self):
        cases = [
            ("RIGOL,DG912,DHO914,0", "signal_generator", "DG900"),
            ("RIGOL,DG900,DHO924S,0", "signal_generator", "DG900"),
            ("RIGOL,DHO900,FAKE,0", "unknown", "unknown"),
            ("RIGOL,DHO9140,FAKE,0", "unknown", "unknown"),
            ("RIGOL,DHO924S Pro,FAKE,0", "unknown", "unknown"),
            ("RIGOL,DHO924S-extra,FAKE,0", "unknown", "unknown"),
            ("OTHER,DHO914,FAKE,0", "unknown", "unknown"),
            ("NOT RIGOL,DHO924,FAKE,0", "unknown", "unknown"),
            ("RIGOL,OTHER,DHO914S,0", "unknown", "unknown"),
            ("SIGLENT,SDS1204X,FAKE,0", "sds_acquisition", "SDS"),
        ]
        resources = [f"TCPIP0::192.0.2.{index}::INSTR" for index in range(31, 31 + len(cases))]
        self.visa.list_resources.return_value = resources
        for resource, (idn, _, _) in zip(resources, cases):
            self.add_identity(resource, idn)
        result = instrument_config.discover_visa_devices(self.root)
        self.assertEqual(result["errors"], [])
        for item, (idn, kind, model) in zip(result["devices"], cases):
            with self.subTest(idn=idn):
                self.assertEqual((item["idn"], item["instrument"], item["model"]), (idn, kind, model))
        self.assertEqual(len(result["devices"]), len(cases))
        for instrument in self.instruments.values():
            self.assertEqual(instrument.method_calls, [call.query("*IDN?"), call.close()])

    def test_scan_leaves_unknown_serial_empty(self):
        self.visa.list_resources.return_value = [LAN]
        self.add_identity(LAN, "RIGOL,DHO924S,UNKNOWN,0")
        result = instrument_config.discover_visa_devices(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["devices"][0]["serial_number"], "")
        self.assertEqual(result["devices"][0]["model"], "DHO900")

    def test_gate_denial_stops_before_creating_communications(self):
        self.gate.side_effect = HardwareAccessDenied("测试拒绝设备扫描")
        with self.assertRaisesRegex(HardwareAccessDenied, "测试拒绝设备扫描"):
            instrument_config.discover_visa_devices(self.root)
        self.gate.assert_called_once_with("设备发现（VISA 与 LabOne）")
        self.manager_factory.assert_not_called()
        self.server_factory.assert_not_called()

    def test_scan_identity_failure_closes_sessions_and_keeps_other_devices(self):
        bad_resource = "TCPIP0::192.0.2.99::INSTR"
        self.visa.list_resources.return_value = [bad_resource, LAN]
        failed = self.add_identity(bad_resource, "")
        failed.query.side_effect = TimeoutError("测试身份查询超时")
        good = self.add_identity(LAN, "RIGOL,DHO914S,DHOFAKE,0")
        result = instrument_config.discover_visa_devices(self.root)
        self.assertEqual([item["resource"] for item in result["devices"]], [LAN])
        self.assertEqual(result["errors"], [{"resource": bad_resource, "message": "测试身份查询超时"}])
        for instrument in (failed, good):
            self.assertEqual(instrument.method_calls, [call.query("*IDN?"), call.close()])
        self.visa.close.assert_called_once_with()
        self.server.disconnect.assert_called_once_with()

    def test_scan_cancellation_closes_manager_before_opening_any_resource(self):
        self.visa.list_resources.return_value = [LAN]
        self.cancel.side_effect = WorkflowCancelled("测试取消扫描")
        with self.assertRaisesRegex(WorkflowCancelled, "测试取消扫描"):
            instrument_config.discover_visa_devices(self.root)
        self.visa.open_resource.assert_not_called()
        self.visa.close.assert_called_once_with()
        self.server_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
