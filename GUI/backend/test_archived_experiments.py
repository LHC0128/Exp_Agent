"""代码归档的卡片、隔离、兼容及源文件完整性回归，不连接硬件。"""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
import yaml

from . import main
from lab_workflows.experiments import catalog, get_experiment, list_experiments
from lab_workflows.experiments.archived import ARCHIVED_EXPERIMENTS, ARCHIVE_PATH

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_bytes((ROOT / "params/experiment_catalog.yaml").read_bytes())
    with patch.object(catalog, "catalog_path", return_value=path), patch.object(main, "ROOT", tmp_path):
        with TestClient(main.app) as client:
            yield client


def test_archive_source_files_preserve_pre_move_bytes():
    manifest = json.loads((ROOT / ARCHIVE_PATH / "manifest.json").read_text(encoding="utf-8"))
    for original, digest in manifest["files"].items():
        archived = ROOT / ARCHIVE_PATH / original
        assert archived.is_file(), original
        assert sha256(archived.read_bytes()).hexdigest() == digest, original


def test_gui_startup_does_not_import_archived_implementations():
    code = """
import sys
from pathlib import Path
from GUI.backend import main
main.experiments()
main.experiment("rf-sensitivity-aw-r")
archive = (Path.cwd() / "old/experiments").resolve()
loaded = [name for name, module in sys.modules.items()
          if getattr(module, "__file__", None) and archive in Path(module.__file__).resolve().parents]
assert not loaded, loaded
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, capture_output=True, text=True)


def test_active_registry_excludes_archive_and_old_python_imports_still_work():
    active = {item.id for item in list_experiments()}
    assert active.isdisjoint(ARCHIVED_EXPERIMENTS)
    assert {item.id for item in list_experiments(include_archived=True)} == active | ARCHIVED_EXPERIMENTS.keys()
    for experiment_id, card in ARCHIVED_EXPERIMENTS.items():
        legacy = get_experiment(experiment_id)
        assert legacy.data_type == card.data_type
        assert legacy.schema()["fields"]
        assert (ROOT / card.acquisition_program).is_file()
        assert (ROOT / card.analysis_program).is_file()


def test_cards_survive_old_catalog_and_cannot_be_restored_without_code(client, tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("schema_version: 2\n", encoding="utf-8")
    original = path.read_bytes()
    cards = {item["id"]: item for item in client.get("/api/experiments").json()}
    assert path.read_bytes() == original
    for experiment_id in ARCHIVED_EXPERIMENTS:
        card = cards[experiment_id]
        assert card["archived"] and card["archive_path"] == ARCHIVE_PATH
        assert not card["can_analyze"] and not card["supports_cancel"]
        assert "仅保留卡片" in card["description"]
        assert client.get(f"/api/experiments/{experiment_id}").json() == card
        response = client.put(f"/api/experiments/{experiment_id}/archive", json={"archived": False})
        assert response.status_code == 422
        assert "手动恢复" in response.json()["detail"]
        assert path.read_bytes() == original
        (tmp_path / "data" / ARCHIVED_EXPERIMENTS[experiment_id].data_type / "old_run" / "results").mkdir(parents=True)
    with patch.object(main, "_read_yaml", side_effect=AssertionError("全局历史不应读取归档运行")):
        assert client.get("/api/runs").json()["total"] == 0


def test_archived_card_metadata_is_editable_without_touching_parameters(client):
    experiment_id = "rf-sensitivity-aw-r"
    response = client.put(f"/api/experiments/{experiment_id}/metadata", json={"title": "归档名称", "description": "归档介绍"})
    assert response.status_code == 200
    card = response.json()["experiment"]
    assert card["title"] == "归档名称" and card["archived"]
    assert card["description"] == "归档介绍" and card["archive_path"] == ARCHIVE_PATH


@pytest.mark.parametrize("experiment_id", ARCHIVED_EXPERIMENTS)
def test_old_runtime_and_history_urls_are_blocked_before_any_job(client, experiment_id):
    base = f"/api/experiments/{experiment_id}"
    with patch.object(main.manager, "create", side_effect=AssertionError("归档实验不得创建任务")):
        requests = [
            ("GET", f"{base}/schema"), ("POST", f"{base}/preflight"),
            ("PUT", f"{base}/defaults"), ("POST", f"{base}/derive"),
            ("POST", f"{base}/runs"), ("GET", f"{base}/runs"),
            ("GET", f"{base}/runs/fake/summary"), ("POST", f"{base}/compare"),
            ("GET", f"{base}/workflow"), ("POST", f"{base}/sessions"),
            ("GET", f"{base}/sessions/active"), ("POST", f"{base}/runs/fake/analysis"),
            ("GET", f"/api/runs?experiment_id={experiment_id}"),
            ("DELETE", f"/api/runs/{experiment_id}/fake"),
            ("GET", f"/api/runs/{experiment_id}/fake/artifacts/result.png"),
        ]
        for method, url in requests:
            response = client.request(method, url, json={"parameters": {}} if method in {"POST", "PUT"} else None)
            assert response.status_code == 410, (method, url, response.text)
            assert ARCHIVE_PATH in response.json()["detail"]
    assert not any(f"/api/experiments/{experiment_id}/" in path for path in main.app.openapi()["paths"])
