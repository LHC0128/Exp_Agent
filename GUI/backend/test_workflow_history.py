"""公共流程历史读取与无分析器工具的无硬件回归。"""

from types import SimpleNamespace
import json

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from .workflow_sessions import WorkflowSessionService, create_router


@pytest.fixture
def history_client(tmp_path):
    adapter = SimpleNamespace(id="history-test", data_type="History_Test", analysis_module=None)
    stages = SimpleNamespace(definition=lambda: dict(
        acquisition=[], analysis=[], parameter_impacts=dict(acquisition={}, analysis={}), diagram_url="/diagram",
    ))
    service = WorkflowSessionService(
        lambda: tmp_path, None, adapter=adapter, preflight=None, stages=stages,
        analysis_workflow=None, session_module="unused", diagram_file="unused",
    )
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    return TestClient(app), service, tmp_path / "data" / adapter.data_type


def save_run(base, name, **updates):
    path = base / name
    path.mkdir(parents=True)
    config = dict(experiment_id="history-test", timestamp=name, run_tag="原参数", completion_status="completed",
                  parameters={"RUN_TAG": "历史值", "SYSTEM_SETTLE_S": 3.5})
    config.update(updates)
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_history_snapshot_missing_results_pagination_and_real_artifact(history_client):
    client, service, base = history_client
    old = save_run(base, "1013_old")
    current = save_run(base, "1014_new", analysis_status="failed", analysis_error="拟合失败")
    (current / "results").mkdir()
    (current / "results" / "result.csv").write_text("frequency,value\n1,2\n", encoding="utf-8")
    archive = current / "results" / "archive"
    archive.mkdir()
    (archive / "old.png").write_bytes(b"old-result")
    prefix = "/api/experiments/history-test"

    response = client.get(f"{prefix}/runs?limit=1&offset=1")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["runs"][0]["run_id"] == old.name
    summary = client.get(f"{prefix}/runs/{current.name}/summary").json()
    assert summary["parameters"]["RUN_TAG"] == "历史值"
    assert summary["analysis_status"] == "failed" and summary["analysis_error"] == "拟合失败"
    assert set(summary["artifacts"]) == {"results/result.csv"}
    artifact = summary["artifacts"]["results/result.csv"]
    assert client.get(artifact).content == (current / "results" / "result.csv").read_bytes()
    assert client.get(f"{prefix}/runs/{old.name}/summary").json()["artifacts"] == {}
    assert client.get(f"{prefix}/runs?offset=-1").status_code == 422
    assert client.get(f"{prefix}/runs?limit=0").status_code == 422
    (current / "analysis_state.json").write_text(json.dumps({"summary": {"status": "completed"}}), encoding="utf-8")
    completed = client.get(f"{prefix}/runs/{current.name}/summary").json()
    assert completed["analysis_status"] == "completed" and completed["analysis_error"] == ""
    (current / "analysis_state.json").write_text(json.dumps({"summary": {"status": "failed", "error": "阶段失败"}}), encoding="utf-8")
    failed = client.get(f"{prefix}/runs/{current.name}/summary").json()
    assert failed["analysis_status"] == "failed" and failed["analysis_error"] == "阶段失败"


def test_history_rejects_bad_identity_missing_config_and_outside_files(history_client):
    client, service, base = history_client
    bad = save_run(base, "wrong", experiment_id="other")
    empty = base / "missing_config"
    empty.mkdir()
    prefix = "/api/experiments/history-test"
    assert client.get(f"{prefix}/runs/{bad.name}/summary").status_code == 422
    assert client.get(f"{prefix}/runs/{empty.name}/summary").status_code == 404
    assert client.get(f"{prefix}/runs/missing/summary").status_code == 404
    with pytest.raises(HTTPException) as exc:
        service.run_directory("../outside")
    assert exc.value.status_code == 404
    safe = save_run(base, "safe")
    assert client.get(f"{prefix}/runs/{safe.name}/files/%2E%2E%2Fexperiment_config.yaml").status_code == 404
    (safe / "experiment_config.yaml").write_text("- invalid\n", encoding="utf-8")
    assert client.get(f"{prefix}/runs/{safe.name}/summary").status_code == 422


def test_tool_has_no_fake_analysis_routes(history_client):
    client, service, base = history_client
    prefix = "/api/experiments/history-test"
    assert client.get(f"{prefix}/workflow").json()["analysis"] == []
    assert client.get(f"{prefix}/runs/run/analysis").status_code == 404
    assert client.post(f"{prefix}/runs/run/analysis", json={"revision": 0, "step_ids": ["B01"]}).status_code == 404
    with pytest.raises(HTTPException) as exc:
        service.analysis_state("run")
    assert exc.value.headers["X-Error-Code"] == "no_analyzer"


def test_history_list_keeps_valid_runs_when_other_configs_are_invalid(history_client):
    client, _, base = history_client
    save_run(base, "valid")
    save_run(base, "other", experiment_id="other")
    broken = save_run(base, "broken")
    (broken / "experiment_config.yaml").write_text("parameters: [", encoding="utf-8")
    response = client.get("/api/experiments/history-test/runs")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["run_id"] for item in response.json()["runs"]] == ["valid"]


@pytest.mark.parametrize("status", [404, 422, 500])
def test_history_list_skips_only_invalid_or_disappeared_runs(history_client, monkeypatch, status):
    client, service, base = history_client
    save_run(base, "unavailable")

    def unavailable(*args, **kwargs):
        raise HTTPException(status, "无法读取该运行")

    monkeypatch.setattr(service, "run_directory", unavailable)
    response = client.get("/api/experiments/history-test/runs")
    assert response.status_code == (500 if status == 500 else 200)
    if status != 500:
        assert response.json() == {"runs": [], "total": 0}


@pytest.mark.parametrize("python_loader", [False, True])
def test_yaml_readers_keep_safe_types_and_reject_python_tags(tmp_path, monkeypatch, python_loader):
    from .main import _read_yaml
    from .workflow_history import read_config

    if python_loader:
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    path = tmp_path / "config.yaml"
    payload = "名称: 历史参数\nvalues: &values [true, null, 1.5]\ncopy: *values\n"
    path.write_text(payload, encoding="utf-8")
    expected = yaml.safe_load(payload)
    assert read_config(path) == expected
    assert _read_yaml(path) == expected
    path.write_text("!!python/object/apply:builtins.list [[1, 2]]", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        read_config(path)
    assert exc.value.status_code == 422
    assert _read_yaml(path) is None


def test_recomputed_artifact_changes_url_and_returns_current_content(history_client):
    import os

    client, service, base = history_client
    run = save_run(base, "1014_recomputed")
    results = run / "results"
    results.mkdir()
    artifact = results / "analysis.json"
    artifact.write_text('{"gain": 1}', encoding="utf-8")
    endpoint = "/api/experiments/history-test/runs/1014_recomputed/summary"
    previous = client.get(endpoint).json()["artifacts"]["results/analysis.json"]
    stamp = artifact.stat()
    artifact.write_text('{"gain": 2}', encoding="utf-8")
    os.utime(artifact, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
    current = client.get(endpoint).json()["artifacts"]["results/analysis.json"]
    assert current != previous
    assert client.get(current).json() == {"gain": 2}
