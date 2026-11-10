"""运行记录接线快照接口的只读、兼容与损坏回归。"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from GUI.backend import main
from lab_workflows.test_wiring import graph_data
from lab_workflows.wiring import WiringGraph, read_wiring_snapshot

EXPERIMENT_ID = "demo-wiring"
DATA_TYPE = "DemoWiring"
NOTE = "运行记录中的接线快照，不代表当时硬件实际状态或安全校验"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)

    def get_experiment(experiment_id):
        if experiment_id != EXPERIMENT_ID:
            raise KeyError(experiment_id)
        return SimpleNamespace(id=EXPERIMENT_ID, data_type=DATA_TYPE)

    monkeypatch.setattr(main, "get_experiment", get_experiment)
    return tmp_path


@pytest.fixture
def client():
    test_client = TestClient(main.app)
    yield test_client
    test_client.close()


def write_wiring(root: Path, targets=("demo-wiring",), corrupt: bool = False) -> None:
    params = root / "params"
    params.mkdir(exist_ok=True)
    if corrupt:
        (params / "wiring.yaml").write_text("schema_version: 1\nsite: not-a-graph\ntargets: {}\n", encoding="utf-8")
        return
    empty = {"nodes": [], "edges": []}
    document = {"schema_version": 1, "site": empty, "targets": {key: deepcopy(empty) for key in targets}}
    (params / "wiring.yaml").write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def create_run(root: Path, name: str, config: dict) -> Path:
    run = root / "data" / DATA_TYPE / name
    run.mkdir(parents=True)
    (run / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    return run


def recorded_config(root: Path, revision: str, snapshot: dict | None) -> dict:
    return {
        "experiment_type": DATA_TYPE,
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "timestamp": "1001_120000",
        "parameters": {},
        "wiring_revision": revision,
        "wiring_snapshot": snapshot,
        "wiring_snapshot_error": None,
    }


def snapshot_payload(snapshot) -> dict:
    return snapshot.model_dump(mode="json", exclude={"revision"})


def files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*") if path.is_file()
    }


def test_recorded_snapshot_is_returned_and_matches_current_wiring(root, client):
    write_wiring(root, targets=(EXPERIMENT_ID, "another-experiment"))
    site, target = graph_data(), graph_data()
    target["edges"][0]["label"] = "目标线"
    document = {
        "schema_version": 1, "site": site,
        "targets": {EXPERIMENT_ID: target, "another-experiment": graph_data()},
    }
    (root / "params" / "wiring.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    snapshot = read_wiring_snapshot(root)
    run = create_run(root, "1001_120000_first", recorded_config(
        root, snapshot.revision, snapshot_payload(snapshot),
    ))
    before = files(run)

    response = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["recorded_revision"] == snapshot.revision == body["current_revision"]
    assert body["matches_current"] is True
    assert body["recorded_at"] == "1001_120000"
    assert body["site"] == WiringGraph.model_validate(site).model_dump(mode="json")
    assert body["targets"][EXPERIMENT_ID]["edges"][0]["label"] == "目标线"
    assert body["current_error"] is None and body["reason"] is None
    assert body["note"] == NOTE
    assert files(run) == before


def test_changed_current_wiring_reports_mismatch_and_keeps_recorded_graph(root, client):
    write_wiring(root, targets=(EXPERIMENT_ID,))
    snapshot = read_wiring_snapshot(root)
    run = create_run(root, "1001_120000_changed", recorded_config(
        root, snapshot.revision, snapshot_payload(snapshot),
    ))
    write_wiring(root, targets=(EXPERIMENT_ID, "new-experiment"))

    body = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").json()
    assert body["available"] is True
    assert body["matches_current"] is False
    assert body["current_revision"] != body["recorded_revision"]
    assert body["targets"] == {EXPERIMENT_ID: {"nodes": [], "edges": []}}
    assert body["recorded_at"] == "1001_120000"


def test_recorded_empty_graph_is_available_not_missing(root, client):
    write_wiring(root, targets=())
    snapshot = read_wiring_snapshot(root)
    run = create_run(root, "1001_120000_empty", recorded_config(
        root, snapshot.revision, snapshot_payload(snapshot),
    ))
    body = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").json()
    assert body["available"] is True
    assert body["site"] == {"nodes": [], "edges": []}
    assert body["targets"] == {}
    assert body["matches_current"] is True


def test_old_run_is_unavailable_and_bytes_are_untouched(root, client):
    run = create_run(root, "1001_120000_old", {
        "experiment_type": DATA_TYPE, "schema_version": 1, "experiment_id": EXPERIMENT_ID,
        "timestamp": "1001_120000", "parameters": {},
    })
    before = files(run)
    body = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").json()
    assert body["available"] is False
    assert "早于接线快照记录" in body["reason"]
    assert body["site"] is None and body["targets"] is None
    assert body["matches_current"] is None
    assert body["recorded_at"] == "1001_120000"
    assert body["note"] == NOTE
    assert files(run) == before


def test_failed_snapshot_recording_explains_recorded_error(root, client):
    config = recorded_config(root, None, None)
    config["wiring_snapshot_error"] = "接线图损坏或结构非法：bad"
    run = create_run(root, "1001_120000_failed", config)
    body = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").json()
    assert body["available"] is False
    assert "未能记录接线快照" in body["reason"]
    assert "接线图损坏或结构非法" in body["reason"]


def test_identity_mismatch_and_cross_directory_are_404(root, client):
    config = recorded_config(root, None, None)
    config["experiment_id"] = "other-experiment"
    mismatched = create_run(root, "1001_120000_other", config)
    assert client.get(f"/api/runs/{EXPERIMENT_ID}/{mismatched.name}/wiring").status_code == 404

    other_base = root / "data" / "OtherData" / "1001_120000_elsewhere"
    other_base.mkdir(parents=True)
    (other_base / "experiment_config.yaml").write_text("experiment_id: demo-wiring\n", encoding="utf-8")
    assert client.get(f"/api/runs/{EXPERIMENT_ID}/{other_base.name}/wiring").status_code == 404


def test_corrupt_current_wiring_keeps_recorded_snapshot_and_error(root, client):
    write_wiring(root, targets=(EXPERIMENT_ID,))
    snapshot = read_wiring_snapshot(root)
    run = create_run(root, "1001_120000_corrupt", recorded_config(
        root, snapshot.revision, snapshot_payload(snapshot),
    ))
    write_wiring(root, corrupt=True)

    body = client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").json()
    assert body["available"] is True
    assert body["recorded_revision"] == snapshot.revision
    assert body["targets"] == {EXPERIMENT_ID: {"nodes": [], "edges": []}}
    assert body["current_revision"] is None
    assert body["matches_current"] is None
    assert "接线图" in body["current_error"]


def test_view_never_writes_params_or_run_config(root, client):
    write_wiring(root, targets=(EXPERIMENT_ID,))
    snapshot = read_wiring_snapshot(root)
    run = create_run(root, "1001_120000_readonly", recorded_config(
        root, snapshot.revision, snapshot_payload(snapshot),
    ))
    before = files(root)
    assert client.get(f"/api/runs/{EXPERIMENT_ID}/{run.name}/wiring").status_code == 200
    assert files(root) == before
    assert not (root / "params" / ".wiring.lock").exists()


def test_route_response_model_is_documented():
    schema = main.app.openapi()
    path = "/api/runs/{experiment_id}/{run_id}/wiring"
    assert path in schema["paths"]
    response = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response == {"$ref": "#/components/schemas/RunWiringResponse"}
