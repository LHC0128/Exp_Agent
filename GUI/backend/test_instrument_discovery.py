"""设备发现的无硬件回归：网络目标、锁相接口、连接清理和硬件互斥。"""

from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import yaml

from GUI.backend import main
from lab_workflows.common import WorkflowCancelled
from lab_workflows.instrument_config import discover_visa_devices, public_device_library


USB = "USB0::0x1AB1::0x0646::DG9Q123::INSTR"
LAN = "TCPIP0::192.0.2.21::INSTR"
SOCKET = "TCPIP0::192.0.2.22::1394::SOCKET"


class InstrumentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        (self.root / "params").mkdir()
        self.visa = Mock()
        self.visa.list_resources.return_value = []
        self.instruments = {}
        self.visa.open_resource.side_effect = lambda resource, **kwargs: self.instruments[resource]
        self.resource_manager = stack.enter_context(patch("pyvisa.ResourceManager", return_value=self.visa))
        self.server = Mock()
        self.server.listNodes.return_value = []
        self.server.getString.return_value = "{}"
        self.daq_server = stack.enter_context(patch("zhinst.core.ziDAQServer", return_value=self.server))

    def write_config(self, name, value):
        path = self.root / "params" / name
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
        return path

    def add_instrument(self, resource, idn):
        instrument = Mock()
        instrument.query.return_value = idn
        self.instruments[resource] = instrument
        return instrument

    def test_all_visa_resources_include_sockets_and_only_query_identity(self):
        self.visa.list_resources.return_value = [USB, SOCKET, "ASRL13::INSTR", "USB0::raw::RAW"]
        usb = self.add_instrument(USB, "RIGOL,DG912 Pro,DG9Q123,1.0")
        network = self.add_instrument(SOCKET, "KEITHLEY,6221,4503331,1.0")
        result = discover_visa_devices(self.root)
        self.visa.list_resources.assert_called_once_with("?*")
        self.assertEqual([item["resource"] for item in result["devices"]], [USB, SOCKET])
        self.assertEqual(result["devices"][1]["serial_number"], "4503331")
        self.assertEqual(network.read_termination, "\n")
        self.assertEqual(network.write_termination, "\n")
        for instrument in (usb, network):
            instrument.query.assert_called_once_with("*IDN?")
            instrument.write.assert_not_called()
            instrument.close.assert_called_once_with()
        self.visa.close.assert_called_once_with()

    def test_configured_network_and_saved_resources_are_scanned_once_without_saving(self):
        config = self.write_config("instrument_discovery.yaml", {"network_resources": [LAN, LAN]})
        library = self.write_config("devices.yaml", {"devices": {
            "source": {"instrument": "signal_generator", "model": "DG900", "label": "信号源", "resource": USB},
            "current": {"instrument": "keithley_6221", "model": "6221", "label": "电流源", "resource": SOCKET},
        }})
        before = (config.read_bytes(), library.read_bytes())
        self.visa.list_resources.return_value = [USB]
        for resource, idn in [(USB, "RIGOL,DG912,DG9Q123,1"), (LAN, "RIGOL,DG912,DG9Q123,1"), (SOCKET, "KEITHLEY,6221,4503331,1")]:
            self.add_instrument(resource, idn)
        result = discover_visa_devices(self.root)
        self.assertEqual(self.visa.open_resource.call_count, 3)
        self.assertEqual({item["resource"] for item in result["devices"]}, {USB, LAN, SOCKET})
        self.assertEqual(before, (config.read_bytes(), library.read_bytes()))

    def test_network_failure_does_not_hide_usb_or_other_network_devices(self):
        bad = "TCPIP0::192.0.2.99::INSTR"
        self.write_config("instrument_discovery.yaml", {"network_resources": [bad, LAN]})
        self.visa.list_resources.return_value = [USB]
        self.add_instrument(USB, "RIGOL,DG912,DG9Q123,1")
        self.add_instrument(LAN, "SIGLENT,SDS1204X,SDSE123,1")
        result = discover_visa_devices(self.root)
        self.assertEqual(len(result["devices"]), 2)
        self.assertEqual(result["errors"][0]["resource"], bad)
        self.visa.close.assert_called_once_with()

    def test_library_targets_override_legacy_and_confirm_four_independent_sources(self):
        targets = [f"TCPIP0::192.168.124.{index}::INSTR" for index in range(31, 35)]
        library = self.write_config("devices.yaml", {"devices": {}, "network_resources": targets})
        self.write_config("instrument_discovery.yaml", {"network_resources": [LAN]})
        before = library.read_bytes()
        for index, target in enumerate(targets, 31):
            self.add_instrument(target, f"RIGOL,DG912 Pro,DG9Q{index},1")
        result = discover_visa_devices(self.root)
        self.assertEqual([item["serial_number"] for item in result["devices"]], [f"DG9Q{i}" for i in range(31, 35)])
        self.assertEqual([item["model"] for item in result["devices"]], ["DG900"] * 4)
        self.assertEqual(library.read_bytes(), before)
        self.assertNotIn(LAN, [call.args[0] for call in self.visa.open_resource.call_args_list])

    def test_empty_library_targets_disable_legacy_candidates_but_still_scan_saved_usb(self):
        self.write_config("devices.yaml", {"network_resources": [], "devices": {
            "source": {"instrument": "signal_generator", "model": "DG900", "label": "信号源", "resource": USB},
        }})
        self.write_config("instrument_discovery.yaml", {"network_resources": [LAN]})
        self.add_instrument(USB, "RIGOL,DG912 Pro,DG9Q123,1")
        result = discover_visa_devices(self.root)
        self.assertEqual([item["resource"] for item in result["devices"]], [USB])
        self.visa.open_resource.assert_called_once_with(USB, open_timeout=2000)

    def test_visa_aliases_scan_once_and_keep_saved_resource_spelling(self):
        self.write_config("devices.yaml", {"devices": {
            "source": {"instrument": "signal_generator", "model": "DG900", "label": "信号源", "resource": USB},
        }})
        self.write_config("instrument_discovery.yaml", {"network_resources": [LAN]})
        self.visa.list_resources.return_value = [USB.replace("::INSTR", "::0::INSTR"), LAN.replace("::INSTR", "::inst0::INSTR")]
        self.add_instrument(USB, "RIGOL,DG912,DG9Q123,1")
        self.add_instrument(LAN, "RIGOL,DG912,DG9Q123,1")
        result = discover_visa_devices(self.root)
        self.assertEqual(self.visa.open_resource.call_count, 2)
        self.assertEqual([item["resource"] for item in result["devices"]], [USB, LAN])

    def test_visa_enumeration_failure_still_scans_explicit_targets(self):
        self.write_config("instrument_discovery.yaml", {"network_resources": [LAN]})
        self.visa.list_resources.side_effect = RuntimeError("枚举失败")
        self.add_instrument(LAN, "RIGOL,DG4162,DG4E123,1")
        result = discover_visa_devices(self.root)
        self.assertEqual(result["devices"][0]["resource"], LAN)
        self.assertEqual(result["errors"][0]["message"], "枚举失败")

    def test_hf2_identity_uses_data_server_without_connecting_or_disconnect_device(self):
        self.write_config("devices.yaml", {"devices": {
            "hf2": {"instrument": "lockin_amplifier", "model": "HF2", "label": "锁相", "resource": None,
                    "connection": {"device_id": "dev18246", "host": "192.0.2.40", "port": 8005}},
        }})
        self.server.listNodes.return_value = ["ZI", "DEV18246", "dev19001"]
        self.server.getString.side_effect = [json.dumps({
            "DEV18246": {"INTERFACE": "PCIe"}, "dev19001": {"INTERFACE": "PCIe"},
        }), "HF2LI", "HF2LI"]
        result = discover_visa_devices(self.root)
        self.daq_server.assert_called_once_with("192.0.2.40", 8005, 1)
        self.assertEqual([item["serial_number"] for item in result["devices"]], ["dev18246", "dev19001"])
        self.assertEqual(result["devices"][0]["connection"], {"device_id": "dev18246", "host": "192.0.2.40", "port": 8005, "interface": "PCIe"})
        self.assertIsNone(result["devices"][0]["resource"])
        self.server.connectDevice.assert_not_called()
        self.server.disconnectDevice.assert_not_called()
        self.server.setInt.assert_not_called()
        self.server.disconnect.assert_called_once_with()

    def test_mfli_discovery_uses_server_interface_and_does_not_change_saved_config(self):
        library = self.write_config("devices.yaml", {"devices": {
            "mfli": {"instrument": "lockin_amplifier", "model": "MFLI", "label": "锁相", "resource": None,
                     "connection": {"device_id": "dev30661", "host": "192.0.2.40", "port": 8004, "interface": "1GbE"}},
        }})
        before = library.read_bytes()
        self.server.listNodes.return_value = ["DEV30661"]
        for interface in ("PCIe", "1GbE", "USB"):
            with self.subTest(interface=interface):
                metadata = json.dumps({"DEV30661": {"INTERFACE": interface, "INTERFACES": "PCIe,1GbE,USB"}})
                self.server.getString.side_effect = lambda path: metadata if path == "/zi/devices" else "MFLI"
                result = discover_visa_devices(self.root)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["devices"][0]["connection"], {
                    "device_id": "dev30661", "host": "192.0.2.40", "port": 8004, "interface": interface,
                })
                self.assertEqual(library.read_bytes(), before)
        self.server.connectDevice.assert_not_called()
        self.server.disconnectDevice.assert_not_called()
        self.server.setInt.assert_not_called()
        self.server.setDouble.assert_not_called()

    def test_missing_server_interface_reports_error_instead_of_guessing_by_model(self):
        self.server.listNodes.return_value = ["DEV30661"]
        self.server.getString.side_effect = lambda path: '{"DEV30661": {}}' if path == "/zi/devices" else "MFLI"
        result = discover_visa_devices(self.root)
        self.assertEqual(result["devices"], [])
        self.assertIn("未报告设备连接接口", result["errors"][0]["message"])
        self.server.connectDevice.assert_not_called()
        self.server.disconnect.assert_called_once_with()

    def test_visa_failure_still_discovers_hf2(self):
        self.resource_manager.side_effect = RuntimeError("VISA 不可用")
        self.server.listNodes.return_value = ["DEV18246"]
        self.server.getString.side_effect = [json.dumps({"DEV18246": {"INTERFACE": "PCIe"}}), "HF2LI"]
        result = discover_visa_devices(self.root)
        self.assertEqual(result["devices"][0]["serial_number"], "dev18246")
        self.assertEqual(result["errors"][0]["resource"], "VISA")

    def test_one_hf2_read_failure_does_not_hide_other_hf2_devices(self):
        self.server.listNodes.return_value = ["dev18246", "dev19001"]
        self.server.getString.side_effect = [json.dumps({
            "dev18246": {"INTERFACE": "PCIe"}, "dev19001": {"INTERFACE": "PCIe"},
        }), RuntimeError("读取失败"), "HF2LI"]
        result = discover_visa_devices(self.root)
        self.assertEqual(result["devices"][0]["serial_number"], "dev19001")
        self.assertIn("dev18246", result["errors"][0]["resource"])
        self.server.disconnect.assert_called_once_with()

    def test_cleanup_failure_keeps_discovered_identity_and_reports_error(self):
        self.visa.list_resources.return_value = [USB]
        instrument = self.add_instrument(USB, "RIGOL,DG912,DG9Q123,1")
        instrument.close.side_effect = RuntimeError("连接关闭失败")
        self.server.disconnect.side_effect = RuntimeError("服务器关闭失败")
        result = discover_visa_devices(self.root)
        self.assertEqual(len(result["devices"]), 1)
        self.assertEqual(len(result["errors"]), 2)
        self.visa.close.assert_called_once_with()

    def test_unknown_serial_is_not_used_as_device_identity(self):
        self.visa.list_resources.return_value = [USB]
        self.add_instrument(USB, "RIGOL,DG912,0,1")
        self.assertEqual(discover_visa_devices(self.root)["devices"][0]["serial_number"], "")

    def test_invalid_targets_fail_before_instrument_connection(self):
        for value in ["192.0.2.21", ["ASRL13::INSTR"], [LAN] * 65]:
            self.write_config("instrument_discovery.yaml", {"network_resources": value})
            with self.subTest(value=value), self.assertRaises(ValueError):
                discover_visa_devices(self.root)
        self.resource_manager.assert_not_called()
        self.daq_server.assert_not_called()

    def test_invalid_yaml_structure_fails_before_instrument_connection(self):
        self.write_config("instrument_discovery.yaml", [LAN])
        with self.assertRaises(ValueError):
            discover_visa_devices(self.root)
        self.resource_manager.assert_not_called()

    def test_cancel_closes_manager_and_does_not_connect_instrument(self):
        self.visa.list_resources.return_value = [USB]
        with patch("lab_workflows.experiment_runtime.check_cancelled", side_effect=WorkflowCancelled("停止")):
            with self.assertRaises(WorkflowCancelled):
                discover_visa_devices(self.root)
        self.visa.open_resource.assert_not_called()
        self.visa.close.assert_called_once_with()

    def test_api_passes_workspace_root_and_preserves_discovery_fields(self):
        result = {"devices": [{"resource": None, "instrument": "lockin_amplifier", "model": "HF2", "idn": "Zurich Instruments,HF2LI,dev18246", "serial_number": "dev18246", "connection": {"host": "127.0.0.1", "port": 8005, "device_id": "dev18246"}}], "errors": []}
        with patch.object(main, "ROOT", self.root), patch.object(main, "discover_visa_devices", return_value=result) as discover, patch.object(main, "_with_short_hardware_lock", side_effect=lambda action: action()):
            response = TestClient(main.app).post("/api/device-library/discover-visa")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        discover.assert_called_once_with(self.root)

    def test_api_rejects_scan_while_hardware_is_busy(self):
        with patch.object(main.manager, "try_acquire_short_hardware", return_value=False), patch.object(main, "discover_visa_devices") as discover:
            response = TestClient(main.app).post("/api/device-library/discover-visa")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.headers["X-Error-Code"], "hardware_busy")
        discover.assert_not_called()

    def test_api_saves_network_candidates_including_empty_list_with_device_revision(self):
        self.write_config("devices.yaml", {"devices": {}, "network_resources": [LAN]})
        self.write_config("mapping.yaml", {"mapping": {}, "constraints": {}})
        before = public_device_library(self.root)
        with patch.object(main, "ROOT", self.root), patch.object(main, "_with_configuration_lock", side_effect=lambda action: action()):
            response = TestClient(main.app).put("/api/device-library", json={
                "base_revision": before["revision"], "devices": {}, "network_resources": [],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["network_resources"], [])
        self.assertNotEqual(response.json()["revision"], before["revision"])
        self.assertEqual(public_device_library(self.root)["network_resources"], [])

    def test_api_legacy_request_without_network_field_preserves_candidates(self):
        self.write_config("devices.yaml", {"devices": {}, "network_resources": [LAN]})
        self.write_config("mapping.yaml", {"mapping": {}, "constraints": {}})
        before = public_device_library(self.root)
        with patch.object(main, "ROOT", self.root), patch.object(main, "_with_configuration_lock", side_effect=lambda action: action()):
            response = TestClient(main.app).put("/api/device-library", json={
                "base_revision": before["revision"], "devices": {},
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["network_resources"], [LAN])


if __name__ == "__main__":
    unittest.main()
