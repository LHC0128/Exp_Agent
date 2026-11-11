"""真实接线图 API 的无硬件、临时根目录定向回归。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

import yaml
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from GUI.backend import main, schemas
from lab_workflows.test_wiring import follow_graph, graph_data, internal_graph_data, write_follow_fixture
from lab_workflows.wiring import (
    WiringBody, WiringCableChange, WiringCompareBody, WiringComparison, WiringGraph, WiringResponse, WiringRevision,
)


class WiringAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch.object(main, "ROOT", self.root))
        self.hardware_guards = []
        for name in (
            "discover_devices", "discover_visa_devices", "read_device", "read_control_target", "read_control_targets",
            "apply_generator_channel", "apply_current_source", "apply_laser_emission", "apply_laser_settings", "apply_scope",
            "apply_control_target_generator", "apply_control_target_current_source", "apply_control_target_emission",
            "apply_control_target_laser", "apply_control_target_scope", "apply_control_target_tec", "apply_control_target_lockin",
            "capture_control_target_lockin", "save_device_library", "save_physical_mappings", "public_device_library",
            "public_physical_mappings", "load_mapping", "initialize_output_loads", "synchronize_clocks",
            "get_experiment", "list_experiments", "public_catalog", "set_experiment_archived", "set_experiment_metadata",
        ):
            guard = self.stack.enter_context(patch.object(main, name, side_effect=AssertionError(f"不应调用硬件、配置或实验目录：{name}")))
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

    def request_body(self, revision=None):
        if revision is None:
            response = self.client.get("/api/wiring")
            self.assertEqual(response.status_code, 200, response.text)
            revision = response.json()["revision"]
        return {"base_revision": revision, "site": graph_data(),
                "targets": {"removed-experiment": graph_data(), "another-experiment": graph_data()}}

    def test_schemas_are_shared_and_routes_have_response_models(self):
        for name, model in (("WiringBody", WiringBody), ("WiringCableChange", WiringCableChange),
                            ("WiringCompareBody", WiringCompareBody),
                            ("WiringComparison", WiringComparison), ("WiringResponse", WiringResponse),
                            ("WiringRevision", WiringRevision)):
            self.assertIs(getattr(schemas, name), model)
        paths = {(route.path, method): route.response_model for route in main.app.routes
                 if getattr(route, "path", None) in {"/api/wiring", "/api/wiring/compare", "/api/wiring/revision"}
                 for method in route.methods}
        self.assertEqual(paths, {
            ("/api/wiring", "GET"): WiringResponse,
            ("/api/wiring", "PUT"): WiringResponse,
            ("/api/wiring/revision", "GET"): WiringRevision,
            ("/api/wiring/compare", "POST"): WiringComparison,
        })

    def test_get_missing_and_compare_do_not_create_any_files(self):
        response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        initial = response.json()
        self.assertEqual(set(initial), {"schema_version", "revision", "site", "targets", "diagnostics", "mapping_groups", "follow_status", "setup_id"})
        self.assertEqual(initial["setup_id"], "default")
        self.assertEqual(initial["mapping_groups"], [])
        self.assertEqual(initial["follow_status"], {"site": [], "targets": {}})
        self.assertEqual(initial["schema_version"], 1)
        self.assertEqual(initial["site"], {"nodes": [], "edges": []})
        self.assertEqual(initial["targets"], {})
        self.assertEqual(initial["diagnostics"], {"site": [], "targets": {}})
        compared = self.client.post("/api/wiring/compare", json={"site": graph_data(), "target": graph_data()})
        self.assertEqual(compared.status_code, 200, compared.text)
        self.assertEqual(set(compared.json()), {"retained", "removed", "added", "internal_retained", "internal_removed",
                                                "internal_added", "mode_changes", "usage_changes", "setting_changes",
                                                "cable_changes", "site_warnings", "target_warnings"})
        self.assertEqual(compared.json()["usage_changes"], [])
        self.assertEqual(compared.json()["cable_changes"], [])
        self.assertEqual(compared.json()["retained"], graph_data()["edges"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cable_fields_roundtrip_compare_changes_and_connector_diagnostics_are_read_only(self):
        body = self.request_body()
        for graph in (body["site"], body["targets"]["removed-experiment"]):
            graph["nodes"][0]["device"]["ports"][0]["connector"] = "BNC"
            graph["nodes"][1]["device"]["ports"][0]["connector"] = "SMA"
        body["site"]["edges"][0].update(connector="BNC", cable_id="C-01", note="现场备用")
        body["targets"]["removed-experiment"]["edges"][0].update(connector="BNC", cable_id="C-02")
        saved_response = self.client.put("/api/wiring", json=body)
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        saved = saved_response.json()
        self.assertEqual(saved["site"]["edges"][0], {
            **graph_data()["edges"][0], "connector": "BNC", "cable_id": "C-01", "note": "现场备用",
        })
        self.assertEqual(saved["targets"]["removed-experiment"]["edges"][0], {
            **graph_data()["edges"][0], "connector": "BNC", "cable_id": "C-02",
        })
        self.assertNotIn("connector", saved["targets"]["another-experiment"]["edges"][0])
        self.assertEqual([warning["code"] for warning in saved["diagnostics"]["site"]], ["connector_conflict"])
        self.assertIn("终点「接收器 / in」记录为“SMA”", saved["diagnostics"]["site"][0]["message"])
        self.assertEqual([warning["code"] for warning in saved["diagnostics"]["targets"]["removed-experiment"]],
                         ["connector_conflict"])
        with patch.object(main, "read_wiring", side_effect=AssertionError("连线比较不得读磁盘")), \
                patch.object(main, "save_wiring", side_effect=AssertionError("连线比较不得写磁盘")):
            compared = self.client.post("/api/wiring/compare", json={
                "site": saved["site"], "target": saved["targets"]["removed-experiment"],
            })
        self.assertEqual(compared.status_code, 200, compared.text)
        self.assertEqual(compared.json()["retained"], saved["targets"]["removed-experiment"]["edges"])
        self.assertEqual(compared.json()["cable_changes"], [{
            "edge_id": "edge-1",
            "site_connector": "BNC", "target_connector": "BNC",
            "site_cable_id": "C-01", "target_cable_id": "C-02",
            "site_note": "现场备用", "target_note": None,
        }])
        self.assertEqual([warning["code"] for warning in compared.json()["site_warnings"]], ["connector_conflict"])
        self.assertEqual([warning["code"] for warning in compared.json()["target_warnings"]], ["connector_conflict"])
        self.assertEqual(self.client.get("/api/wiring").json(), saved)

    def test_revision_endpoint_matches_full_read_and_never_writes_or_locks(self):
        missing = self.client.get("/api/wiring/revision")
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertEqual(set(missing.json()), {"revision"})
        self.assertEqual(missing.json()["revision"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(missing.json()["revision"], self.client.get("/api/wiring").json()["revision"])
        self.assertEqual(list(self.root.iterdir()), [])

        write_follow_fixture(self.root)
        params = self.root / "params"
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in params.iterdir()}
        with (
            patch("lab_workflows.instrument_config._process_config_lock",
                  side_effect=AssertionError("修订检查不得创建配置锁")),
            patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("修订检查不得写文件")),
        ):
            revision = self.client.get("/api/wiring/revision")
            full = self.client.get("/api/wiring")
        self.assertEqual(revision.status_code, 200, revision.text)
        self.assertEqual(set(revision.json()), {"revision"})
        self.assertEqual(revision.json()["revision"], full.json()["revision"])
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before})
        self.assertEqual(set(params.iterdir()), set(before))

        mapping = params / "mapping.yaml"
        mapping.write_bytes(mapping.read_bytes() + b"\n# saved elsewhere\n")
        changed = self.client.get("/api/wiring/revision")
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertNotEqual(changed.json()["revision"], revision.json()["revision"])
        self.assertEqual(changed.json()["revision"], self.client.get("/api/wiring").json()["revision"])

    def test_internal_routes_and_noise_eater_settings_roundtrip_update_and_delete(self):
        graph = internal_graph_data()
        graph["nodes"][0]["port_usage"] = {"clock_10mhz": "unused"}
        target = deepcopy(graph)
        target["nodes"][0]["settings"][0]["value"] = "low"
        target["nodes"][0]["settings"][1]["value"] = "4"
        target["edges"][1]["target"]["port_id"] = "feedback-input"
        request = {"base_revision": self.client.get("/api/wiring").json()["revision"], "site": graph,
                   "targets": {"removed-experiment": target, "legacy": graph_data()}}
        original = deepcopy(request)
        response = self.client.put("/api/wiring", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["site"], WiringGraph.model_validate(graph).model_dump())
        self.assertEqual(saved["targets"]["removed-experiment"], WiringGraph.model_validate(target).model_dump())
        self.assertEqual(saved["diagnostics"], {"site": [], "targets": {"removed-experiment": [], "legacy": []}})
        self.assertEqual(request, original)
        path = self.root / "params" / "wiring.yaml"
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.assertEqual(saved["revision"], hashlib.sha256(before[0]).hexdigest())
        self.assertEqual(yaml.safe_load(before[0])["site"], saved["site"])
        with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("GET 不得改写内部信号或图内设置")):
            loaded = self.client.get("/api/wiring")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertEqual(loaded.json(), saved)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        for index in range(1, 5):
            update = {"base_revision": saved["revision"], "site": deepcopy(saved["site"]), "targets": saved["targets"]}
            update["site"]["nodes"][0]["settings"][0]["value"] = "low" if index % 2 else "high"
            update["site"]["nodes"][0]["settings"][1]["value"] = str(index)
            response = self.client.put("/api/wiring", json=update)
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual([setting["value"] for setting in saved["site"]["nodes"][0]["settings"]],
                             ["low" if index % 2 else "high", str(index)])
            self.assertEqual(saved["targets"], loaded.json()["targets"])
            self.assertEqual(self.client.get("/api/wiring").json(), saved)
        update = {"base_revision": saved["revision"], "site": deepcopy(saved["site"]), "targets": saved["targets"]}
        update["site"]["nodes"][0]["settings"][0]["value"] = None
        response = self.client.put("/api/wiring", json=update)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["site"]["nodes"][0]["settings"][0]["value"])
        update["base_revision"] = response.json()["revision"]
        update["site"]["edges"] = update["site"]["edges"][:1]
        update["site"]["nodes"][0]["internal_ports"] = []
        update["site"]["nodes"][0]["settings"] = []
        response = self.client.put("/api/wiring", json=update)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["site"]["nodes"][0]["internal_ports"], [])
        self.assertEqual(response.json()["site"]["nodes"][0]["settings"], [])
        self.assertEqual(response.json()["targets"], loaded.json()["targets"])
        self.assertEqual(self.client.get("/api/wiring").json(), response.json())
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_internal_routes_and_setting_choices_are_422_without_touching_saved_graph(self):
        valid = internal_graph_data()
        valid["nodes"][1]["internal_ports"] = deepcopy(valid["nodes"][0]["internal_ports"])
        initial = self.client.get("/api/wiring").json()
        saved_response = self.client.put("/api/wiring", json={"base_revision": initial["revision"], "site": valid, "targets": {}})
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        saved = saved_response.json()
        path = self.root / "params" / "wiring.yaml"
        before = path.read_bytes(), path.stat().st_mtime_ns
        candidates = []
        for keys, values in (
            (["nodes", 0, "internal_ports"], [None, {}]),
            (["nodes", 0, "internal_ports", 0, "id"], [1, True, None, "", " ", "out"]),
            (["nodes", 0, "internal_ports", 0, "name"], [1, True, None, "", " "]),
            (["nodes", 0, "internal_ports", 0, "direction"], [True, "inout"]),
            (["nodes", 0, "internal_ports", 0, "unknown"], [True]),
            (["nodes", 0, "settings"], [None, {}]),
            (["nodes", 0, "settings", 0, "id"], [1, True, None, "", " "]),
            (["nodes", 0, "settings", 0, "name"], [1, True, None, "", " "]),
            (["nodes", 0, "settings", 0, "options"], [None, {}, []]),
            (["nodes", 0, "settings", 0, "options", 0, "value"], [1, True, None, "", " "]),
            (["nodes", 0, "settings", 0, "options", 0, "label"], [1, True, None, "", " "]),
            (["nodes", 0, "settings", 0, "options", 1, "value"], ["high"]),
            (["nodes", 0, "settings", 0, "options", 0, "unknown"], [True]),
            (["nodes", 0, "settings", 0, "value"], [1, True, "", " ", "missing"]),
            (["nodes", 0, "settings", 0, "unknown"], [True]),
            (["nodes", 0, "port_usage"], [{"aux-monitor": "used"}]),
            (["nodes", 0, "port_directions"], [{"aux-monitor": "out"}]),
            (["edges", 1, "source", "device_id"], ["receiver", "missing"]),
            (["edges", 1, "target", "device_id"], ["receiver", "missing"]),
            (["edges", 1, "source", "port_id"], ["out", "missing", "demod-input"]),
            (["edges", 1, "target", "port_id"], ["out", "missing", "control", "aux-monitor"]),
            (["edges", 1, "signal_type"], ["optical", "electrical"]),
            (["edges", 0, "signal_type"], ["internal"]),
        ):
            for value in values:
                candidate = deepcopy(valid)
                item = candidate
                for key in keys[:-1]:
                    item = item[key]
                item[keys[-1]] = value
                candidates.append(candidate)
        for field in ("internal_ports", "settings"):
            candidate = deepcopy(valid)
            candidate["nodes"][0][field].append(deepcopy(candidate["nodes"][0][field][0]))
            candidates.append(candidate)
        for same_id in (False, True):
            candidate = deepcopy(valid)
            duplicate = deepcopy(candidate["edges"][1])
            duplicate["id"] = duplicate["id"] if same_id else "duplicate-internal"
            candidate["edges"].append(duplicate)
            candidates.append(candidate)
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                for response in (
                    self.client.put("/api/wiring", json={"base_revision": saved["revision"], "site": candidate, "targets": {}}),
                    self.client.post("/api/wiring/compare", json={"site": valid, "target": candidate}),
                ):
                    self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(self.client.get("/api/wiring").json(), saved)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_internal_compare_has_separate_columns_and_raw_setting_values_without_io(self):
        site = internal_graph_data()
        site["edges"].append({"id": "retained-internal", "source": {"device_id": "source", "port_id": "control"},
                              "target": {"device_id": "source", "port_id": "feedback-input"}, "signal_type": "internal", "label": ""})
        target = deepcopy(site)
        target["nodes"][0].update(x=200, y=400)
        target["nodes"][0]["device"]["name"] = "目标设备"
        target["nodes"][0]["settings"][0]["value"] = "low"
        target["nodes"][0]["settings"][0]["options"][1]["label"] = "低显示名不作比较值"
        target["nodes"][0]["settings"][1]["value"] = "4"
        target["edges"][0].update(id="renamed-physical", label="新物理标签")
        target["edges"][1].update(id="changed-internal", target={"device_id": "source", "port_id": "feedback-input"})
        target["edges"][2].update(id="renamed-internal", label="新内部标签")
        before_inputs = deepcopy((site, target))
        path = self.root / "params" / "wiring.yaml"
        path.parent.mkdir()
        path.write_bytes(b"schema_version: 99\n")
        before = path.read_bytes(), path.stat().st_mtime_ns
        with patch.object(main, "read_wiring", side_effect=AssertionError("内部比较不得读磁盘")), \
                patch.object(main, "save_wiring", side_effect=AssertionError("内部比较不得写磁盘")):
            response = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
        self.assertEqual(response.status_code, 200, response.text)
        compared = response.json()
        self.assertEqual(compared["retained"], target["edges"][:1])
        self.assertEqual((compared["removed"], compared["added"]), ([], []))
        self.assertEqual(compared["internal_retained"], target["edges"][2:])
        self.assertEqual(compared["internal_removed"], site["edges"][1:2])
        self.assertEqual(compared["internal_added"], target["edges"][1:2])
        self.assertEqual(compared["setting_changes"], [
            {"device_id": "source", "device_name": "目标设备", "setting_id": "noise-eater", "setting_name": "Noise Eater",
             "site_value": "high", "target_value": "low"},
            {"device_id": "source", "device_name": "目标设备", "setting_id": "channel", "setting_name": "档位",
             "site_value": "1", "target_value": "4"},
        ])
        self.assertEqual((compared["mode_changes"], compared["usage_changes"], compared["site_warnings"], compared["target_warnings"]),
                         ([], [], [], []))
        self.assertEqual((site, target), before_inputs)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_snapshot_roundtrip_targets_unknown_keys_and_profiles_are_independent(self):
        request = self.request_body()
        target = request["targets"]["removed-experiment"]
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
        saved_response = self.client.put("/api/wiring", json=request)
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        saved = saved_response.json()
        self.assertEqual(saved["site"], WiringGraph.model_validate(request["site"]).model_dump())
        self.assertEqual(saved["targets"], {key: WiringGraph.model_validate(graph).model_dump() for key, graph in request["targets"].items()})
        self.assertEqual(saved, self.client.get("/api/wiring").json())
        profile_initial = self.client.get("/api/device-ports").json()
        profile = deepcopy(request["site"]["nodes"][0]["device"])
        profile["name"] = "档案后改"
        profile["ports"][1].update(name="档案端口后改", selected_mode=None)
        profile["ports"].pop(0)
        profile_response = self.client.put("/api/device-ports", json={"base_revision": profile_initial["revision"], "devices": [profile]})
        self.assertEqual(profile_response.status_code, 200, profile_response.text)
        self.assertEqual(self.client.get("/api/wiring").json(), saved)
        request["base_revision"] = saved["revision"]
        request["site"]["nodes"][0]["device"]["name"] = "现场改名"
        request["site"]["nodes"][0]["device"]["ports"][1].update(name="现场端口改名", selected_mode=None)
        updated = self.client.put("/api/wiring", json=request)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["targets"], saved["targets"])
        self.assertEqual(updated.json()["site"]["nodes"][0]["device"]["id"], "source")
        self.assertEqual(updated.json()["site"]["nodes"][0]["device"]["ports"][1]["id"], "clock_10mhz")
        self.assertEqual({path.name for path in (self.root / "params").iterdir()}, {"wiring.yaml", "device_ports.yaml"})

    def test_invalid_put_and_compare_are_422_before_creating_archive(self):
        valid = self.request_body()
        candidates = []

        def change(path, value):
            candidate = deepcopy(valid)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            candidates.append(candidate)

        for path, values in (
            (["unknown"], [True]), (["base_revision"], [1, None]),
            (["targets"], [{"": graph_data()}, {" ": graph_data()}, []]),
            (["site", "nodes", 0, "x"], [True, 20.0, "20", 1, -20, 20020]),
            (["site", "nodes", 0, "y"], [False, 0.0, 21]),
            (["site", "nodes", 0, "device", "device_library_id"], [3]),
            (["site", "nodes", 0, "follow_mapping"], ["drive", [""], [1], ["drive", "drive"]]),
            (["site", "nodes", 0, "port_directions"], [None, {"out": "out"}, {"missing": "in"}, {"clock_10mhz": "out"}]),
            (["site", "nodes", 0, "port_usage"], [None, [], {"missing": "used"}, {"in": "used"},
                                                   {"": "used"}, {" ": "used"}, {"out": None},
                                                   {"out": True}, {"out": 1}, {"out": "USED"}]),
            (["site", "nodes", 0, "device", "ports", 1, "selected_mode"], ["missing"]),
            (["site", "edges", 0, "source", "device_id"], ["missing"]),
            (["site", "edges", 0, "target", "port_id"], ["missing"]),
            (["site", "edges", 0, "source", "unknown"], [True]),
            (["site", "edges", 0, "signal_type"], ["unknown"]),
            (["site", "edges", 0, "label"], [None]),
        ):
            for value in values:
                change(path, value)
        duplicate = deepcopy(valid)
        duplicate["site"]["nodes"].append(deepcopy(duplicate["site"]["nodes"][0]))
        candidates.append(duplicate)
        duplicate_library = deepcopy(valid)
        duplicate_library["site"]["nodes"][1]["device"]["device_library_id"] = "old-library-id"
        candidates.append(duplicate_library)
        duplicate = deepcopy(valid)
        edge = deepcopy(duplicate["site"]["edges"][0])
        edge.update(id="another", signal_type="optical")
        edge["source"], edge["target"] = edge["target"], edge["source"]
        duplicate["site"]["edges"].append(edge)
        candidates.append(duplicate)
        self_edge = deepcopy(valid)
        self_edge["site"]["edges"][0]["target"] = deepcopy(self_edge["site"]["edges"][0]["source"])
        candidates.append(self_edge)
        required = deepcopy(valid)
        del required["site"]["nodes"][0]["device"]["ports"][0]["selected_mode"]
        candidates.append(required)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                response = self.client.put("/api/wiring", json=candidate)
                self.assertEqual(response.status_code, 422, response.text)
        for request in (
            {"site": valid["site"]}, {"site": valid["site"], "target": valid["site"], "unknown": True},
            {"site": self_edge["site"], "target": valid["site"]},
        ):
            response = self.client.post("/api/wiring/compare", json=request)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_usage_three_states_roundtrip_and_legacy_get_does_not_guess_or_write(self):
        request = self.request_body()
        request["site"]["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "unused"}
        request["site"]["nodes"][1]["port_usage"] = {"aux": "unconfirmed"}
        request["targets"]["removed-experiment"]["nodes"][0]["port_usage"] = {"clock_10mhz": "used"}
        request["targets"]["another-experiment"]["nodes"][0]["port_usage"] = {"clock_10mhz": "unconfirmed"}
        response = self.client.put("/api/wiring", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved, self.client.get("/api/wiring").json())
        self.assertEqual(saved["site"]["nodes"][0]["port_usage"], {"out": "used", "clock_10mhz": "unused"})
        self.assertEqual(saved["targets"]["removed-experiment"]["nodes"][0]["port_usage"], {"clock_10mhz": "used"})
        self.assertEqual(saved["targets"]["another-experiment"]["nodes"][0]["port_usage"], {"clock_10mhz": "unconfirmed"})
        path = self.root / "params" / "wiring.yaml"
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["site"]["nodes"][0]["port_usage"],
                         request["site"]["nodes"][0]["port_usage"])
        legacy = {"schema_version": 1, "site": graph_data(), "targets": {"no-lines": graph_data()}}
        legacy["targets"]["no-lines"]["edges"] = []
        path.write_text(yaml.safe_dump(legacy, allow_unicode=True), encoding="utf-8")
        before = path.read_bytes(), path.stat().st_mtime_ns
        with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("GET 不得补写使用状态")):
            loaded = self.client.get("/api/wiring")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        for graph in (loaded.json()["site"], loaded.json()["targets"]["no-lines"]):
            self.assertTrue(all(node["port_usage"] == {} for node in graph["nodes"]))
            self.assertTrue(all(node["internal_ports"] == [] and node["settings"] == [] for node in graph["nodes"]))
        self.assertEqual(loaded.json()["revision"], hashlib.sha256(before[0]).hexdigest())
        self.assertTrue(all("internal_ports" not in node and "settings" not in node
                            for graph in (legacy["site"], *legacy["targets"].values()) for node in graph["nodes"]))
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_connected_unused_returns_422_for_put_and_compare_and_keeps_old_graph(self):
        saved_response = self.client.put("/api/wiring", json=self.request_body())
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        saved = saved_response.json()
        path = self.root / "params" / "wiring.yaml"
        before = path.read_bytes(), path.stat().st_mtime_ns
        for key in ("site", "target"):
            for index, port_id in ((0, "out"), (1, "in")):
                graph = graph_data()
                graph["nodes"][index]["port_usage"] = {port_id: "unused"}
                request = self.request_body(saved["revision"])
                if key == "site":
                    request["site"] = graph
                else:
                    request["targets"]["removed-experiment"] = graph
                compare = {"site": graph_data(), "target": graph_data(), key: graph}
                with self.subTest(key=key, index=index):
                    for response in (self.client.put("/api/wiring", json=request),
                                     self.client.post("/api/wiring/compare", json=compare)):
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertIn("已有连线的物理端口不能标记为未使用", response.text)
                    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
                    self.assertEqual(self.client.get("/api/wiring").json(), saved)
                    self.assertEqual(list(path.parent.iterdir()), [path])

    def test_disk_connected_unused_is_storage_corruption_without_silent_line_deletion(self):
        request = self.request_body()
        dirty = graph_data()
        dirty["nodes"][0]["port_usage"] = {"out": "unused"}
        path = self.root / "params" / "wiring.yaml"
        path.parent.mkdir()
        path.write_text(yaml.safe_dump({"schema_version": 1, "site": dirty, "targets": {}}, allow_unicode=True), encoding="utf-8")
        before = path.read_bytes(), path.stat().st_mtime_ns
        for response in (self.client.get("/api/wiring"), self.client.put("/api/wiring", json=request)):
            self.assertEqual(response.status_code, 500, response.text)
            self.assertIn("损坏或结构非法", response.json()["detail"])
            self.assertIn("已有连线的物理端口不能标记为未使用", response.json()["detail"])
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(len(yaml.safe_load(path.read_bytes())["site"]["edges"]), 1)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_compare_reports_usage_changes_independently_and_only_for_common_ids(self):
        site = follow_graph([])
        site["nodes"][0]["port_usage"] = {"clock_10mhz": "unused"}
        site["nodes"][1]["port_usage"] = {"aux": "used"}
        target = deepcopy(site)
        target["nodes"][0]["device"]["name"] = "目标名"
        target["nodes"][0]["device"]["ports"][1].update(name="目标时钟", selected_mode="external")
        target["nodes"][0]["port_usage"] = {"clock_10mhz": "used"}
        target["nodes"][1]["port_usage"] = {"in": "unconfirmed", "aux": "unused"}
        before = deepcopy((site, target))
        response = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
        self.assertEqual(response.status_code, 200, response.text)
        compared = response.json()
        self.assertEqual(compared["usage_changes"], [
            {"device_id": "source", "device_name": "目标名", "port_id": "clock_10mhz", "port_name": "目标时钟",
             "site_usage": "unused", "target_usage": "used"},
            {"device_id": "receiver", "device_name": "接收器", "port_id": "aux", "port_name": "aux",
             "site_usage": "used", "target_usage": "unused"},
        ])
        self.assertEqual([change["port_id"] for change in compared["mode_changes"]], ["clock_10mhz"])
        self.assertEqual(compared["retained"], target["edges"])
        self.assertEqual((site, target), before)
        target["nodes"][0]["device"]["id"] = "replacement-source"
        target["edges"][0]["source"]["device_id"] = "replacement-source"
        target["nodes"][1]["device"]["ports"][1]["id"] = "replacement-aux"
        target["nodes"][1]["port_usage"] = {"in": "unconfirmed", "replacement-aux": "unused"}
        response = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["usage_changes"], [])
        self.assertEqual(response.json()["mode_changes"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_compare_uses_binary_usage_for_legacy_states_and_both_edge_endpoints(self):
        states = (
            (False, None, "unused"), (False, "unconfirmed", "unused"),
            (False, "unused", "unused"), (False, "used", "used"),
            (True, None, "used"), (True, "unconfirmed", "used"), (True, "used", "used"),
        )
        for site_connected, site_raw, site_usage in states:
            for target_connected, target_raw, target_usage in states:
                with self.subTest(site=(site_connected, site_raw), target=(target_connected, target_raw)):
                    site, target = graph_data(), graph_data()
                    for graph, connected, raw in ((site, site_connected, site_raw), (target, target_connected, target_raw)):
                        if not connected:
                            graph["edges"] = []
                        if raw is not None:
                            graph["nodes"][0]["port_usage"] = {"out": raw}
                            graph["nodes"][1]["port_usage"] = {"in": raw}
                    if target_connected:
                        edge = target["edges"][0]
                        edge["source"], edge["target"] = edge["target"], edge["source"]
                    before = deepcopy((site, target))
                    response = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
                    self.assertEqual(response.status_code, 200, response.text)
                    compared = response.json()
                    expected = [
                        {"device_id": "source", "device_name": "信号源", "port_id": "out", "port_name": "out",
                         "site_usage": site_usage, "target_usage": target_usage},
                        {"device_id": "receiver", "device_name": "接收器", "port_id": "in", "port_name": "in",
                         "site_usage": site_usage, "target_usage": target_usage},
                    ] if site_usage != target_usage else []
                    self.assertEqual(compared["usage_changes"], expected)
                    self.assertEqual(compared["mode_changes"], [])
                    self.assertEqual(compared["retained"], target["edges"] if site_connected and target_connected else [])
                    self.assertEqual(compared["removed"], site["edges"] if not target_connected else [])
                    self.assertEqual(compared["added"], target["edges"] if not site_connected else [])
                    self.assertEqual((site, target), before)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_warnings_do_not_block_put_and_modes_are_graph_local(self):
        request = self.request_body()
        request["site"]["nodes"][0]["device"]["ports"][0].update(direction="in", signal_type="optical")
        target = request["targets"]["removed-experiment"]
        target["edges"][0]["source"]["port_id"] = "clock_10mhz"
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = None
        target["nodes"][1]["device"]["ports"][0].update(direction=None, signal_type=None)
        response = self.client.put("/api/wiring", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual({warning["code"] for warning in saved["diagnostics"]["site"]}, {
            "source_direction_conflict", "source_signal_type_conflict",
        })
        self.assertEqual({warning["code"] for warning in saved["diagnostics"]["targets"]["removed-experiment"]}, {
            "source_mode_unconfirmed", "target_direction_unknown", "target_signal_type_unknown",
        })
        self.assertEqual(saved["diagnostics"]["targets"]["another-experiment"], [])
        self.assertEqual(saved, self.client.get("/api/wiring").json())

    def test_compare_is_pure_with_reversed_edges_mode_diff_and_corrupt_storage(self):
        site = graph_data()
        target = deepcopy(site)
        target["nodes"][0].update(x=20000, y=0)
        target["nodes"][0]["device"]["name"] = "目标名"
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
        edge = target["edges"][0]
        edge.update(id="target-id", label="新标签")
        edge["source"], edge["target"] = edge["target"], edge["source"]
        path = self.root / "params" / "wiring.yaml"
        path.parent.mkdir()
        before = b"schema_version: 99\n"
        path.write_bytes(before)
        with patch.object(main, "read_wiring", side_effect=AssertionError("比较不得读取磁盘")):
            with patch.object(main, "save_wiring", side_effect=AssertionError("比较不得写入磁盘")):
                response = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
        self.assertEqual(response.status_code, 200, response.text)
        comparison = response.json()
        self.assertEqual(comparison["retained"], target["edges"])
        self.assertEqual((comparison["removed"], comparison["added"]), ([], []))
        self.assertEqual(comparison["mode_changes"], [{
            "device_id": "source", "device_name": "目标名", "port_id": "clock_10mhz", "port_name": "10 MHz",
            "site_mode": "内部参考时钟（输出）", "target_mode": "外部参考时钟（输入）",
        }])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_stale_and_concurrent_requests_return_409_with_one_winner(self):
        initial_request = self.request_body()
        saved = self.client.put("/api/wiring", json=initial_request)
        self.assertEqual(saved.status_code, 200, saved.text)
        conflict = self.client.put("/api/wiring", json=initial_request)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.headers["X-Error-Code"], "revision_conflict")
        self.assertEqual(self.client.get("/api/wiring").json(), saved.json())
        gate = threading.Barrier(2)

        def competing(name):
            request = self.request_body(saved.json()["revision"])
            request["site"]["nodes"][0]["device"]["name"] = name
            gate.wait(timeout=5)
            return self.client.put("/api/wiring", json=request)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(competing, ["并发甲", "并发乙"]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        losing = next(response for response in responses if response.status_code == 409)
        self.assertEqual(losing.headers["X-Error-Code"], "revision_conflict")
        winner = next(response for response in responses if response.status_code == 200)
        self.assertEqual(winner.json(), self.client.get("/api/wiring").json())

    def test_corrupt_and_unknown_version_return_500_and_cannot_be_overwritten(self):
        request = self.request_body()
        path = self.root / "params" / "wiring.yaml"
        path.parent.mkdir()
        for content in (b"site: [", b"schema_version: 99\nsite: {nodes: [], edges: []}\ntargets: {}\n",
                        b"schema_version: true\nsite: {nodes: [], edges: []}\ntargets: {}\n",
                        b"schema_version: 1.0\nsite: {nodes: [], edges: []}\ntargets: {}\n"):
            with self.subTest(content=content):
                path.write_bytes(content)
                for response in (self.client.get("/api/wiring"), self.client.put("/api/wiring", json=request)):
                    self.assertEqual(response.status_code, 500, response.text)
                    self.assertIn("接线图", response.json()["detail"])
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_get_projects_all_graphs_read_only_and_mapping_save_invalidates_revision(self):
        payloads = write_follow_fixture(self.root)
        params = self.root / "params"
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in params.iterdir()}
        with patch("lab_workflows.instrument_config._process_config_lock", side_effect=AssertionError("GET 不得创建配置锁")):
            response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([status["state"] for status in result["follow_status"]["site"]], ["updated", "independent"])
        self.assertEqual(result["site"]["nodes"][0]["device"]["device_library_id"], "new-library-id")
        self.assertEqual(result["site"]["nodes"][0]["follow_mapping"], ["drive"])
        for key in payloads["wiring.yaml"]["targets"]:
            self.assertEqual(result["targets"][key]["nodes"][0]["device"]["device_library_id"], "new-library-id")
            self.assertEqual(result["follow_status"]["targets"][key][0]["state"], "updated")
        self.assertEqual(result["mapping_groups"], [{"keys": ["drive"], "label": "驱动", "device_library_id": "new-library-id"}])
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before})
        self.assertEqual(set(params.iterdir()), set(before))
        mapping_path = params / "mapping.yaml"
        mapping_path.write_bytes(mapping_path.read_bytes() + b"\n# mapping saved elsewhere\n")
        conflict = self.client.put("/api/wiring", json={"base_revision": result["revision"], "site": result["site"], "targets": result["targets"]})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.headers["X-Error-Code"], "revision_conflict")
        self.assertEqual((params / "wiring.yaml").read_bytes(), before[params / "wiring.yaml"][0])

    def test_blocked_site_is_atomic_but_targets_project_and_bad_config_is_not_empty(self):
        payloads = write_follow_fixture(self.root)
        payloads["wiring.yaml"]["site"]["nodes"][1]["follow_mapping"] = ["gone"]
        wiring_path = self.root / "params" / "wiring.yaml"
        wiring_path.write_text(yaml.safe_dump(payloads["wiring.yaml"], allow_unicode=True), encoding="utf-8")
        before = wiring_path.read_bytes()
        response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["site"], WiringGraph.model_validate(payloads["wiring.yaml"]["site"]).model_dump())
        self.assertEqual([status["state"] for status in result["follow_status"]["site"]], ["blocked", "blocked"])
        self.assertTrue(all("未同步" in status["message"] for status in result["follow_status"]["site"]))
        self.assertEqual(result["targets"]["removed-experiment"]["nodes"][0]["device"]["device_library_id"], "new-library-id")
        (self.root / "params" / "mapping.yaml").write_bytes(b"mapping: []\n")
        malformed = self.client.get("/api/wiring")
        self.assertEqual(malformed.status_code, 200, malformed.text)
        self.assertEqual(malformed.json()["follow_status"]["site"][0]["state"], "blocked")
        self.assertIn("不可用", malformed.json()["follow_status"]["site"][0]["message"])
        self.assertEqual(wiring_path.read_bytes(), before)
        self.assertEqual(len(list(wiring_path.parent.iterdir())), 4)

    def test_follow_migration_and_atomic_graph_failure_keep_usage_and_stable_ids(self):
        graph = internal_graph_data()
        graph["nodes"][0]["follow_mapping"] = ["drive"]
        graph["nodes"][0]["device"]["ports"][0]["direction"] = "inout"
        graph["nodes"][0]["port_directions"] = {"out": "out"}
        graph["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "unused"}
        graph["nodes"][1]["port_usage"] = {"in": "unconfirmed", "aux": "used"}
        payloads = write_follow_fixture(self.root, graph)
        target = payloads["wiring.yaml"]["targets"]["removed-experiment"]
        target["nodes"][0]["port_usage"]["clock_10mhz"] = "unconfirmed"
        target["nodes"][0]["settings"][0]["value"] = "low"
        target["nodes"][0]["settings"][1]["value"] = "4"
        path = self.root / "params" / "wiring.yaml"
        path.write_text(yaml.safe_dump(payloads["wiring.yaml"], allow_unicode=True), encoding="utf-8")
        before = {candidate: (candidate.read_bytes(), candidate.stat().st_mtime_ns) for candidate in path.parent.iterdir()}
        response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        projected = response.json()
        self.assertEqual(projected["follow_status"]["site"][0]["state"], "updated")
        self.assertEqual(projected["site"]["nodes"][0]["device"]["device_library_id"], "new-library-id")
        self.assertEqual(projected["site"]["nodes"][0]["port_usage"], graph["nodes"][0]["port_usage"])
        self.assertEqual(projected["site"]["nodes"][1]["port_usage"], graph["nodes"][1]["port_usage"])
        self.assertEqual(projected["targets"]["removed-experiment"]["nodes"][0]["port_usage"],
                         {"out": "used", "clock_10mhz": "unconfirmed"})
        self.assertEqual([port["id"] for port in projected["site"]["nodes"][0]["device"]["ports"]], ["out", "clock_10mhz"])
        self.assertEqual(projected["site"]["nodes"][0]["port_directions"], {"out": "out"})
        self.assertEqual(projected["site"]["edges"], graph["edges"])
        self.assertEqual(projected["site"]["nodes"][0]["internal_ports"], graph["nodes"][0]["internal_ports"])
        self.assertEqual(projected["site"]["nodes"][0]["settings"], graph["nodes"][0]["settings"])
        self.assertEqual(projected["targets"]["removed-experiment"]["nodes"][0]["settings"], target["nodes"][0]["settings"])
        self.assertEqual(before, {candidate: (candidate.read_bytes(), candidate.stat().st_mtime_ns) for candidate in before})
        response = self.client.put("/api/wiring", json={"base_revision": projected["revision"],
                                                       "site": projected["site"], "targets": projected["targets"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/wiring").json()["site"], projected["site"])
        payloads["wiring.yaml"]["site"]["nodes"][1]["follow_mapping"] = ["gone"]
        path.write_text(yaml.safe_dump(payloads["wiring.yaml"], allow_unicode=True), encoding="utf-8")
        unchanged = path.read_bytes(), path.stat().st_mtime_ns
        response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        blocked = response.json()
        expected = WiringGraph.model_validate(payloads["wiring.yaml"]["site"]).model_dump()
        self.assertEqual(blocked["site"], expected)
        self.assertEqual([status["state"] for status in blocked["follow_status"]["site"]], ["blocked", "blocked"])
        self.assertEqual(blocked["targets"]["removed-experiment"]["nodes"][0]["device"]["device_library_id"], "new-library-id")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), unchanged)
        response = self.client.put("/api/wiring", json={"base_revision": blocked["revision"],
                                                       "site": blocked["site"], "targets": blocked["targets"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["site"], expected)
        self.assertEqual(self.client.get("/api/wiring").json()["site"], expected)
        receiver = payloads["wiring.yaml"]["site"]["nodes"][1]
        receiver["follow_mapping"] = []
        receiver["device"]["device_library_id"] = "new-library-id"
        path.write_text(yaml.safe_dump(payloads["wiring.yaml"], allow_unicode=True), encoding="utf-8")
        before_collision = path.read_bytes(), path.stat().st_mtime_ns
        response = self.client.get("/api/wiring")
        self.assertEqual(response.status_code, 200, response.text)
        collision = response.json()
        self.assertEqual(collision["site"], WiringGraph.model_validate(payloads["wiring.yaml"]["site"]).model_dump())
        self.assertEqual(collision["site"]["nodes"][0]["port_usage"], graph["nodes"][0]["port_usage"])
        self.assertIn("迁移后的设备库关联冲突", collision["follow_status"]["site"][0]["message"])
        self.assertEqual(collision["targets"]["removed-experiment"]["nodes"][0]["device"]["device_library_id"], "new-library-id")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before_collision)
        response = self.client.put("/api/wiring", json={"base_revision": collision["revision"],
                                                       "site": collision["site"], "targets": collision["targets"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["site"], collision["site"])

    def test_bad_config_with_empty_or_independent_graph_returns_explicit_500(self):
        write_follow_fixture(self.root)
        wiring_path = self.root / "params" / "wiring.yaml"
        for graph in ({"nodes": [], "edges": []}, follow_graph([])):
            graph = deepcopy(graph)
            for node in graph["nodes"]:
                node["follow_mapping"] = []
            wiring_path.write_text(yaml.safe_dump({"schema_version": 1, "site": graph, "targets": {}}), encoding="utf-8")
            (wiring_path.parent / "mapping.yaml").write_bytes(b"mapping: []\n")
            before = wiring_path.read_bytes()
            response = self.client.get("/api/wiring")
            self.assertEqual(response.status_code, 500, response.text)
            self.assertIn("用途配置不可用", response.json()["detail"])
            self.assertEqual(wiring_path.read_bytes(), before)
            self.assertEqual(len(list(wiring_path.parent.iterdir())), 4)

    def test_compare_uses_graph_working_direction_and_put_rechecks_dependencies(self):
        site = follow_graph([])
        target = deepcopy(site)
        target["nodes"][0]["port_directions"]["out"] = "in"
        compared = self.client.post("/api/wiring/compare", json={"site": site, "target": target})
        self.assertEqual(compared.status_code, 200, compared.text)
        self.assertEqual(compared.json()["mode_changes"][0]["site_mode"], "输出")
        self.assertEqual(compared.json()["mode_changes"][0]["target_mode"], "输入")
        self.assertEqual(compared.json()["target_warnings"][0]["code"], "source_direction_conflict")
        self.assertEqual(list(self.root.iterdir()), [])
        write_follow_fixture(self.root)
        initial = self.client.get("/api/wiring").json()
        wiring_path = self.root / "params" / "wiring.yaml"
        before = wiring_path.read_bytes()
        mapping_path = wiring_path.parent / "mapping.yaml"
        original_mapping = mapping_path.read_bytes()

        def change_mapping(_descriptor):
            mapping_path.write_bytes(original_mapping + b"\n# changed during put\n")

        with patch("lab_workflows.wiring.os.fsync", side_effect=change_mapping):
            response = self.client.put("/api/wiring", json={"base_revision": initial["revision"], "site": initial["site"], "targets": initial["targets"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.headers["X-Error-Code"], "revision_conflict")
        self.assertEqual(wiring_path.read_bytes(), before)
        self.assertFalse(list(wiring_path.parent.glob(".wiring.yaml.*.tmp")))

    def test_read_write_failures_return_500_and_preserve_existing_bytes(self):
        saved = self.client.put("/api/wiring", json=self.request_body())
        self.assertEqual(saved.status_code, 200, saved.text)
        path = self.root / "params" / "wiring.yaml"
        before = path.read_bytes()
        request = self.request_body(saved.json()["revision"])
        with patch("lab_workflows.wiring.os.replace", side_effect=OSError("模拟磁盘失败")):
            response = self.client.put("/api/wiring", json=request)
        self.assertEqual(response.status_code, 500, response.text)
        self.assertIn("写入失败", response.json()["detail"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])
        with patch.object(Path, "read_bytes", side_effect=OSError("模拟读取失败")):
            for response in (self.client.get("/api/wiring"), self.client.put("/api/wiring", json=request)):
                self.assertEqual(response.status_code, 500, response.text)
                self.assertIn("读取失败", response.json()["detail"])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
