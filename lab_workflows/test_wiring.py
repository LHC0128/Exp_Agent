"""人工接线图的纯快照、比较与临时目录存储回归。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from pydantic import ValidationError
import yaml

from lab_workflows.device_ports import (
    DevicePortsBody, PhysicalPort, device_port_presets, read_device_ports, save_device_ports,
)
from lab_workflows.wiring import (
    RevisionConflict, WiringBody, WiringCableChange, WiringCompareBody, WiringComparison, WiringGraph, WiringModeChange,
    WiringResponse, WiringSettingChange, WiringStorageError, WiringUsageChange, compare_wiring, read_wiring, save_wiring,
)


def port_data(port_id="out", direction="out", signal_type="electrical") -> dict:
    """固定方向、未知方向和人工切换端口共用的完整字段。"""
    return {
        "id": port_id, "name": port_id, "group": "", "signal_type": signal_type,
        "connector": "", "purpose": "", "direction": direction, "modes": [], "selected_mode": None,
    }


def graph_data() -> dict:
    """构造含人工可切换端口、未关联设备库的最小双设备图。"""
    clock = device_port_presets()[0].ports[-1].model_dump()
    clock["selected_mode"] = "internal"
    return {
        "nodes": [
            {"device": {
                "id": "source", "name": "信号源", "model": "DG4000", "device_library_id": "old-library-id",
                "ports": [port_data(), clock],
            }, "x": 0, "y": 20},
            {"device": {
                "id": "receiver", "name": "接收器", "model": "", "device_library_id": None,
                "ports": [port_data("in", "in"), port_data("aux", "inout")],
            }, "x": 400, "y": 20000},
        ],
        "edges": [{
            "id": "edge-1", "source": {"device_id": "source", "port_id": "out"},
            "target": {"device_id": "receiver", "port_id": "in"}, "signal_type": "electrical", "label": "测试线",
        }],
    }


def internal_graph_data() -> dict:
    """内部信号和图内设置独立于物理口，供共享/API 公共边界回归。"""
    graph = graph_data()
    graph["nodes"][0].update(
        internal_ports=[
            {"id": "aux-monitor", "name": "Aux 内部回读", "direction": "out"},
            {"id": "demod-input", "name": "解调器输入", "direction": "in"},
            {"id": "control", "name": "内部控制", "direction": "out"},
            {"id": "feedback-input", "name": "反馈输入", "direction": "in"},
        ],
        settings=[
            {"id": "noise-eater", "name": "Noise Eater", "options": [
                {"value": "high", "label": "高"}, {"value": "low", "label": "低"}], "value": "high"},
            {"id": "channel", "name": "档位", "options": [
                {"value": str(index), "label": str(index)} for index in range(1, 5)], "value": "1"},
        ],
    )
    graph["edges"].append({
        "id": "internal-1", "source": {"device_id": "source", "port_id": "aux-monitor"},
        "target": {"device_id": "source", "port_id": "demod-input"}, "signal_type": "internal", "label": "内部回读",
    })
    return graph


def request_data(revision="base") -> dict:
    return {"base_revision": revision, "site": graph_data(), "targets": {"removed-experiment": graph_data()}}


def follow_graph(keys=None) -> dict:
    """仪器节点可绑定；未关联实库的接收器保留手工节点语义。"""
    graph = graph_data()
    graph["nodes"][0]["follow_mapping"] = keys
    graph["nodes"][0]["device"]["ports"][0]["direction"] = "inout"
    graph["nodes"][0]["port_directions"] = {"out": "out"}
    return graph


def write_follow_fixture(root: Path, graph: dict | None = None, destination="new-library-id") -> dict:
    """仅在临时根目录准备真实格式配置，不调用配置写入口或硬件。"""
    graph = follow_graph(["drive"]) if graph is None else deepcopy(graph)
    params = root / "params"
    params.mkdir(exist_ok=True)
    devices = {key: {"instrument": "signal_generator", "model": "DG4000", "label": key}
               for key in ("old-library-id", "new-library-id", "other-library-id")}
    mapping = {"schema_version": 2, "mapping": {"drive": {"device_id": destination, "label": "驱动"}},
               "constraints": {"shared_channel_groups": {}, "colocation_groups": {}}}
    profile = deepcopy(graph["nodes"][0]["device"])
    profile.update(id="new-profile", device_library_id="new-library-id", name="新信号源", model=" dg4000 ")
    for port in profile["ports"]:
        port["id"] = "new-" + port["id"]
        for mode in port["modes"]:
            mode["id"] = "new-" + mode["id"]
        if port["selected_mode"] is not None:
            port["selected_mode"] = "new-" + port["selected_mode"]
    payloads = {
        "devices.yaml": {"schema_version": 1, "network_resources": [], "devices": devices},
        "mapping.yaml": mapping,
        "device_ports.yaml": {"schema_version": 1, "devices": [profile]},
        "wiring.yaml": {"schema_version": 1, "site": graph,
                        "targets": {"removed-experiment": deepcopy(graph), "another-experiment": deepcopy(graph)}},
    }
    for name, document in payloads.items():
        (params / name).write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return payloads


class WiringModelTests(unittest.TestCase):
    def test_internal_ports_and_settings_are_graph_local_with_legacy_defaults(self):
        data = graph_data()
        data["nodes"][0].update(
            internal_ports=[{"id": "aux-monitor", "name": "Aux 内部回读", "direction": "out"},
                            {"id": "demod-input", "name": "解调器输入", "direction": "in"}],
            settings=[{"id": "noise-eater", "name": "Noise Eater", "options": [
                {"value": "high", "label": "高"}, {"value": "low", "label": "低"}], "value": "high"},
                {"id": "channel", "name": "档位", "options": [
                    {"value": str(index), "label": str(index)} for index in range(1, 5)]}],
        )
        before = deepcopy(data)
        graph = WiringGraph.model_validate(data)
        self.assertEqual([port.model_dump() for port in graph.nodes[0].internal_ports],
                         data["nodes"][0]["internal_ports"])
        self.assertEqual([setting.value for setting in graph.nodes[0].settings], ["high", None])
        self.assertEqual(graph.nodes[1].internal_ports, [])
        self.assertEqual(graph.nodes[1].settings, [])
        body = WiringBody(base_revision="base", site=graph, targets={"target": graph})
        body.site.nodes[0].settings[0].value = "low"
        body.site.nodes[1].internal_ports.append(graph.nodes[0].internal_ports[0])
        self.assertEqual(body.targets["target"].nodes[0].settings[0].value, "high")
        self.assertEqual(body.targets["target"].nodes[1].internal_ports, [])
        self.assertEqual(graph.nodes[0].settings[0].value, "high")
        self.assertEqual(graph.nodes[1].internal_ports, [])
        self.assertEqual(data, before)

    def test_internal_and_setting_choices_reject_invalid_shapes_ids_and_values(self):
        valid = internal_graph_data()
        valid["edges"] = graph_data()["edges"]
        self.assertEqual(WiringGraph.model_validate(valid).nodes[0].settings[1].value, "1")
        for path, values in (
            (["internal_ports"], [None, (), {}]),
            (["internal_ports", 0, "id"], [1, True, None, "", " ", "out"]),
            (["internal_ports", 0, "name"], [1, True, None, "", " "]),
            (["internal_ports", 0, "direction"], [1, None, "inout", "unknown"]),
            (["internal_ports", 0, "unknown"], [True]),
            (["settings"], [None, (), {}]),
            (["settings", 0, "id"], [1, True, None, "", " "]),
            (["settings", 0, "name"], [1, True, None, "", " "]),
            (["settings", 0, "options"], [None, (), [], {}]),
            (["settings", 0, "options", 0, "value"], [1, True, None, "", " "]),
            (["settings", 0, "options", 0, "label"], [1, True, None, "", " "]),
            (["settings", 0, "options", 0, "unknown"], [True]),
            (["settings", 0, "value"], [1, True, "", " ", "missing"]),
            (["settings", 0, "unknown"], [True]),
            (["port_usage"], [{"aux-monitor": "used"}]),
            (["port_directions"], [{"aux-monitor": "out"}]),
        ):
            for value in values:
                candidate = deepcopy(valid)
                item = candidate["nodes"][0]
                for key in path[:-1]:
                    item = item[key]
                item[path[-1]] = value
                with self.subTest(path=path, value=value), self.assertRaises(ValidationError):
                    WiringGraph.model_validate(candidate)
        for path in (("internal_ports", 0, "id"), ("internal_ports", 0, "name"),
                     ("internal_ports", 0, "direction"), ("settings", 0, "id"),
                     ("settings", 0, "name"), ("settings", 0, "options")):
            candidate = deepcopy(valid)
            del candidate["nodes"][0][path[0]][path[1]][path[2]]
            with self.subTest(missing=path), self.assertRaises(ValidationError):
                WiringGraph.model_validate(candidate)
        for field in ("internal_ports", "settings"):
            candidate = deepcopy(valid)
            candidate["nodes"][0][field].append(deepcopy(candidate["nodes"][0][field][0]))
            with self.subTest(duplicate=field), self.assertRaises(ValidationError):
                WiringGraph.model_validate(candidate)
        candidate = deepcopy(valid)
        candidate["nodes"][0]["settings"][0]["options"][1]["value"] = "high"
        with self.assertRaises(ValidationError):
            WiringGraph.model_validate(candidate)
        graph = WiringGraph.model_validate(valid)
        graph.nodes[0].settings[0].value = "not-an-option"
        with self.assertRaises(ValidationError):
            WiringGraph.model_validate(graph)

    def test_internal_edges_stay_on_one_device_out_to_in_without_physical_mix(self):
        valid = internal_graph_data()
        valid["nodes"][1]["internal_ports"] = deepcopy(valid["nodes"][0]["internal_ports"])
        valid["nodes"][1]["settings"] = deepcopy(valid["nodes"][0]["settings"])
        valid["nodes"][0]["port_usage"] = {"clock_10mhz": "unused"}
        valid["edges"].extend([
            {"id": "fanout-internal", "source": {"device_id": "source", "port_id": "aux-monitor"},
             "target": {"device_id": "source", "port_id": "feedback-input"}, "signal_type": "internal", "label": ""},
            {"id": "another-internal", "source": {"device_id": "source", "port_id": "control"},
             "target": {"device_id": "source", "port_id": "demod-input"}, "signal_type": "internal", "label": ""},
        ])
        before = deepcopy(valid)
        self.assertEqual(len(WiringGraph.model_validate(valid).edges), 4)
        for endpoint, key, values in (
            ("source", "device_id", ["receiver", "missing"]),
            ("target", "device_id", ["receiver", "missing"]),
            ("source", "port_id", ["out", "clock_10mhz", "missing", "demod-input"]),
            ("target", "port_id", ["out", "clock_10mhz", "missing", "control", "aux-monitor"]),
        ):
            for value in values:
                candidate = deepcopy(valid)
                candidate["edges"][1][endpoint][key] = value
                with self.subTest(endpoint=endpoint, key=key, value=value), self.assertRaises(ValidationError):
                    WiringGraph.model_validate(candidate)
        for signal_type in ("optical", "electrical"):
            for physical_source, physical_target in ((False, False), (True, False), (False, True)):
                candidate = deepcopy(valid)
                edge = candidate["edges"][1]
                edge["signal_type"] = signal_type
                if physical_source:
                    edge["source"]["port_id"] = "out"
                if physical_target:
                    edge["target"]["port_id"] = "clock_10mhz"
                with self.subTest(signal_type=signal_type, physical=(physical_source, physical_target)), self.assertRaises(ValidationError):
                    WiringGraph.model_validate(candidate)
        for duplicate_id, reverse in ((False, False), (True, False), (False, True)):
            candidate = deepcopy(valid)
            extra = deepcopy(candidate["edges"][1])
            if not duplicate_id:
                extra["id"] = "duplicate-internal"
            if reverse:
                extra["source"], extra["target"] = extra["target"], extra["source"]
            candidate["edges"].append(extra)
            with self.subTest(duplicate_id=duplicate_id, reverse=reverse), self.assertRaises(ValidationError):
                WiringGraph.model_validate(candidate)
        physical_only = deepcopy(valid)
        physical_only["edges"][0]["signal_type"] = "internal"
        with self.assertRaises(ValidationError):
            WiringGraph.model_validate(physical_only)
        self.assertEqual(valid, before)

    def test_strict_types_extra_fields_required_nullable_and_grid_are_enforced(self):
        valid = request_data()
        self.assertEqual(WiringBody.model_validate(valid).site.nodes[0].x, 0)
        invalid = []

        def change(path, value):
            candidate = deepcopy(valid)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            invalid.append(candidate)

        for path, values in (
            (["base_revision"], [1, " ", None]),
            (["unknown"], [True]),
            (["site", "unknown"], [True]),
            (["site", "nodes"], [(), {}]),
            (["site", "edges"], [()]),
            (["site", "nodes", 0, "unknown"], [True]),
            (["site", "nodes", 0, "x"], [True, False, 0.0, 20.0, "20", -20, 1, 20020]),
            (["site", "nodes", 0, "y"], [True, 20.0, 21, -1, 20001]),
            (["site", "nodes", 0, "device", "unknown"], [True]),
            (["site", "nodes", 0, "device", "id"], [1, " "]),
            (["site", "nodes", 0, "device", "device_library_id"], [1, ""]),
            (["site", "nodes", 0, "device", "ports", 0, "signal_type"], ["unknown"]),
            (["site", "nodes", 0, "device", "ports", 0, "unknown"], [True]),
            (["site", "nodes", 0, "device", "ports", 1, "modes", 0, "unknown"], [True]),
            (["site", "nodes", 0, "device", "ports", 1, "selected_mode"], [1, "missing"]),
            (["site", "edges", 0, "unknown"], [True]),
            (["site", "edges", 0, "id"], [1, ""]),
            (["site", "edges", 0, "label"], [None, 1]),
            (["site", "edges", 0, "signal_type"], ["unknown", None]),
            (["site", "edges", 0, "source", "device_id"], [1, ""]),
            (["site", "edges", 0, "source", "port_id"], [None, " "]),
            (["site", "edges", 0, "source", "unknown"], [True]),
            (["targets"], [[], {"": graph_data()}, {" ": graph_data()}, {1: graph_data()}]),
        ):
            for value in values:
                change(path, value)
        for field in ("signal_type", "direction", "selected_mode"):
            candidate = deepcopy(valid)
            del candidate["site"]["nodes"][0]["device"]["ports"][0][field]
            invalid.append(candidate)
        candidate = deepcopy(valid)
        del candidate["site"]["nodes"][0]["device"]["device_library_id"]
        invalid.append(candidate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                WiringBody.model_validate(candidate)
        for field in ("site", "target"):
            with self.subTest(compare_missing=field), self.assertRaises(ValidationError):
                WiringCompareBody.model_validate({key: graph_data() for key in ("site", "target") if key != field})
        with self.assertRaises(ValidationError):
            WiringCompareBody.model_validate({"site": graph_data(), "target": graph_data(), "unknown": True})

    def test_follow_choices_and_working_directions_are_strict_and_graph_local(self):
        graph = WiringGraph.model_validate(follow_graph(["drive"]))
        self.assertEqual(graph.nodes[0].follow_mapping, ["drive"])
        self.assertEqual(graph.nodes[0].port_directions, {"out": "out"})
        self.assertIsNone(WiringGraph.model_validate(graph_data()).nodes[0].follow_mapping)
        self.assertEqual(WiringGraph.model_validate(graph_data()).nodes[0].port_directions, {})
        for field, values in (
            ("follow_mapping", ["drive", (), [""], [" "], [1], ["drive", "drive"]]),
            ("port_directions", [None, [], {"missing": "in"}, {"clock_10mhz": "in"},
                                 {"out": "inout"}, {"out": None}, {"out": 1}, {1: "in"}]),
        ):
            for value in values:
                candidate = follow_graph()
                candidate["nodes"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    WiringGraph.model_validate(candidate)
        candidate = graph_data()
        candidate["nodes"][0]["port_directions"] = {"out": "out"}
        with self.assertRaises(ValidationError):
            WiringGraph.model_validate(candidate)
        response = WiringResponse.model_validate({
            "schema_version": 1, "revision": "base", "site": graph_data(), "targets": {},
            "diagnostics": {"site": [], "targets": {}},
        })
        self.assertEqual(response.mapping_groups, [])
        self.assertEqual(response.follow_status.model_dump(), {"site": [], "targets": {}})

    def test_port_usage_is_graph_local_three_state_and_missing_is_not_materialized(self):
        original = graph_data()
        graph = WiringGraph.model_validate(original)
        body = WiringBody(base_revision="base", site=graph, targets={
            "used": graph, "unused": graph, "unconfirmed": graph,
        })
        for usage, target in body.targets.items():
            target.nodes[0].port_usage["clock_10mhz"] = usage
        body.site.nodes[0].port_usage["out"] = "used"
        validated = WiringBody.model_validate(body.model_dump())
        self.assertEqual(validated.site.nodes[0].port_usage, {"out": "used"})
        for usage, target in validated.targets.items():
            self.assertEqual(target.nodes[0].port_usage, {"clock_10mhz": usage})
            self.assertEqual(target.nodes[0].device.ports[1].selected_mode, "internal")
            self.assertEqual(target.nodes[0].port_directions, {})
        self.assertEqual(graph.nodes[0].port_usage, {})
        self.assertNotIn("port_usage", original["nodes"][0])
        disconnected = graph_data()
        disconnected["edges"] = []
        self.assertTrue(all(node.port_usage == {} for node in WiringGraph.model_validate(disconnected).nodes))
        self.assertNotIn("clock_10mhz", graph.nodes[0].port_usage)
        self.assertIsNone(graph.nodes[0].port_usage.get("clock_10mhz"))

    def test_port_usage_rejects_unknown_keys_and_nonliteral_values(self):
        for value in (None, [], (), "used", {"missing": "used"}, {"in": "used"},
                      {"": "used"}, {" ": "used"}, {1: "used"}, {"out": None},
                      {"out": True}, {"out": 1}, {"out": "USED"}, {"out": "unknown"}):
            candidate = graph_data()
            candidate["nodes"][0]["port_usage"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WiringGraph.model_validate(candidate)

    def test_either_connected_endpoint_cannot_be_explicitly_unused(self):
        for index, port_id in ((0, "out"), (1, "in")):
            for usage in ("used", "unconfirmed", "unused"):
                candidate = graph_data()
                candidate["nodes"][index]["port_usage"] = {port_id: usage}
                with self.subTest(index=index, usage=usage):
                    if usage == "unused":
                        with self.assertRaisesRegex(ValidationError, "已有连线的物理端口不能标记为未使用"):
                            WiringGraph.model_validate(candidate)
                    else:
                        self.assertEqual(len(WiringGraph.model_validate(candidate).edges), 1)
        graph = WiringGraph.model_validate(graph_data())
        graph.nodes[0].port_usage["out"] = "unused"
        with self.assertRaises(ValidationError):
            WiringGraph.model_validate(graph)

    def test_usage_comparison_model_default_and_literals_are_strict(self):
        comparison = {"retained": [], "removed": [], "added": [], "mode_changes": [],
                      "site_warnings": [], "target_warnings": []}
        first = WiringComparison.model_validate(comparison)
        second = WiringComparison.model_validate(comparison)
        self.assertEqual(first.usage_changes, [])
        valid = {"device_id": "d", "device_name": "", "port_id": "p", "port_name": "",
                 "site_usage": "used", "target_usage": "unused"}
        first.usage_changes.append(WiringUsageChange.model_validate(valid))
        self.assertEqual(second.usage_changes, [])
        for field in ("site_usage", "target_usage"):
            missing = deepcopy(valid)
            del missing[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                WiringUsageChange.model_validate(missing)
            for value in (None, 1, True, "unknown", "USED"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    WiringUsageChange.model_validate({**valid, field: value})

    def test_internal_comparison_defaults_are_isolated_and_setting_change_values_stay_strict(self):
        legacy = {"retained": [], "removed": [], "added": [], "mode_changes": [],
                  "site_warnings": [], "target_warnings": []}
        first = WiringComparison.model_validate(legacy)
        second = WiringComparison.model_validate(legacy)
        for field in ("internal_retained", "internal_removed", "internal_added"):
            self.assertEqual(getattr(first, field), [])
            getattr(first, field).append(WiringGraph.model_validate(internal_graph_data()).edges[1])
            self.assertEqual(getattr(second, field), [])
        valid = {"device_id": "d", "device_name": "设备", "setting_id": "s", "setting_name": "设置",
                 "site_value": None, "target_value": "raw-value"}
        self.assertEqual(first.setting_changes, [])
        first.setting_changes.append(WiringSettingChange.model_validate(valid))
        self.assertEqual(second.setting_changes, [])
        for field in valid:
            missing = deepcopy(valid)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                WiringSettingChange.model_validate(missing)
            for value in (1, True, []):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    WiringSettingChange.model_validate({**valid, field: value})
        for field in ("device_id", "setting_id"):
            for value in (None, "", " "):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    WiringSettingChange.model_validate({**valid, field: value})
        for field in ("site_value", "target_value"):
            self.assertIsNone(getattr(WiringSettingChange.model_validate({**valid, field: None}), field))
        with self.assertRaises(ValidationError):
            WiringSettingChange.model_validate({**valid, "unknown": True})

    def test_cable_fields_are_optional_trimmed_and_never_on_internal_signals(self):
        resolved = WiringGraph.model_validate(graph_data()).edges[0]
        self.assertEqual((resolved.connector, resolved.cable_id, resolved.note), (None, None, None))
        self.assertEqual(set(resolved.model_dump()), {"id", "source", "target", "signal_type", "label"})
        data = graph_data()
        data["edges"][0].update(connector=" BNC ", cable_id=" C-01 ", note=" 现场备用 ")
        resolved = WiringGraph.model_validate(data).edges[0]
        self.assertEqual((resolved.connector, resolved.cable_id, resolved.note), ("BNC", "C-01", "现场备用"))
        blank = graph_data()
        blank["edges"][0].update(connector="  ", cable_id="", note="\t")
        resolved = WiringGraph.model_validate(blank).edges[0]
        self.assertEqual((resolved.connector, resolved.cable_id, resolved.note), (None, None, None))
        self.assertFalse({"connector", "cable_id", "note"}.intersection(resolved.model_dump()))
        for field in ("connector", "cable_id", "note"):
            for value in (1, True, []):
                candidate = graph_data()
                candidate["edges"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    WiringGraph.model_validate(candidate)
            internal = internal_graph_data()
            internal["edges"][1][field] = "内部记录"
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "内部信号不是线缆"):
                WiringGraph.model_validate(internal)

    def test_cable_differences_keep_one_retained_edge_and_are_listed_by_target_order(self):
        site = graph_data()
        target = deepcopy(site)
        target["edges"][0].update(id="target-edge", connector="SMA", cable_id="C-02", note="目标备注")
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual((result.removed, result.added), ([], []))
        self.assertEqual([item.model_dump() for item in result.retained], target["edges"])
        self.assertEqual([change.model_dump() for change in result.cable_changes], [{
            "edge_id": "target-edge",
            "site_connector": None, "target_connector": "SMA",
            "site_cable_id": None, "target_cable_id": "C-02",
            "site_note": None, "target_note": "目标备注",
        }])
        self.assertEqual((site, target), before)
        relabeled = deepcopy(site)
        relabeled["edges"][0].update(id="renamed-again", label="只改标签")
        self.assertEqual(compare_wiring(site, relabeled).cable_changes, [])
        moved = deepcopy(target)
        moved["edges"][0]["target"]["port_id"] = "aux"
        self.assertEqual(compare_wiring(site, moved).cable_changes, [])
        added = deepcopy(site)
        added["edges"].append({"id": "new-edge", "source": {"device_id": "source", "port_id": "clock_10mhz"},
                               "target": {"device_id": "receiver", "port_id": "aux"},
                               "signal_type": "electrical", "label": "", "cable_id": "C-03"})
        self.assertEqual(compare_wiring(site, added).cable_changes, [])
        valid = result.cable_changes[0].model_dump()
        self.assertIsNone(WiringCableChange.model_validate({**valid, "site_note": None}).site_note)
        for change in ({"edge_id": ""}, {"unknown": True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                WiringCableChange.model_validate({**valid, **change})

    def test_connector_conflict_warns_once_per_edge_only_for_known_port_connectors(self):
        site = graph_data()
        site["nodes"][0]["device"]["ports"][0]["connector"] = " BNC "
        site["nodes"][1]["device"]["ports"][0]["connector"] = "SMA"
        site["edges"][0]["connector"] = "bnc"
        warnings = compare_wiring(site, site).site_warnings
        self.assertEqual([(warning.edge_id, warning.code) for warning in warnings], [("edge-1", "connector_conflict")])
        self.assertIn("终点「接收器 / in」记录为“SMA”", warnings[0].message)
        self.assertNotIn("起点", warnings[0].message)
        site["edges"][0]["connector"] = "SMA"
        warnings = compare_wiring(site, site).site_warnings
        self.assertEqual([warning.code for warning in warnings], ["connector_conflict"])
        self.assertIn("起点「信号源 / out」记录为“BNC”", warnings[0].message)
        self.assertNotIn("终点", warnings[0].message)
        no_cable_record = graph_data()
        no_cable_record["nodes"][0]["device"]["ports"][0]["connector"] = "BNC"
        no_cable_record["nodes"][1]["device"]["ports"][0]["connector"] = "SMA"
        self.assertEqual(compare_wiring(no_cable_record, no_cable_record).site_warnings, [])
        unknown_ports = graph_data()
        unknown_ports["nodes"][0]["device"]["ports"][0]["connector"] = "   "
        unknown_ports["nodes"][1]["device"]["ports"][0]["connector"] = "BNC"
        unknown_ports["edges"][0]["connector"] = "BNC"
        self.assertEqual(compare_wiring(unknown_ports, unknown_ports).site_warnings, [])
        matching = graph_data()
        matching["nodes"][0]["device"]["ports"][0]["connector"] = "bnc"
        matching["nodes"][1]["device"]["ports"][0]["connector"] = "BNC"
        matching["edges"][0]["connector"] = "BNC"
        self.assertEqual(compare_wiring(matching, matching).site_warnings, [])

    def test_dangling_duplicate_and_self_endpoints_are_rejected(self):
        invalid = []
        duplicate_device = graph_data()
        duplicate_device["nodes"].append(deepcopy(duplicate_device["nodes"][0]))
        invalid.append(duplicate_device)
        duplicate_library = graph_data()
        duplicate_library["nodes"][1]["device"]["device_library_id"] = "old-library-id"
        invalid.append(duplicate_library)
        duplicate_port = graph_data()
        duplicate_port["nodes"][0]["device"]["ports"].append(port_data())
        invalid.append(duplicate_port)
        duplicate_mode = graph_data()
        modes = duplicate_mode["nodes"][0]["device"]["ports"][1]["modes"]
        modes.append(deepcopy(modes[0]))
        invalid.append(duplicate_mode)
        for endpoint, key, value in (("source", "device_id", "missing"), ("target", "port_id", "missing")):
            candidate = graph_data()
            candidate["edges"][0][endpoint][key] = value
            invalid.append(candidate)
        same_port = graph_data()
        same_port["edges"][0]["target"] = deepcopy(same_port["edges"][0]["source"])
        invalid.append(same_port)
        for reverse in (False, True):
            for signal_type in ("optical", "electrical"):
                candidate = graph_data()
                duplicate = deepcopy(candidate["edges"][0])
                duplicate.update(id="other-edge", signal_type=signal_type)
                if reverse:
                    duplicate["source"], duplicate["target"] = duplicate["target"], duplicate["source"]
                candidate["edges"].append(duplicate)
                invalid.append(candidate)
        duplicate_edge = graph_data()
        other = deepcopy(duplicate_edge["edges"][0])
        other["target"]["port_id"] = "aux"
        duplicate_edge["edges"].append(other)
        invalid.append(duplicate_edge)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                WiringGraph.model_validate(candidate)

    def test_port_fanout_manual_splitter_and_same_device_loop_are_allowed(self):
        graph = graph_data()
        graph["nodes"].append({"device": {
            "id": "splitter", "name": "人工分路器", "model": "", "device_library_id": None,
            "ports": [port_data("common", "inout"), port_data("branch-1", "inout"), port_data("branch-2", "inout")],
        }, "x": 200, "y": 100})
        graph["edges"].extend([
            {"id": "fanout", "source": {"device_id": "source", "port_id": "out"},
             "target": {"device_id": "splitter", "port_id": "common"}, "signal_type": "electrical", "label": ""},
            {"id": "branch-1", "source": {"device_id": "splitter", "port_id": "branch-1"},
             "target": {"device_id": "receiver", "port_id": "in"}, "signal_type": "electrical", "label": ""},
            {"id": "branch-2", "source": {"device_id": "splitter", "port_id": "branch-2"},
             "target": {"device_id": "receiver", "port_id": "aux"}, "signal_type": "electrical", "label": ""},
            {"id": "loop", "source": {"device_id": "receiver", "port_id": "aux"},
             "target": {"device_id": "receiver", "port_id": "in"}, "signal_type": "electrical", "label": ""},
        ])
        validated = WiringGraph.model_validate(graph)
        self.assertEqual((len(validated.nodes), len(validated.edges)), (3, 5))
        self.assertEqual(compare_wiring(validated, validated).site_warnings, [])

    def test_model_and_shared_inputs_do_not_alias_graph_snapshots(self):
        graph = WiringGraph.model_validate(graph_data())
        body = WiringBody(base_revision="base", site=graph, targets={"experiment-a": graph, "experiment-b": graph})
        body.site.nodes[0].device.name = "只改现场"
        body.targets["experiment-a"].nodes[0].device.ports[1].selected_mode = "external"
        self.assertEqual(graph.nodes[0].device.name, "信号源")
        self.assertEqual(body.targets["experiment-a"].nodes[0].device.name, "信号源")
        self.assertEqual(body.site.nodes[0].device.ports[1].selected_mode, "internal")
        self.assertEqual(body.targets["experiment-b"].nodes[0].device.ports[1].selected_mode, "internal")

    def test_response_schema_version_requires_integer_one(self):
        valid = {"schema_version": 1, "revision": "base", "site": graph_data(), "targets": {},
                 "diagnostics": {"site": [], "targets": {}}}
        self.assertEqual(WiringResponse.model_validate(valid).schema_version, 1)
        for value in (True, 1.0, "1", 2):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WiringResponse.model_validate({**valid, "schema_version": value})
        for field in ("site_mode", "target_mode"):
            mode_change = {"device_id": "d", "device_name": "", "port_id": "p", "port_name": "",
                           "site_mode": None, "target_mode": None}
            del mode_change[field]
            with self.assertRaises(ValidationError):
                WiringModeChange.model_validate(mode_change)


class WiringComparisonTests(unittest.TestCase):
    def test_internal_changes_are_separate_directed_and_not_physical_usage_or_warnings(self):
        site = internal_graph_data()
        site["edges"].append({
            "id": "old-route", "source": {"device_id": "source", "port_id": "control"},
            "target": {"device_id": "source", "port_id": "feedback-input"}, "signal_type": "internal", "label": "旧路由",
        })
        target = deepcopy(site)
        target["nodes"][0].update(x=200, y=400)
        target["nodes"][0]["device"]["name"] = "仅改显示名"
        target["nodes"][0]["internal_ports"][0]["name"] = "内部回读新名"
        target["edges"][0].update(id="physical-renamed", label="物理标签")
        target["edges"][1].update(id="internal-renamed", label="内部标签")
        target["edges"][2].update(id="new-route", target={"device_id": "source", "port_id": "demod-input"})
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([edge.model_dump() for edge in result.retained], target["edges"][:1])
        self.assertEqual((result.removed, result.added), ([], []))
        self.assertEqual([edge.model_dump() for edge in result.internal_retained], target["edges"][1:2])
        self.assertEqual([edge.model_dump() for edge in result.internal_removed], site["edges"][2:])
        self.assertEqual([edge.model_dump() for edge in result.internal_added], target["edges"][2:])
        self.assertEqual((result.mode_changes, result.usage_changes, result.site_warnings, result.target_warnings),
                         ([], [], [], []))
        self.assertEqual((site, target), before)
        site = internal_graph_data()
        site["edges"] = []
        site["nodes"][0]["port_usage"] = {"out": "unused", "clock_10mhz": "unused"}
        target = deepcopy(site)
        target["edges"] = internal_graph_data()["edges"][1:]
        result = compare_wiring(site, target)
        self.assertEqual((result.retained, result.removed, result.added, result.usage_changes), ([], [], [], []))
        self.assertEqual([edge.id for edge in result.internal_added], ["internal-1"])
        site = internal_graph_data()
        target = deepcopy(site)
        edge = target["edges"][1]
        edge["source"], edge["target"] = edge["target"], edge["source"]
        target["nodes"][0]["internal_ports"][0]["direction"] = "in"
        target["nodes"][0]["internal_ports"][1]["direction"] = "out"
        result = compare_wiring(site, target)
        self.assertEqual(result.internal_retained, [])
        self.assertEqual([edge.model_dump() for edge in result.internal_removed], site["edges"][1:])
        self.assertEqual([edge.model_dump() for edge in result.internal_added], target["edges"][1:])
        self.assertEqual(result.mode_changes, [])

    def test_setting_changes_use_stable_ids_raw_values_and_absent_means_none(self):
        site = internal_graph_data()
        site["nodes"][0]["settings"].extend([
            {"id": "removed", "name": "已移除设置", "options": [{"value": "raw-old", "label": "旧显示"}], "value": "raw-old"},
            {"id": "removed-unselected", "name": "移除待确认", "options": [{"value": "x", "label": "X"}]},
        ])
        target = deepcopy(site)
        node = target["nodes"][0]
        node["device"]["name"] = "目标设备"
        node["settings"][0].update(name="Noise Eater 目标", value="low")
        node["settings"][1]["value"] = "4"
        node["settings"] = node["settings"][:2] + [
            {"id": "added", "name": "新增设置", "options": [{"value": "raw-new", "label": "新显示"}], "value": "raw-new"},
            {"id": "added-unselected", "name": "新增待确认", "options": [{"value": "x", "label": "X"}]},
        ]
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([change.model_dump() for change in result.setting_changes], [
            {"device_id": "source", "device_name": "目标设备", "setting_id": "noise-eater", "setting_name": "Noise Eater 目标",
             "site_value": "high", "target_value": "low"},
            {"device_id": "source", "device_name": "目标设备", "setting_id": "channel", "setting_name": "档位",
             "site_value": "1", "target_value": "4"},
            {"device_id": "source", "device_name": "目标设备", "setting_id": "added", "setting_name": "新增设置",
             "site_value": None, "target_value": "raw-new"},
            {"device_id": "source", "device_name": "目标设备", "setting_id": "removed", "setting_name": "已移除设置",
             "site_value": "raw-old", "target_value": None},
        ])
        self.assertEqual((result.mode_changes, result.usage_changes, result.removed, result.added), ([], [], [], []))
        self.assertEqual((site, target), before)
        target = deepcopy(site)
        for setting in target["nodes"][0]["settings"]:
            setting["name"] = "仅改设置名称"
            setting["options"].reverse()
            for option in setting["options"]:
                option["label"] = "仅改选项显示名"
        self.assertEqual(compare_wiring(site, target).setting_changes, [])
        target["nodes"][0]["settings"][0]["value"] = None
        change = compare_wiring(site, target).setting_changes[0]
        self.assertEqual((change.site_value, change.target_value), ("high", None))
        self.assertEqual((compare_wiring(target, site).setting_changes[0].site_value,
                          compare_wiring(target, site).setting_changes[0].target_value), (None, "high"))
        target["nodes"][0]["device"]["id"] = "replacement-source"
        for edge in target["edges"]:
            for endpoint in (edge["source"], edge["target"]):
                if endpoint["device_id"] == "source":
                    endpoint["device_id"] = "replacement-source"
        self.assertEqual(compare_wiring(site, target).setting_changes, [])

    def test_pair_comparison_ignores_edge_ids_labels_layout_arrow_and_names(self):
        site = graph_data()
        target = deepcopy(site)
        edge = target["edges"][0]
        edge.update(id="renamed-edge", label="另一个说明")
        edge["source"], edge["target"] = edge["target"], edge["source"]
        target["nodes"][0].update(x=20000, y=0)
        target["nodes"][0]["device"]["name"] = "重命名源"
        target["nodes"][0]["device"]["ports"][0]["name"] = "重命名端口"
        target["nodes"][0]["device"]["ports"][1]["modes"][0]["name"] = "仅显示名改变"
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([item.model_dump() for item in result.retained], target["edges"])
        self.assertEqual((result.removed, result.added, result.mode_changes), ([], [], []))
        self.assertEqual((site, target), before)
        self.assertEqual({warning.code for warning in result.target_warnings}, {"source_direction_conflict", "target_direction_conflict"})

    def test_changed_type_and_endpoints_produce_added_removed_in_graph_order(self):
        site = graph_data()
        target = deepcopy(site)
        target["edges"][0]["signal_type"] = "optical"
        target["edges"].append({"id": "another", "source": {"device_id": "source", "port_id": "clock_10mhz"},
                                "target": {"device_id": "receiver", "port_id": "aux"}, "signal_type": "electrical", "label": ""})
        result = compare_wiring(site, target)
        self.assertEqual(result.retained, [])
        self.assertEqual([edge.model_dump() for edge in result.removed], site["edges"])
        self.assertEqual([edge.model_dump() for edge in result.added], target["edges"])
        different = deepcopy(site)
        different["edges"][0]["target"]["port_id"] = "aux"
        result = compare_wiring(site, different)
        self.assertEqual((len(result.retained), len(result.removed), len(result.added)), (0, 1, 1))

    def test_mode_and_direction_changes_use_stable_ids_and_target_names(self):
        site = graph_data()
        target = deepcopy(site)
        target["nodes"][0]["device"]["name"] = "目标设备名"
        port = target["nodes"][0]["device"]["ports"][1]
        port.update(name="目标时钟", selected_mode="external")
        target["nodes"][1]["device"]["ports"][0]["direction"] = "out"
        result = compare_wiring(site, target)
        self.assertEqual([change.model_dump() for change in result.mode_changes], [
            {"device_id": "source", "device_name": "目标设备名", "port_id": "clock_10mhz", "port_name": "目标时钟",
             "site_mode": "内部参考时钟（输出）", "target_mode": "外部参考时钟（输入）"},
            {"device_id": "receiver", "device_name": "接收器", "port_id": "in", "port_name": "in",
             "site_mode": "输入", "target_mode": "输出"},
        ])
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = None
        self.assertIsNone(compare_wiring(site, target).mode_changes[0].target_mode)
        site["nodes"][0]["device"]["ports"][1]["selected_mode"] = None
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
        self.assertIsNone(compare_wiring(site, target).mode_changes[0].site_mode)
        site["nodes"][1]["device"]["ports"][0]["direction"] = None
        self.assertIsNone(compare_wiring(site, target).mode_changes[1].site_mode)

    def test_same_name_different_mode_id_and_same_id_changed_direction_are_diffs(self):
        site = graph_data()
        target = deepcopy(site)
        port = target["nodes"][0]["device"]["ports"][1]
        port["modes"][0]["id"] = "different-id"
        port["selected_mode"] = "different-id"
        changes = compare_wiring(site, target).mode_changes
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].site_mode, changes[0].target_mode)
        target = deepcopy(site)
        target["nodes"][0]["device"]["ports"][1]["modes"][0]["direction"] = "in"
        changes = compare_wiring(site, target).mode_changes
        self.assertEqual(len(changes), 1)
        self.assertNotEqual(changes[0].site_mode, changes[0].target_mode)

    def test_inout_working_direction_changes_are_diffs_and_drive_warnings(self):
        site = follow_graph([])
        target = deepcopy(site)
        target["nodes"][0]["port_directions"]["out"] = "in"
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([change.model_dump() for change in result.mode_changes], [{
            "device_id": "source", "device_name": "信号源", "port_id": "out", "port_name": "out",
            "site_mode": "输出", "target_mode": "输入",
        }])
        self.assertEqual(result.site_warnings, [])
        self.assertEqual([warning.code for warning in result.target_warnings], ["source_direction_conflict"])
        self.assertEqual((site, target), before)
        self.assertEqual(target["nodes"][0]["device"]["ports"][0]["direction"], "inout")

    def test_only_common_device_port_ids_participate_in_mode_changes(self):
        site = graph_data()
        target = deepcopy(site)
        target["nodes"][0]["device"]["id"] = "replacement-source"
        target["edges"][0]["source"]["device_id"] = "replacement-source"
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
        target["nodes"][1]["device"]["ports"][0].update(id="replacement-in", direction="out")
        target["edges"][0]["target"]["port_id"] = "replacement-in"
        self.assertEqual(compare_wiring(site, target).mode_changes, [])

    def test_usage_changes_use_common_stable_endpoints_target_names_and_missing_semantics(self):
        site = graph_data()
        site["nodes"][0]["port_usage"] = {"clock_10mhz": "unused"}
        site["nodes"][1]["port_usage"] = {"aux": "used"}
        target = deepcopy(site)
        target["nodes"][0]["device"]["name"] = "目标设备名"
        target["nodes"][0]["device"]["ports"][1]["name"] = "目标时钟"
        target["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "used"}
        target["nodes"][1]["port_usage"] = {"in": "unconfirmed", "aux": "unused"}
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([change.model_dump() for change in result.usage_changes], [
            {"device_id": "source", "device_name": "目标设备名", "port_id": "clock_10mhz", "port_name": "目标时钟",
             "site_usage": "unused", "target_usage": "used"},
            {"device_id": "receiver", "device_name": "接收器", "port_id": "aux", "port_name": "aux",
             "site_usage": "used", "target_usage": "unused"},
        ])
        self.assertEqual(result.mode_changes, [])
        self.assertEqual((site, target), before)
        self.assertEqual((result.removed, result.added), ([], []))
        self.assertEqual([edge.model_dump() for edge in result.retained], target["edges"])
        target["nodes"][0]["device"]["id"] = "replacement-source"
        target["edges"][0]["source"]["device_id"] = "replacement-source"
        target["nodes"][1]["device"]["ports"][1]["id"] = "replacement-aux"
        target["nodes"][1]["port_usage"] = {"in": "unconfirmed", "replacement-aux": "unused"}
        self.assertEqual(compare_wiring(site, target).usage_changes, [])

    def test_usage_changes_use_effective_binary_states_for_both_edge_endpoints(self):
        states = (
            (False, None, "unused"), (False, "unconfirmed", "unused"),
            (False, "unused", "unused"), (False, "used", "used"),
            (True, None, "used"), (True, "unconfirmed", "used"), (True, "used", "used"),
        )
        for site_connected, site_raw, site_usage in states:
            for target_connected, target_raw, target_usage in states:
                for reverse in (False, True):
                    with self.subTest(site=(site_connected, site_raw), target=(target_connected, target_raw), reverse=reverse):
                        site, target = graph_data(), graph_data()
                        for graph, connected, raw in ((site, site_connected, site_raw), (target, target_connected, target_raw)):
                            if not connected:
                                graph["edges"] = []
                            if raw is not None:
                                graph["nodes"][0]["port_usage"] = {"out": raw}
                                graph["nodes"][1]["port_usage"] = {"in": raw}
                        if reverse and target_connected:
                            edge = target["edges"][0]
                            edge["source"], edge["target"] = edge["target"], edge["source"]
                        before = deepcopy((site, target))
                        result = compare_wiring(site, target)
                        expected = [
                            {"device_id": "source", "device_name": "信号源", "port_id": "out", "port_name": "out",
                             "site_usage": site_usage, "target_usage": target_usage},
                            {"device_id": "receiver", "device_name": "接收器", "port_id": "in", "port_name": "in",
                             "site_usage": site_usage, "target_usage": target_usage},
                        ] if site_usage != target_usage else []
                        self.assertEqual([change.model_dump() for change in result.usage_changes], expected)
                        self.assertEqual(result.mode_changes, [])
                        self.assertEqual([edge.model_dump() for edge in result.retained],
                                         target["edges"] if site_connected and target_connected else [])
                        self.assertEqual([edge.model_dump() for edge in result.removed],
                                         site["edges"] if not target_connected else [])
                        self.assertEqual([edge.model_dump() for edge in result.added],
                                         target["edges"] if not site_connected else [])
                        self.assertEqual((site, target), before)

    def test_usage_follows_each_physical_endpoint_when_a_line_moves(self):
        site = graph_data()
        target = deepcopy(site)
        target["edges"][0]["target"]["port_id"] = "aux"
        target["edges"][0]["source"], target["edges"][0]["target"] = target["edges"][0]["target"], target["edges"][0]["source"]
        before = deepcopy((site, target))
        result = compare_wiring(site, target)
        self.assertEqual([change.model_dump() for change in result.usage_changes], [
            {"device_id": "receiver", "device_name": "接收器", "port_id": "in", "port_name": "in",
             "site_usage": "used", "target_usage": "unused"},
            {"device_id": "receiver", "device_name": "接收器", "port_id": "aux", "port_name": "aux",
             "site_usage": "unused", "target_usage": "used"},
        ])
        self.assertEqual(result.mode_changes, [])
        self.assertEqual(result.retained, [])
        self.assertEqual([edge.model_dump() for edge in result.removed], site["edges"])
        self.assertEqual([edge.model_dump() for edge in result.added], target["edges"])
        self.assertEqual((site, target), before)

    def test_usage_and_mode_changes_are_independent_even_on_the_same_port(self):
        site = follow_graph([])
        site["nodes"][0]["port_usage"] = {"clock_10mhz": "unconfirmed"}
        target = deepcopy(site)
        target["nodes"][0]["port_usage"]["clock_10mhz"] = "used"
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
        result = compare_wiring(site, target)
        self.assertEqual([change.port_id for change in result.mode_changes], ["clock_10mhz"])
        self.assertEqual([change.model_dump() for change in result.usage_changes], [{
            "device_id": "source", "device_name": "信号源", "port_id": "clock_10mhz", "port_name": "10 MHz",
            "site_usage": "unused", "target_usage": "used",
        }])
        target["nodes"][0]["port_usage"] = {}
        self.assertEqual(compare_wiring(site, target).usage_changes, [])
        target["nodes"][0]["device"]["ports"][1]["selected_mode"] = "internal"
        target["nodes"][0]["port_directions"]["out"] = "in"
        self.assertEqual([change.port_id for change in compare_wiring(site, target).mode_changes], ["out"])
        self.assertEqual(compare_wiring(site, target).usage_changes, [])

    def test_compare_does_not_revalidate_existing_port_models(self):
        graph = WiringGraph.model_validate(graph_data())
        real_validate = PhysicalPort.model_validate

        def guarded(value, *args, **kwargs):
            if isinstance(value, PhysicalPort):
                raise AssertionError("不应重复校验接线图已有端口模型")
            return real_validate(value, *args, **kwargs)

        with patch.object(PhysicalPort, "model_validate", side_effect=guarded):
            compared = compare_wiring(graph, graph)
        self.assertEqual((compared.mode_changes, compared.site_warnings), ([], []))

    def test_known_unknown_and_unselected_mode_warnings_are_nonblocking(self):
        site = graph_data()
        site["nodes"][0]["device"]["ports"][0].update(direction="in", signal_type="optical")
        site["nodes"][1]["device"]["ports"][0].update(direction="out", signal_type="optical")
        target = graph_data()
        target["edges"][0]["source"]["port_id"] = "clock_10mhz"
        target["nodes"][0]["device"]["ports"][1].update(selected_mode=None, signal_type=None)
        target["nodes"][1]["device"]["ports"][0].update(direction=None, signal_type=None)
        result = compare_wiring(site, target)
        self.assertEqual({warning.code for warning in result.site_warnings}, {
            "source_direction_conflict", "source_signal_type_conflict", "target_direction_conflict", "target_signal_type_conflict",
        })
        self.assertEqual({warning.code for warning in result.target_warnings}, {
            "source_mode_unconfirmed", "source_signal_type_unknown", "target_direction_unknown", "target_signal_type_unknown",
        })
        self.assertTrue(all(warning.edge_id == "edge-1" and warning.message for warning in result.site_warnings + result.target_warnings))
        with TemporaryDirectory() as directory:
            saved = save_wiring(directory, {"base_revision": read_wiring(directory).revision, "site": site, "targets": {"unknown-id": target}})
            self.assertEqual(saved.diagnostics.site, result.site_warnings)
            self.assertEqual(saved.diagnostics.targets["unknown-id"], result.target_warnings)
            self.assertEqual(read_wiring(directory), saved)


class WiringStorageTests(unittest.TestCase):
    def test_missing_read_and_compare_do_not_create_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = read_wiring(root)
            self.assertEqual(initial.schema_version, 1)
            self.assertEqual(initial.revision, hashlib.sha256(b"").hexdigest())
            self.assertEqual(initial.site.model_dump(), {"nodes": [], "edges": []})
            self.assertEqual((initial.targets, initial.diagnostics.model_dump()), ({}, {"site": [], "targets": {}}))
            compare_wiring(graph_data(), graph_data())
            self.assertEqual(list(root.iterdir()), [])

    def test_legacy_without_follow_config_ignores_unreadable_port_archive(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_wiring(root, request_data(read_wiring(root).revision))
            real_read = Path.read_bytes

            def unreadable_profiles(path):
                if path.name == "device_ports.yaml":
                    raise OSError("历史图不应读取端口档案")
                return real_read(path)

            with patch.object(Path, "read_bytes", unreadable_profiles):
                self.assertEqual(read_wiring(root), saved)
                updated = save_wiring(root, WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets))
                self.assertEqual(updated, saved)

    def test_full_snapshot_roundtrip_is_independent_of_profiles_and_other_graphs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = graph_data()
            profile = graph["nodes"][0]["device"]
            archive = save_device_ports(root, DevicePortsBody(base_revision=read_device_ports(root).revision, devices=[profile]))
            body = {"base_revision": read_wiring(root).revision, "site": graph,
                    "targets": {"removed-experiment": deepcopy(graph), "another-experiment": deepcopy(graph)}}
            body["targets"]["removed-experiment"]["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
            saved = save_wiring(root, body)
            archive.devices[0].name = "档案已改名"
            archive.devices[0].ports[1].name = "档案端口已改名"
            archive.devices[0].ports[1].selected_mode = None
            archive.devices[0].ports.pop(0)
            save_device_ports(root, DevicePortsBody(base_revision=archive.revision, devices=archive.devices))
            self.assertEqual(read_wiring(root), saved)
            self.assertEqual(saved.site.nodes[0].device.name, "信号源")
            self.assertEqual(len(saved.site.nodes[0].device.ports), 2)
            self.assertEqual(saved.targets["removed-experiment"].nodes[0].device.ports[1].selected_mode, "external")
            self.assertEqual(saved.targets["another-experiment"].nodes[0].device.ports[1].selected_mode, "internal")
            path = root / "params" / "wiring.yaml"
            self.assertEqual(saved.revision, hashlib.sha256(path.read_bytes()).hexdigest())
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(set(document), {"schema_version", "site", "targets"})
            self.assertEqual(set(document["targets"]), {"removed-experiment", "another-experiment"})
            self.assertEqual(set(path.parent.iterdir()), {path, path.parent / "device_ports.yaml"})
            body["site"]["nodes"][0]["device"]["name"] = "调用方后改"
            self.assertEqual(read_wiring(root), saved)

    def test_cable_fields_roundtrip_and_legacy_edges_keep_yaml_shape(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            body = request_data(read_wiring(root).revision)
            body["site"]["edges"][0].update(connector="BNC", cable_id="C-01", note="现场备用")
            body["targets"]["removed-experiment"]["edges"][0].update(cable_id="C-02")
            saved = save_wiring(root, body)
            self.assertEqual(read_wiring(root), saved)
            self.assertEqual(
                (saved.site.edges[0].connector, saved.site.edges[0].cable_id, saved.site.edges[0].note),
                ("BNC", "C-01", "现场备用"),
            )
            path = root / "params" / "wiring.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(set(document["site"]["edges"][0]), {
                "id", "source", "target", "signal_type", "label", "connector", "cable_id", "note",
            })
            self.assertEqual(document["targets"]["removed-experiment"]["edges"][0]["cable_id"], "C-02")
            self.assertEqual(set(document["targets"]["removed-experiment"]["edges"][0]), {
                "id", "source", "target", "signal_type", "label", "cable_id",
            })
            self.assertEqual(set(document["targets"]), {"removed-experiment"})
            update = WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets)
            update.site.edges[0].connector = None
            update.site.edges[0].cable_id = None
            update.site.edges[0].note = None
            cleared = save_wiring(root, update)
            self.assertEqual((cleared.site.edges[0].connector, cleared.site.edges[0].cable_id,
                              cleared.site.edges[0].note), (None, None, None))
            self.assertNotIn("connector", yaml.safe_load(path.read_text(encoding="utf-8"))["site"]["edges"][0])
            self.assertEqual(cleared.schema_version, 1)

    def test_rename_preserves_ids_and_modes_remain_graph_local_after_resave(self):
        with TemporaryDirectory() as directory:
            saved = save_wiring(directory, request_data(read_wiring(directory).revision))
            saved.site.nodes[0].device.name = "现场重命名"
            saved.site.nodes[0].device.ports[1].name = "现场新端口名"
            saved.site.nodes[0].device.ports[1].selected_mode = "external"
            updated = save_wiring(directory, WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets))
            self.assertNotEqual(saved.revision, updated.revision)
            self.assertEqual((updated.site.nodes[0].device.id, updated.site.nodes[0].device.ports[1].id), ("source", "clock_10mhz"))
            self.assertEqual(updated.targets["removed-experiment"].nodes[0].device.name, "信号源")
            self.assertEqual(updated.targets["removed-experiment"].nodes[0].device.ports[1].selected_mode, "internal")
            updated.site.nodes[0].device.ports[1].selected_mode = None
            self.assertEqual(read_wiring(directory).site.nodes[0].device.ports[1].selected_mode, "external")

    def test_usage_roundtrip_is_graph_local_and_legacy_read_is_zero_write(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            body = request_data(read_wiring(root).revision)
            body["site"]["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "unused"}
            body["site"]["nodes"][1]["port_usage"] = {"aux": "unconfirmed"}
            body["targets"]["removed-experiment"]["nodes"][0]["port_usage"] = {"clock_10mhz": "used"}
            saved = save_wiring(root, body)
            self.assertEqual(read_wiring(root), saved)
            self.assertEqual(saved.site.nodes[0].port_usage, body["site"]["nodes"][0]["port_usage"])
            self.assertEqual(saved.targets["removed-experiment"].nodes[0].port_usage, {"clock_10mhz": "used"})
            path = root / "params" / "wiring.yaml"
            self.assertEqual(saved.revision, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"], 1)
            legacy = {"schema_version": 1, "site": graph_data(), "targets": {}}
            path.write_text(yaml.safe_dump(legacy, allow_unicode=True), encoding="utf-8")
            before = path.read_bytes(), path.stat().st_mtime_ns
            with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("只读不得写文件")):
                loaded = read_wiring(root)
                self.assertEqual(compare_wiring(loaded.site, legacy["site"]).usage_changes, [])
            self.assertTrue(all(node.port_usage == {} for node in loaded.site.nodes))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(loaded.revision, hashlib.sha256(before[0]).hexdigest())
            self.assertTrue(all("port_usage" not in node for node in yaml.safe_load(before[0])["site"]["nodes"]))
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_internal_settings_roundtrip_legacy_raw_revision_and_compare_never_write_back(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            graph = internal_graph_data()
            graph["nodes"][0]["port_usage"] = {"clock_10mhz": "unused"}
            target = deepcopy(graph)
            target["nodes"][0]["settings"][0]["value"] = "low"
            target["nodes"][0]["settings"][1]["value"] = "4"
            target["edges"][1]["target"]["port_id"] = "feedback-input"
            body = {"base_revision": read_wiring(root).revision, "site": graph,
                    "targets": {"removed-experiment": target, "legacy": graph_data()}}
            original = deepcopy(body)
            saved = save_wiring(root, body)
            self.assertEqual(body, original)
            self.assertEqual(saved.site.model_dump(), WiringGraph.model_validate(graph).model_dump())
            self.assertEqual(saved.targets["removed-experiment"].model_dump(), WiringGraph.model_validate(target).model_dump())
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes(), path.stat().st_mtime_ns
            self.assertEqual(saved.revision, hashlib.sha256(before[0]).hexdigest())
            self.assertEqual(yaml.safe_load(before[0]), {"schema_version": 1, "site": saved.site.model_dump(),
                                                       "targets": {key: value.model_dump() for key, value in saved.targets.items()}})
            with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("只读比较不得补写新字段")):
                self.assertEqual(read_wiring(root), saved)
                compared = compare_wiring(saved.site, saved.targets["removed-experiment"])
            self.assertEqual([change.target_value for change in compared.setting_changes], ["low", "4"])
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            legacy = {"schema_version": 1, "site": graph_data(), "targets": {"old-target": graph_data()}}
            path.write_text(yaml.safe_dump(legacy, allow_unicode=True), encoding="utf-8")
            before = path.read_bytes(), path.stat().st_mtime_ns
            with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("旧图 GET 不得补写默认值")):
                loaded = read_wiring(root)
                compared = compare_wiring(loaded.site, loaded.targets["old-target"])
            self.assertEqual(loaded.revision, hashlib.sha256(before[0]).hexdigest())
            for snapshot in (loaded.site, loaded.targets["old-target"]):
                self.assertTrue(all(node.internal_ports == [] and node.settings == [] for node in snapshot.nodes))
            self.assertEqual((compared.internal_retained, compared.internal_removed, compared.internal_added,
                              compared.setting_changes), ([], [], [], []))
            self.assertTrue(all("internal_ports" not in node and "settings" not in node
                                for graph in (legacy["site"], *legacy["targets"].values()) for node in graph["nodes"]))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_connected_unused_save_is_rejected_and_dirty_disk_is_not_repaired(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_wiring(root, request_data(read_wiring(root).revision))
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes()
            for index, port_id in ((0, "out"), (1, "in")):
                invalid = request_data(saved.revision)
                invalid["site"]["nodes"][index]["port_usage"] = {port_id: "unused"}
                with self.subTest(index=index), self.assertRaises(ValidationError):
                    save_wiring(root, invalid)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(read_wiring(root), saved)
            dirty = yaml.safe_load(before)
            dirty["site"]["nodes"][0]["port_usage"] = {"out": "unused"}
            path.write_text(yaml.safe_dump(dirty, allow_unicode=True), encoding="utf-8")
            corrupt = path.read_bytes()
            for action in (lambda: read_wiring(root), lambda: save_wiring(root, request_data(saved.revision))):
                with self.assertRaisesRegex(WiringStorageError, "(?s)损坏或结构非法.*已有连线"):
                    action()
                self.assertEqual(path.read_bytes(), corrupt)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_stale_revision_and_concurrent_updates_have_one_winner(self):
        with TemporaryDirectory() as directory:
            saved = save_wiring(directory, request_data(read_wiring(directory).revision))
            with self.assertRaises(RevisionConflict):
                save_wiring(directory, request_data("stale"))
            gate = threading.Barrier(2)

            def competing(name):
                request = request_data(saved.revision)
                request["site"]["nodes"][0]["device"]["name"] = name
                gate.wait(timeout=5)
                try:
                    return save_wiring(directory, request)
                except RevisionConflict as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(competing, ["并发甲", "并发乙"]))
            self.assertEqual(sum(isinstance(result, RevisionConflict) for result in results), 1)
            winner = next(result for result in results if not isinstance(result, RevisionConflict))
            self.assertEqual(read_wiring(directory), winner)

    def test_atomic_replace_failure_keeps_old_archive_cleans_only_own_temporary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_wiring(root, request_data(read_wiring(root).revision))
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes()
            other = path.parent / ".wiring.yaml.other-writer.tmp"
            other.write_bytes(b"unrelated")
            observed = []

            def fail_replace(source, target):
                source, target = Path(source), Path(target)
                self.assertEqual(source.parent, target.parent)
                self.assertEqual(target, path)
                self.assertNotEqual(source, other)
                self.assertTrue(source.is_file())
                observed.append(source)
                raise OSError("模拟原子替换失败")

            with patch("lab_workflows.wiring.os.replace", side_effect=fail_replace):
                with self.assertRaisesRegex(WiringStorageError, "写入失败"):
                    save_wiring(root, request_data(saved.revision))
            self.assertEqual(len(observed), 1)
            self.assertFalse(observed[0].exists())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(other.read_bytes(), b"unrelated")
            self.assertEqual(set(path.parent.iterdir()), {path, other})
            self.assertEqual(read_wiring(root), saved)

    def test_fsync_failure_and_cleanup_failure_preserve_old_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            saved = save_wiring(root, request_data(read_wiring(root).revision))
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes()
            with patch("lab_workflows.wiring.os.fsync", side_effect=OSError("模拟落盘失败")):
                with self.assertRaisesRegex(WiringStorageError, "模拟落盘失败"):
                    save_wiring(root, request_data(saved.revision))
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(path.read_bytes(), before)
            with patch("lab_workflows.wiring.os.replace", side_effect=OSError("原始替换错误")):
                with patch.object(Path, "unlink", side_effect=OSError("清理错误")):
                    with self.assertRaisesRegex(WiringStorageError, "原始替换错误.*清理错误") as caught:
                        save_wiring(root, request_data(saved.revision))
            self.assertIn("原始替换错误", str(caught.exception.__cause__))
            self.assertEqual(path.read_bytes(), before)

    def test_read_and_directory_write_errors_are_storage_errors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            revision = read_wiring(root).revision
            with patch.object(Path, "read_bytes", side_effect=OSError("模拟无法读取")):
                for action in (lambda: read_wiring(root), lambda: save_wiring(root, request_data(revision))):
                    with self.assertRaisesRegex(WiringStorageError, "读取失败.*模拟无法读取"):
                        action()
            with patch.object(Path, "mkdir", side_effect=OSError("模拟无法建目录")):
                with self.assertRaisesRegex(WiringStorageError, "写入失败.*模拟无法建目录"):
                    save_wiring(root, request_data(revision))
            self.assertEqual(list(root.iterdir()), [])

    def test_corrupt_unknown_versions_and_invalid_disk_graph_cannot_be_overwritten(self):
        malformed = [b"site: [", b"", b"\xff", b"[]\n"]
        for version in (2, True, 1.0, "1"):
            malformed.append(yaml.safe_dump({"schema_version": version, "site": {"nodes": [], "edges": []}, "targets": {}}).encode())
        valid = {"schema_version": 1, "site": graph_data(), "targets": {}}
        malformed.append(yaml.safe_dump({**valid, "unknown": True}).encode())
        duplicate = deepcopy(valid)
        duplicate["site"]["edges"].append(deepcopy(duplicate["site"]["edges"][0]))
        malformed.append(yaml.safe_dump(duplicate).encode())
        dangling = deepcopy(valid)
        dangling["site"]["nodes"] = []
        malformed.append(yaml.safe_dump(dangling).encode())
        with TemporaryDirectory() as directory:
            root = Path(directory)
            revision = read_wiring(root).revision
            path = root / "params" / "wiring.yaml"
            path.parent.mkdir()
            for content in malformed:
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(WiringStorageError):
                        read_wiring(root)
                    with self.assertRaises(WiringStorageError):
                        save_wiring(root, request_data(revision))
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(list(path.parent.iterdir()), [path])


class WiringFollowTests(unittest.TestCase):
    def write_document(self, root: Path, name: str, document: dict):
        (root / "params" / name).write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def test_get_projects_shared_site_and_all_targets_without_writes_or_config_lock(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root)
            payloads["wiring.yaml"]["targets"]["removed-experiment"]["nodes"][0]["device"]["ports"][1]["selected_mode"] = "external"
            self.write_document(root, "wiring.yaml", payloads["wiring.yaml"])
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (root / "params").iterdir()}
            with patch("lab_workflows.instrument_config._process_config_lock", side_effect=AssertionError("GET 不得获取配置写锁")):
                with patch("lab_workflows.wiring.os.replace", side_effect=AssertionError("GET 不得写入")):
                    response = read_wiring(root)
            self.assertEqual(set(before), set((root / "params").iterdir()))
            self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before})
            for key, graph in (("site", response.site), *response.targets.items()):
                original = payloads["wiring.yaml"][key] if key == "site" else payloads["wiring.yaml"]["targets"][key]
                expected = WiringGraph.model_validate(original).model_dump()
                expected["nodes"][0]["device"].update(device_library_id="new-library-id", name="新信号源", model=" dg4000 ")
                self.assertEqual(graph.model_dump(), expected)
                statuses = response.follow_status.site if key == "site" else response.follow_status.targets[key]
                self.assertEqual([status.state for status in statuses], ["updated", "independent"])
                self.assertEqual(statuses[0].mapping_keys, ["drive"])
            self.assertEqual(response.mapping_groups[0].model_dump(), {
                "keys": ["drive"], "label": "驱动", "device_library_id": "new-library-id",
            })
            self.assertNotEqual(response.revision, hashlib.sha256((root / "params" / "wiring.yaml").read_bytes()).hexdigest())

    def test_migration_and_whole_graph_rollback_preserve_usage_and_logical_ids(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = internal_graph_data()
            original["nodes"][0]["follow_mapping"] = ["drive"]
            original["nodes"][0]["device"]["ports"][0]["direction"] = "inout"
            original["nodes"][0]["port_directions"] = {"out": "out"}
            original["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "unused"}
            original["nodes"][1]["port_usage"] = {"in": "unconfirmed", "aux": "used"}
            payloads = write_follow_fixture(root, original)
            target = payloads["wiring.yaml"]["targets"]["removed-experiment"]
            target["nodes"][0]["port_usage"]["clock_10mhz"] = "unconfirmed"
            target["nodes"][0]["settings"][0]["value"] = "low"
            target["nodes"][0]["settings"][1]["value"] = "4"
            self.write_document(root, "wiring.yaml", payloads["wiring.yaml"])
            path = root / "params" / "wiring.yaml"
            before = {candidate: (candidate.read_bytes(), candidate.stat().st_mtime_ns)
                      for candidate in path.parent.iterdir()}
            response = read_wiring(root)
            self.assertEqual(response.follow_status.site[0].state, "updated")
            self.assertEqual(response.site.nodes[0].device.device_library_id, "new-library-id")
            for graph in (response.site, *response.targets.values()):
                self.assertEqual([port.id for port in graph.nodes[0].device.ports], ["out", "clock_10mhz"])
                self.assertEqual([mode.id for mode in graph.nodes[0].device.ports[1].modes], ["internal", "external"])
                self.assertEqual(graph.nodes[0].port_directions, {"out": "out"})
                self.assertEqual([edge.model_dump() for edge in graph.edges], original["edges"])
                self.assertEqual([port.model_dump() for port in graph.nodes[0].internal_ports], original["nodes"][0]["internal_ports"])
            self.assertEqual([setting.model_dump() for setting in response.site.nodes[0].settings], original["nodes"][0]["settings"])
            self.assertEqual([setting.model_dump() for setting in response.targets["removed-experiment"].nodes[0].settings],
                             target["nodes"][0]["settings"])
            self.assertEqual(response.site.nodes[0].port_usage, original["nodes"][0]["port_usage"])
            self.assertEqual(response.site.nodes[1].port_usage, original["nodes"][1]["port_usage"])
            self.assertEqual(response.targets["removed-experiment"].nodes[0].port_usage,
                             {"out": "used", "clock_10mhz": "unconfirmed"})
            self.assertEqual(before, {candidate: (candidate.read_bytes(), candidate.stat().st_mtime_ns) for candidate in before})
            saved = save_wiring(root, WiringBody(base_revision=response.revision, site=response.site, targets=response.targets))
            self.assertEqual(read_wiring(root).site, saved.site)
            self.assertEqual(saved.site.nodes[0].port_usage, original["nodes"][0]["port_usage"])
            self.assertEqual([setting.model_dump() for setting in saved.site.nodes[0].settings], original["nodes"][0]["settings"])
            self.assertEqual([port.model_dump() for port in saved.site.nodes[0].internal_ports], original["nodes"][0]["internal_ports"])
            payloads["wiring.yaml"]["site"]["nodes"][1]["follow_mapping"] = ["gone"]
            self.write_document(root, "wiring.yaml", payloads["wiring.yaml"])
            unchanged = path.read_bytes()
            blocked = read_wiring(root)
            expected = WiringGraph.model_validate(payloads["wiring.yaml"]["site"])
            self.assertEqual(blocked.site, expected)
            self.assertEqual([status.state for status in blocked.follow_status.site], ["blocked", "blocked"])
            self.assertEqual(blocked.targets["removed-experiment"].nodes[0].device.device_library_id, "new-library-id")
            self.assertEqual(blocked.targets["removed-experiment"].nodes[0].port_usage,
                             {"out": "used", "clock_10mhz": "unconfirmed"})
            self.assertEqual(path.read_bytes(), unchanged)
            preserved = save_wiring(root, WiringBody(base_revision=blocked.revision, site=blocked.site, targets=blocked.targets))
            self.assertEqual(preserved.site, expected)
            self.assertEqual(read_wiring(root).site, expected)

    def test_auto_unique_advice_stays_none_multiple_groups_pending_and_old_replacement_not_inferred(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root, follow_graph(), destination="old-library-id")
            response = read_wiring(root)
            self.assertEqual(response.follow_status.site[0].state, "bound")
            self.assertEqual(response.follow_status.site[0].mapping_keys, ["drive"])
            self.assertIsNone(response.site.nodes[0].follow_mapping)
            saved = save_wiring(root, WiringBody(base_revision=response.revision, site=response.site, targets=response.targets))
            self.assertIsNone(saved.site.nodes[0].follow_mapping)
            self.assertIsNone(yaml.safe_load((root / "params" / "wiring.yaml").read_text(encoding="utf-8"))["site"]["nodes"][0]["follow_mapping"])
            mapping = payloads["mapping.yaml"]
            mapping["mapping"]["monitor"] = {"device_id": "old-library-id"}
            self.write_document(root, "mapping.yaml", mapping)
            pending = read_wiring(root)
            self.assertEqual(len(pending.mapping_groups), 2, "同设备不能把无约束用途并组")
            self.assertEqual(pending.follow_status.site[0].state, "pending")
            self.assertIn("未同步", pending.follow_status.site[0].message)
            for config in mapping["mapping"].values():
                config["device_id"] = "new-library-id"
            self.write_document(root, "mapping.yaml", mapping)
            legacy = read_wiring(root)
            self.assertEqual(legacy.site, pending.site)
            self.assertEqual(legacy.follow_status.site[0].state, "pending")
            self.assertIn("不可反推", legacy.follow_status.site[0].message)

    def test_constraint_overlap_is_transitive_but_explicit_keys_remain_authoritative(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root)
            mapping = payloads["mapping.yaml"]
            mapping["mapping"].update({key: {"device_id": "new-library-id"} for key in ("read", "clock", "unrelated")})
            mapping["constraints"] = {"shared_channel_groups": {"shared": ["drive", "read"]},
                                      "colocation_groups": {"colocated": ["read", "clock"]}}
            self.write_document(root, "mapping.yaml", mapping)
            response = read_wiring(root)
            self.assertEqual([set(group.keys) for group in response.mapping_groups], [{"drive", "read", "clock"}, {"unrelated"}])
            self.assertEqual(response.site.nodes[0].follow_mapping, ["drive"])
            self.assertEqual(response.follow_status.site[0].mapping_keys, ["drive"])
            self.assertEqual(response.follow_status.site[0].state, "updated")
            mapping["mapping"]["clock"]["device_id"] = "other-library-id"
            self.write_document(root, "mapping.yaml", mapping)
            blocked = read_wiring(root)
            self.assertEqual(blocked.follow_status.site[0].state, "blocked")
            self.assertEqual(blocked.site.nodes[0].follow_mapping, ["drive"])
            self.assertEqual(blocked.site.nodes[0].device.device_library_id, "old-library-id")
            self.assertIsNone(blocked.mapping_groups[0].device_library_id)

    def test_independent_node_never_follows_and_explicit_missing_keys_are_not_cleared(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root, follow_graph([]))
            independent = read_wiring(root)
            self.assertEqual(independent.follow_status.site[0].state, "independent")
            self.assertEqual(independent.site.nodes[0].device.device_library_id, "old-library-id")
            payloads["wiring.yaml"]["site"]["nodes"][0]["follow_mapping"] = ["deleted-purpose"]
            self.write_document(root, "wiring.yaml", payloads["wiring.yaml"])
            blocked = read_wiring(root)
            self.assertEqual(blocked.follow_status.site[0].state, "blocked")
            self.assertEqual(blocked.follow_status.site[0].mapping_keys, ["deleted-purpose"])
            self.assertEqual(blocked.site.nodes[0].follow_mapping, ["deleted-purpose"])
            self.assertIn("不存在", blocked.follow_status.site[0].message)
            self.assertEqual(blocked.follow_status.targets["removed-experiment"][0].state, "independent")

    def test_model_ports_modes_and_collision_failures_preserve_each_entire_graph(self):
        def model_changed(payloads):
            payloads["devices.yaml"]["devices"]["new-library-id"]["model"] = "DG900"

        def missing_port(payloads):
            payloads["device_ports.yaml"]["devices"][0]["ports"].pop()

        def extra_port(payloads):
            payloads["device_ports.yaml"]["devices"][0]["ports"].append(port_data("extra"))

        def ambiguous_port(payloads):
            profile = payloads["device_ports.yaml"]["devices"][0]
            duplicate = deepcopy(profile["ports"][0])
            duplicate["id"] = "other-id-same-name"
            profile["ports"].append(duplicate)

        def missing_mode(payloads):
            payloads["device_ports.yaml"]["devices"][0]["ports"][1]["modes"].pop()

        def ambiguous_mode(payloads):
            modes = payloads["device_ports.yaml"]["devices"][0]["ports"][1]["modes"]
            modes.append({**modes[0], "id": "duplicate-name-mode"})

        def library_collision(payloads):
            node = payloads["wiring.yaml"]["site"]["nodes"][1]
            node["device"]["device_library_id"] = "new-library-id"
            node["follow_mapping"] = []

        for mutation in (model_changed, missing_port, extra_port, ambiguous_port, missing_mode, ambiguous_mode, library_collision):
            with self.subTest(mutation=mutation.__name__), TemporaryDirectory() as directory:
                root = Path(directory)
                graph = follow_graph(["drive"])
                graph["nodes"][0]["port_usage"] = {"out": "used", "clock_10mhz": "unused"}
                graph["nodes"][1]["port_usage"] = {"in": "unconfirmed", "aux": "used"}
                payloads = write_follow_fixture(root, graph)
                mutation(payloads)
                for name, document in payloads.items():
                    self.write_document(root, name, document)
                before = (root / "params" / "wiring.yaml").read_bytes()
                response = read_wiring(root)
                self.assertEqual(response.site, WiringGraph.model_validate(payloads["wiring.yaml"]["site"]))
                self.assertEqual(response.site.nodes[0].port_usage, graph["nodes"][0]["port_usage"])
                self.assertEqual(response.site.nodes[1].port_usage, graph["nodes"][1]["port_usage"])
                self.assertEqual(response.follow_status.site[0].state, "blocked")
                self.assertTrue(all(status.state != "updated" and "未同步" in status.message for status in response.follow_status.site))
                if mutation is library_collision:
                    self.assertEqual(response.targets["removed-experiment"].nodes[0].device.device_library_id, "new-library-id")
                    self.assertEqual(response.follow_status.targets["removed-experiment"][0].state, "updated")
                self.assertEqual((root / "params" / "wiring.yaml").read_bytes(), before)

    def test_one_unmigratable_node_cancels_other_success_and_targets_remain_independent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root)
            graph = payloads["wiring.yaml"]["site"]
            graph["nodes"][1]["follow_mapping"] = ["gone"]
            self.write_document(root, "wiring.yaml", payloads["wiring.yaml"])
            response = read_wiring(root)
            self.assertEqual(response.site, WiringGraph.model_validate(graph))
            self.assertEqual([status.state for status in response.follow_status.site], ["blocked", "blocked"])
            self.assertTrue(all("未同步" in status.message for status in response.follow_status.site))
            self.assertEqual(response.targets["another-experiment"].nodes[0].device.device_library_id, "new-library-id")

    def test_combined_revision_covers_all_dependency_bytes_missing_and_appearance(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root)
            baseline = read_wiring(root).revision
            raw = (root / "params" / "wiring.yaml").read_bytes()
            with self.assertRaises(RevisionConflict):
                save_wiring(root, request_data(hashlib.sha256(raw).hexdigest()))
            for name in ("mapping.yaml", "devices.yaml", "device_ports.yaml"):
                path = root / "params" / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# revision-byte-change\n")
                changed = read_wiring(root)
                self.assertNotEqual(changed.revision, baseline)
                with self.assertRaises(RevisionConflict):
                    save_wiring(root, WiringBody(base_revision=baseline, site=changed.site, targets=changed.targets))
                path.write_bytes(original)
                self.assertEqual(read_wiring(root).revision, baseline)
                path.unlink()
                missing = read_wiring(root)
                self.assertNotEqual(missing.revision, baseline)
                self.assertEqual(missing.follow_status.site[0].state, "blocked")
                path.write_bytes(original)
                self.assertEqual(read_wiring(root).revision, baseline)
            self.assertEqual((root / "params" / "wiring.yaml").read_bytes(), raw)
            self.assertEqual(set((root / "params").iterdir()), {root / "params" / name for name in payloads})

    def test_bad_configs_are_explicitly_blocked_not_empty_or_old_raw_revision(self):
        cases = {
            "mapping.yaml": [b"[", b"", b"mapping: []\n", b"mapping: {}\nconstraints: []\n", b"schema_version: true\nmapping: {}\n", b"schema_version: null\nmapping: {}\n"],
            "devices.yaml": [b"[", b"", b"devices: []\n", b"{}\n"],
            "device_ports.yaml": [b"[", b"schema_version: 99\ndevices: []\n"],
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_follow_fixture(root)
            wiring = root / "params" / "wiring.yaml"
            raw = wiring.read_bytes()
            raw_revision = hashlib.sha256(raw).hexdigest()
            for name, variants in cases.items():
                path = root / "params" / name
                original = path.read_bytes()
                for content in variants:
                    with self.subTest(name=name, content=content):
                        path.write_bytes(content)
                        response = read_wiring(root)
                        self.assertEqual(response.follow_status.site[0].state, "blocked")
                        self.assertIn("不可用", response.follow_status.site[0].message)
                        self.assertNotEqual(response.revision, raw_revision)
                        with self.assertRaises(RevisionConflict):
                            save_wiring(root, request_data(raw_revision))
                        self.assertEqual(wiring.read_bytes(), raw)
                path.write_bytes(original)

    def test_bad_config_is_explicit_error_for_empty_or_all_independent_graphs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = write_follow_fixture(root)
            for graph in ({"nodes": [], "edges": []}, follow_graph([])):
                graph = deepcopy(graph)
                for node in graph["nodes"]:
                    node["follow_mapping"] = []
                self.write_document(root, "wiring.yaml", {"schema_version": 1, "site": graph, "targets": {}})
                valid_revision = read_wiring(root).revision
                (root / "params" / "mapping.yaml").write_bytes(b"mapping: []\n")
                with self.assertRaisesRegex(WiringStorageError, "用途配置不可用"):
                    read_wiring(root)
                with self.assertRaises(RevisionConflict):
                    save_wiring(root, {"base_revision": valid_revision, "site": graph, "targets": {}})
                self.write_document(root, "mapping.yaml", payloads["mapping.yaml"])

    def test_put_uses_same_projection_snapshot_and_rechecks_before_atomic_replace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_follow_fixture(root)
            response = read_wiring(root)
            request = {"base_revision": response.revision, "site": follow_graph(["drive"]), "targets": {}}
            saved = save_wiring(root, request)
            self.assertEqual(saved.follow_status.site[0].state, "updated")
            self.assertEqual(saved.site.nodes[0].device.device_library_id, "new-library-id")
            self.assertEqual(read_wiring(root).site, saved.site)
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes()
            mapping_path = root / "params" / "mapping.yaml"
            original_mapping = mapping_path.read_bytes()

            def change_configuration(_descriptor):
                mapping_path.write_bytes(original_mapping + b"\n# competing configuration save\n")

            with patch("lab_workflows.wiring.os.fsync", side_effect=change_configuration):
                with self.assertRaises(RevisionConflict):
                    save_wiring(root, WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(path.parent.glob(".wiring.yaml.*.tmp")))
            self.assertNotEqual(read_wiring(root).revision, saved.revision)

    def test_dependency_read_error_after_temp_creation_cleans_only_own_temp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_follow_fixture(root)
            response = read_wiring(root)
            path = root / "params" / "wiring.yaml"
            before = path.read_bytes()
            real_read = Path.read_bytes
            after_sync = False

            def fail_dependency_read(candidate):
                if after_sync and candidate.name == "mapping.yaml":
                    raise OSError("模拟保存重核时依赖不可读")
                return real_read(candidate)

            def synced(_descriptor):
                nonlocal after_sync
                after_sync = True

            with patch.object(Path, "read_bytes", fail_dependency_read), patch("lab_workflows.wiring.os.fsync", side_effect=synced):
                with self.assertRaisesRegex(WiringStorageError, "依赖不可读"):
                    save_wiring(root, WiringBody(base_revision=response.revision, site=response.site, targets=response.targets))
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(path.parent.glob(".wiring.yaml.*.tmp")))


if __name__ == "__main__":
    unittest.main()
