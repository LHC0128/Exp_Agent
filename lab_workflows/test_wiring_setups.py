"""装置现场图隔离、共享目标与 v1 字节兼容的无硬件回归。"""

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
import yaml

from lab_workflows import wiring
from lab_workflows.setups import (
    SETUP_ENV_VAR, SetupConfigError, bound_setup, create_setup, load_setup_registry, set_active_setup,
)
from lab_workflows.test_wiring import internal_graph_data, write_follow_fixture
from lab_workflows.wiring import (
    RevisionConflict, WiringBody, WiringGraph, WiringStorageError, compare_wiring, read_wiring,
    read_wiring_revision, read_wiring_snapshot, save_wiring,
)


EMPTY_GRAPH = {"nodes": [], "edges": []}


@pytest.fixture(autouse=True)
def unbound_environment(monkeypatch):
    monkeypatch.delenv(SETUP_ENV_VAR, raising=False)


def write_yaml(root: Path, name: str, payload: dict) -> Path:
    path = root / "params" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).encode("utf-8"))
    return path


def stored_registry(root: Path, active="default") -> Path:
    return write_yaml(root, "setups.yaml", {
        "schema_version": 1, "active": active,
        "setups": {
            "default": {"label": "默认装置", "mapping_file": "params/mapping.yaml"},
            "mag_b": {"label": "磁力仪 B", "mapping_file": "params/setups/mag_b/mapping.yaml"},
        },
    })


def independent_graph(name="现场") -> dict:
    graph = internal_graph_data()
    graph["nodes"][0]["device"]["name"] = name
    for node in graph["nodes"]:
        node["follow_mapping"] = []
    return graph


def test_stored_registry_reads_v1_as_default_and_other_setup_as_empty(tmp_path):
    stored_registry(tmp_path)
    site = independent_graph()
    targets = {"shared": independent_graph("实验目标")}
    path = write_yaml(tmp_path, "wiring.yaml", {"schema_version": 1, "site": site, "targets": targets})
    before = path.read_bytes(), path.stat().st_mtime_ns
    with bound_setup("default"):
        default = read_wiring(tmp_path)
        assert default.site == WiringGraph.model_validate(site)
        assert read_wiring_snapshot(tmp_path).site == default.site
    with bound_setup("mag_b"):
        second = read_wiring(tmp_path)
        assert second.site.model_dump() == EMPTY_GRAPH
        assert read_wiring_snapshot(tmp_path).site.model_dump() == EMPTY_GRAPH
        assert second.targets == default.targets
        assert read_wiring_revision(tmp_path).revision == second.revision
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not path.with_name("wiring.yaml.v1.bak").exists()


@pytest.mark.parametrize("configured", [False, True])
def test_without_registry_keeps_legacy_bytes_raw_and_combined_revisions(tmp_path, configured):
    # 摘要在本轮修改前由原 v1 实现、同一图与固定依赖字节采集，不从待测代码生成期望。
    legacy_raw_revision = "35dd829df198fe6d6f31bfeb5780632eec76e2b7efcbeb8f5af3586db9307c76"
    legacy_combined_revision = "0eab748c4bb9ee509374719f621a346fa9b3cc04a0e6cd8d1be2884cd9ccb495"
    if configured:
        params = tmp_path / "params"
        params.mkdir()
        for name, raw in {
            "mapping.yaml": b"schema_version: 2\nmapping: {}\nconstraints: {}\n",
            "devices.yaml": b"schema_version: 1\ndevices: {}\n",
            "device_ports.yaml": b"schema_version: 1\ndevices: []\n",
        }.items():
            (params / name).write_bytes(raw)
    else:
        assert read_wiring(tmp_path).revision == hashlib.sha256(b"").hexdigest()
        assert read_wiring_snapshot(tmp_path).site.model_dump() == EMPTY_GRAPH
        assert not (tmp_path / "params").exists()
    graph = internal_graph_data()
    original = deepcopy(graph)
    saved = save_wiring(tmp_path, {
        "base_revision": read_wiring(tmp_path).revision, "site": graph, "targets": {"shared": graph},
    })
    raw = (tmp_path / "params" / "wiring.yaml").read_bytes()
    assert len(raw) == 5351
    assert hashlib.sha256(raw).hexdigest() == legacy_raw_revision
    assert yaml.safe_load(raw) == {
        "schema_version": 1, "site": saved.site.model_dump(mode="json"),
        "targets": {"shared": saved.targets["shared"].model_dump(mode="json")},
    }
    assert saved.revision == (legacy_combined_revision if configured else legacy_raw_revision)
    assert read_wiring(tmp_path) == saved
    snapshot = read_wiring_snapshot(tmp_path)
    assert snapshot.model_dump() == {
        "schema_version": 1, "revision": saved.revision,
        "site": saved.site.model_dump(), "targets": {"shared": saved.targets["shared"].model_dump()},
    }
    assert read_wiring_revision(tmp_path).revision == saved.revision
    save_wiring(tmp_path, WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets))
    assert (tmp_path / "params" / "wiring.yaml").read_bytes() == raw
    assert graph == original
    assert not (tmp_path / "params" / "setups.yaml").exists()
    assert not (tmp_path / "params" / "wiring.yaml.v1.bak").exists()


def test_without_registry_empty_save_keeps_exact_legacy_yaml(tmp_path):
    saved = save_wiring(tmp_path, {
        "base_revision": read_wiring(tmp_path).revision, "site": EMPTY_GRAPH, "targets": {},
    })
    expected = b"site:\n  nodes: []\n  edges: []\ntargets: {}\nschema_version: 1\n"
    assert (tmp_path / "params" / "wiring.yaml").read_bytes() == expected
    assert saved.revision == hashlib.sha256(expected).hexdigest()


def test_create_copies_site_active_switch_isolated_save_and_shared_targets(tmp_path):
    write_yaml(tmp_path, "mapping.yaml", {"schema_version": 2, "mapping": {}, "constraints": {}})
    write_yaml(tmp_path, "devices.yaml", {"schema_version": 1, "devices": {}})
    site = independent_graph("默认现场")
    targets = {"shared": independent_graph("共享目标")}
    path = write_yaml(tmp_path, "wiring.yaml", {"schema_version": 1, "site": site, "targets": targets})
    original = path.read_bytes()
    original_payload = yaml.safe_load(original)
    registry = create_setup("磁力仪 B", copy_from="default", setup_id="mag_b",
                            base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
    copied = yaml.safe_load(path.read_bytes())
    assert copied == {"schema_version": 2, "sites": {"default": site, "mag_b": site}, "targets": targets}
    assert copied["sites"]["default"] == original_payload["site"]
    assert path.with_name("wiring.yaml.v1.bak").read_bytes() == original
    assert registry.active == "default"
    assert (tmp_path / "params" / "setups" / "mag_b" / "mapping.yaml").read_bytes() == (
        tmp_path / "params" / "mapping.yaml").read_bytes()
    first = read_wiring(tmp_path)
    registry = set_active_setup("mag_b", base_revision=registry.revision, root=tmp_path)
    second = read_wiring(tmp_path)
    assert second.site == first.site
    assert second.targets == first.targets
    second.site.nodes[0].device.name = "B 的独立现场"
    second.site.edges[0].signal_type = "optical"
    second.targets["shared"].nodes[0].device.name = "B 修改共享目标"
    saved = save_wiring(tmp_path, WiringBody(
        base_revision=second.revision, site=second.site, targets=second.targets,
    ))
    after = yaml.safe_load(path.read_bytes())
    assert after["sites"]["default"] == copied["sites"]["default"]
    assert after["sites"]["mag_b"] == saved.site.model_dump(mode="json")
    assert after["schema_version"] == copied["schema_version"]
    assert "site" not in after
    assert read_wiring(tmp_path).site == saved.site
    assert read_wiring_snapshot(tmp_path).site == saved.site
    assert read_wiring_revision(tmp_path).revision == saved.revision
    assert {warning.code for warning in saved.diagnostics.site} == {
        "source_signal_type_conflict", "target_signal_type_conflict",
    }
    assert len(compare_wiring(saved.site, saved.targets["shared"]).removed) == 1
    set_active_setup("default", base_revision=registry.revision, root=tmp_path)
    default = read_wiring(tmp_path)
    assert default.site == first.site
    assert default.targets == saved.targets
    default.targets["shared"].nodes[0].device.name = "默认装置修改共享目标"
    updated = save_wiring(tmp_path, WiringBody(
        base_revision=default.revision, site=default.site, targets=default.targets,
    ))
    with bound_setup("mag_b"):
        assert read_wiring(tmp_path).targets == updated.targets
        assert read_wiring(tmp_path).site == saved.site
    assert yaml.safe_load(path.read_bytes())["sites"]["mag_b"] == after["sites"]["mag_b"]
    assert path.with_name("wiring.yaml.v1.bak").read_bytes() == original


@pytest.mark.parametrize("existing_backup", [False, True])
def test_first_stored_save_backs_up_v1_without_overwriting_existing_backup(tmp_path, existing_backup):
    stored_registry(tmp_path)
    site = independent_graph()
    path = write_yaml(tmp_path, "wiring.yaml", {"schema_version": 1, "site": site, "targets": {}})
    # 自定义注释和 CRLF 证明备份保存原始字节而不是重新 dump。
    original = b"# original v1\r\n" + path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(original)
    backup = path.with_name("wiring.yaml.v1.bak")
    if existing_backup:
        backup.write_bytes(b"keep-existing-backup\x00\xff")
    expected_backup = backup.read_bytes() if existing_backup else original
    current = read_wiring(tmp_path)
    assert not backup.exists() if not existing_backup else backup.read_bytes() == expected_backup
    current.site.nodes[0].device.name = "保存后的现场"
    saved = save_wiring(tmp_path, WiringBody(
        base_revision=current.revision, site=current.site, targets=current.targets,
    ))
    payload = yaml.safe_load(path.read_bytes())
    assert set(payload) == {"schema_version", "sites", "targets"}
    assert payload["schema_version"] == 2
    assert payload["sites"] == {"default": saved.site.model_dump(mode="json")}
    assert backup.read_bytes() == expected_backup
    save_wiring(tmp_path, WiringBody(base_revision=saved.revision, site=saved.site, targets=saved.targets))
    assert backup.read_bytes() == expected_backup


def test_bound_snapshot_keeps_bound_site_even_when_active_changes(tmp_path, monkeypatch):
    stored_registry(tmp_path, active="mag_b")
    default, second = independent_graph("默认现场"), independent_graph("B 现场")
    targets = {"shared": independent_graph("共享目标")}
    path = write_yaml(tmp_path, "wiring.yaml", {
        "schema_version": 2, "sites": {"default": default, "mag_b": second}, "targets": targets,
    })
    before = path.read_bytes(), path.stat().st_mtime_ns
    monkeypatch.setenv(SETUP_ENV_VAR, "mag_b")
    assert read_wiring_snapshot(tmp_path).site == WiringGraph.model_validate(second)
    with bound_setup("default"):
        snapshot = read_wiring_snapshot(tmp_path)
        assert snapshot.site == WiringGraph.model_validate(default)
        assert snapshot.targets == {"shared": WiringGraph.model_validate(targets["shared"])}
        assert snapshot.revision == read_wiring_revision(tmp_path).revision
        assert set(snapshot.model_dump()) == {"schema_version", "revision", "site", "targets"}
    assert read_wiring_snapshot(tmp_path).site == WiringGraph.model_validate(second)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("setup_id", ["", "bad id", "../escape", "_leading", "-leading", "装置", "a" * 65, 3, None, " default "])
def test_v2_invalid_setup_keys_raise_storage_error_and_are_not_overwritten(tmp_path, setup_id):
    stored_registry(tmp_path)
    path = write_yaml(tmp_path, "wiring.yaml", {
        "schema_version": 2, "sites": {"default": EMPTY_GRAPH, setup_id: EMPTY_GRAPH}, "targets": {},
    })
    before = path.read_bytes()
    for read in (read_wiring, read_wiring_revision, read_wiring_snapshot):
        with pytest.raises(WiringStorageError):
            read(tmp_path)
    with pytest.raises(WiringStorageError):
        save_wiring(tmp_path, {"base_revision": "stale", "site": EMPTY_GRAPH, "targets": {}})
    assert path.read_bytes() == before
    assert not path.with_name("wiring.yaml.v1.bak").exists()


def test_unregistered_and_inactive_sites_preserve_raw_fields_and_whole_file_revision(tmp_path):
    stored_registry(tmp_path, active="mag_b")
    archived_id = "A" + "b" * 63
    default = independent_graph("非生效现场")
    archived = independent_graph("外部归档现场")
    # 缺省字段与非规范化文本读时可用，保存其他装置时必须按原数据保留。
    archived["edges"][0]["connector"] = "  BNC  "
    targets = {"shared": independent_graph("共享目标")}
    path = write_yaml(tmp_path, "wiring.yaml", {
        "schema_version": 2, "sites": {"default": default, "mag_b": independent_graph("B"), archived_id: archived},
        "targets": targets,
    })
    with bound_setup("default"):
        stale_default = read_wiring(tmp_path)
    current = read_wiring(tmp_path)
    current.site.nodes[0].device.name = "B 已修改"
    save_wiring(tmp_path, WiringBody(base_revision=current.revision, site=current.site, targets=current.targets))
    after = yaml.safe_load(path.read_bytes())
    assert after["sites"]["default"] == default
    assert after["sites"][archived_id] == archived
    assert archived_id not in load_setup_registry(tmp_path).setups
    before_conflict = path.read_bytes()
    with bound_setup("default"), pytest.raises(RevisionConflict):
        save_wiring(tmp_path, WiringBody(
            base_revision=stale_default.revision, site=stale_default.site, targets=stale_default.targets,
        ))
    assert path.read_bytes() == before_conflict
    assert not path.with_name("wiring.yaml.v1.bak").exists()


@pytest.mark.parametrize("missing_archive", [False, True])
def test_create_from_setup_without_site_leaves_new_site_empty_and_archive_unchanged(tmp_path, missing_archive):
    stored_registry(tmp_path)
    write_yaml(tmp_path, "setups/mag_b/mapping.yaml", {"schema_version": 2, "mapping": {}, "constraints": {}})
    path = tmp_path / "params" / "wiring.yaml"
    if not missing_archive:
        write_yaml(tmp_path, "wiring.yaml", {
            "schema_version": 2, "sites": {"default": independent_graph()}, "targets": {},
        })
    before = path.read_bytes() if path.exists() else None
    create_setup("磁力仪 C", copy_from="mag_b", setup_id="mag_c",
                 base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
    assert (path.read_bytes() if path.exists() else None) == before
    assert not path.with_name("wiring.yaml.v1.bak").exists()
    with bound_setup("mag_c"):
        assert read_wiring_snapshot(tmp_path).site.model_dump() == EMPTY_GRAPH


def test_create_copies_explicit_source_not_active_or_bound_setup(tmp_path):
    stored_registry(tmp_path)
    write_yaml(tmp_path, "setups/mag_b/mapping.yaml", {"schema_version": 2, "mapping": {}, "constraints": {}})
    sites = {"default": independent_graph("默认"), "mag_b": independent_graph("来源 B")}
    path = write_yaml(tmp_path, "wiring.yaml", {
        "schema_version": 2, "sites": sites, "targets": {"shared": independent_graph("共享目标")},
    })
    before = yaml.safe_load(path.read_bytes())
    with bound_setup("default"):
        create_setup("磁力仪 C", copy_from="mag_b", setup_id="mag_c",
                     base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
    after = yaml.safe_load(path.read_bytes())
    assert after["sites"] == {**before["sites"], "mag_c": before["sites"]["mag_b"]}
    assert after["targets"] == before["targets"]
    with bound_setup("mag_c"):
        assert read_wiring_snapshot(tmp_path).site == WiringGraph.model_validate(sites["mag_b"])


def test_create_site_copy_failure_reports_partial_success_and_keeps_registry_and_mapping(tmp_path, monkeypatch):
    mapping = write_yaml(tmp_path, "mapping.yaml", {"schema_version": 2, "mapping": {}, "constraints": {}})
    original = mapping.read_bytes()
    failure = OSError("模拟现场图复制失败")

    def fail_copy(root, source_id, new_id):
        raise failure

    monkeypatch.setattr(wiring, "copy_site_for_setup", fail_copy)
    with pytest.raises(SetupConfigError, match="装置已建立但现场图未复制") as caught:
        create_setup("磁力仪 B", copy_from="default", setup_id="mag_b",
                     base_revision=load_setup_registry(tmp_path).revision, root=tmp_path)
    assert caught.value.__cause__ is failure
    registry = load_setup_registry(tmp_path)
    assert registry.stored
    assert registry.active == "default"
    assert registry.setups["mag_b"].mapping_file == "params/setups/mag_b/mapping.yaml"
    assert (tmp_path / "params" / "setups" / "mag_b" / "mapping.yaml").read_bytes() == original
    assert mapping.read_bytes() == original


def test_follow_projection_uses_effective_site_and_shared_targets_without_touching_other_sites(tmp_path):
    payloads = write_follow_fixture(tmp_path)
    stored_registry(tmp_path)
    default = deepcopy(payloads["wiring.yaml"]["site"])
    default["nodes"][0]["follow_mapping"] = ["missing-only-in-default"]
    second = deepcopy(payloads["wiring.yaml"]["site"])
    path = write_yaml(tmp_path, "wiring.yaml", {
        "schema_version": 2, "sites": {"default": default, "mag_b": second},
        "targets": payloads["wiring.yaml"]["targets"],
    })
    write_yaml(tmp_path, "setups/mag_b/mapping.yaml", payloads["mapping.yaml"])
    before = path.read_bytes()
    with bound_setup("mag_b"):
        snapshot = read_wiring_snapshot(tmp_path)
        response = read_wiring(tmp_path)
        assert snapshot.site.nodes[0].device.device_library_id == "old-library-id"
        assert response.site.nodes[0].device.device_library_id == "new-library-id"
        assert response.follow_status.site[0].state == "updated"
        assert all(graph.nodes[0].device.device_library_id == "new-library-id" for graph in response.targets.values())
        assert path.read_bytes() == before
        save_wiring(tmp_path, WiringBody(
            base_revision=response.revision, site=response.site, targets=response.targets,
        ))
    assert yaml.safe_load(path.read_bytes())["sites"]["default"] == default
    with bound_setup("default"):
        response = read_wiring(tmp_path)
        assert response.site == WiringGraph.model_validate(default)
        assert response.follow_status.site[0].state == "blocked"
