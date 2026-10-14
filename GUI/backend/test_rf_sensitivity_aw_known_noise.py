"""已知噪声实验历史、流程与重分析入口无硬件验证。"""
from unittest.mock import Mock
from pathlib import Path
import shutil
from fastapi.testclient import TestClient
import pytest
import yaml
from GUI.backend import main
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise import run_format
from lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.definition import ADAPTER

BASE = "/api/experiments/" + run_format.EXPERIMENT_ID


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.manager, "has_active_kind", lambda _: False)
    relative = Path("lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/RF 已知噪声灵敏度.excalidraw")
    (tmp_path / relative).parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[2] / relative, tmp_path / relative)
    return TestClient(main.app)


def create_run(root, *, schema_version=ADAPTER.params_type.schema_version):
    path = root / "data" / run_format.DATA_TYPE / "1014_120000_known"
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    config = dict(experiment_id=run_format.EXPERIMENT_ID, schema_version=schema_version,
        execution_mode="typed_workflow", acquisition_method=run_format.ACQUISITION_METHOD,
        parameters={"RUN_TAG": "saved", "CONTROL_WAVEFORM_FILES": ["v9/waveforms/optimal_control_waveform.csv"]},
        completion_status="completed", phase_calibration_status="completed",
        xy_phase_calibration={"converged": True}, analysis_status="not_started")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.mark.parametrize("schema_version", [2, 3, 4])
def test_history_summary_missing_results_and_invalid_ids(client, tmp_path, schema_version):
    path = create_run(tmp_path, schema_version=schema_version)
    before = (path / "experiment_config.yaml").read_bytes()
    page = client.get(BASE + "/runs").json()
    assert page["total"] == 1
    assert page["runs"][0]["run_id"] == path.name
    summary = client.get(BASE + f"/runs/{path.name}/summary").json()
    assert summary["parameters"]["RUN_TAG"] == "saved"
    assert summary["artifacts"] == {}
    assert summary["can_analyze"] is True
    assert client.get(BASE + "/runs/missing/summary").status_code == 404
    assert client.get(BASE + "/runs/bad%5Cname/summary").status_code in (400, 404, 422)
    assert before == (path / "experiment_config.yaml").read_bytes()
    generic = client.get("/api/runs", params={"experiment_id": run_format.EXPERIMENT_ID}).json()
    assert generic["total"] == 1


def test_workflow_download_and_schema_are_hardware_free(client):
    flow = client.get(BASE + "/workflow")
    assert flow.status_code == 200
    assert len(flow.json()["acquisition"]) == 17
    assert "CONTROL_WAVEFORM_FILES" in flow.json()["parameter_impacts"]["acquisition"]
    diagram = client.get(BASE + "/workflow/diagram")
    assert diagram.status_code == 200
    assert diagram.json()["type"] == "excalidraw"
    assert client.get(BASE + "/sessions/active").json() is None


@pytest.mark.parametrize("schema_version", [2, 3, 4])
def test_reanalysis_route_uses_own_adapter(client, tmp_path, monkeypatch, schema_version):
    path = create_run(tmp_path, schema_version=schema_version)
    job = Mock(id="analysis", status="queued", kind=f"analyze:{run_format.EXPERIMENT_ID}")
    def create(kind, target, **kwargs):
        job.kind = kind
        job.public.return_value = {"id": "analysis", "kind": kind, "status": "queued"}
        return job
    monkeypatch.setattr(main.manager, "create", create)
    response = client.post(f"/api/runs/{path.name}/analyze", json={"experiment_id": run_format.EXPERIMENT_ID})
    assert response.status_code == 200
    assert response.json()["id"] == "analysis"
    assert run_format.EXPERIMENT_ID in job.kind


@pytest.mark.parametrize("changes", [
    {"schema_version": 1}, {"schema_version": 5},
    {"experiment_id": "rf-sensitivity-aw"}, {"experiment_id": "rf-sensitivity-aw-r"},
    {"acquisition_method": "direct_aw_x"}, {"execution_mode": "legacy_script"},
    {"parameters": None}, {"completion_status": "failed"},
    {"phase_calibration_status": "failed"}, {"xy_phase_calibration": {"converged": False}},
])
def test_analysis_qualification_keeps_identity_and_quality_checks(tmp_path, changes):
    path = create_run(tmp_path)
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    config.update(changes)
    assert run_format.analysis_block_reason(config)


def test_phase_response_comparison_available_in_history_and_downloads(client, tmp_path):
    path = create_run(tmp_path)
    for suffix in ("png", "pdf"):
        name = "z_phase_response_comparison." + suffix
        (path / "results" / name).write_bytes(b"phase comparison")
    summary = client.get(BASE + f"/runs/{path.name}/summary").json()
    for suffix in ("png", "pdf"):
        name = "z_phase_response_comparison." + suffix
        url = summary["artifacts"][name]
        assert client.get(url).content == b"phase comparison"


def test_all_control_full_analysis_plots_and_downloads(client, tmp_path):
    path = create_run(tmp_path)
    versions = ("v9", "常数控制", "v10")
    (path / "results/analysis.yaml").write_text(yaml.safe_dump({"controls": dict.fromkeys(versions, {})},
        allow_unicode=True), encoding="utf-8")
    for version in versions[:2]:
        results = path / "controls" / version / "results"
        results.mkdir(parents=True)
        for suffix in ("png", "pdf"):
            (results / ("full_analysis." + suffix)).write_bytes((version + suffix).encode("utf-8"))
    config_before = (path / "experiment_config.yaml").read_bytes()
    response = client.get(BASE + f"/runs/{path.name}/summary")
    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    for version in versions[:2]:
        for suffix in ("png", "pdf"):
            url = artifacts[f"controls/{version}/full_analysis.{suffix}"]
            assert client.get(url).content == (version + suffix).encode("utf-8")
    assert "controls/v10/full_analysis.png" not in artifacts
    assert client.get(BASE + f"/runs/{path.name}/controls/v10/artifacts/full_analysis.png").status_code == 404
    assert client.get(BASE + f"/runs/{path.name}/controls/v9/artifacts/experiment_config.yaml").status_code == 404
    assert client.get(BASE + f"/runs/{path.name}/controls/bad%5Cname/artifacts/full_analysis.png").status_code in (400, 404)
    assert (path / "experiment_config.yaml").read_bytes() == config_before


def test_control_plot_cannot_escape_run_directory(client, tmp_path, monkeypatch):
    path = create_run(tmp_path)
    (path / "results/analysis.yaml").write_text(yaml.safe_dump({"controls": {"v9": {}, "../outside": {}, "\x00bad": {}}}), encoding="utf-8")
    external = tmp_path / "outside.png"
    external.write_bytes(b"outside")
    candidate = path / "controls/v9/results/full_analysis.png"
    resolve = Path.resolve
    def redirected_resolve(self, *args, **kwargs):
        return external if self == candidate else resolve(self, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected_resolve)
    artifacts = client.get(BASE + f"/runs/{path.name}/summary").json()["artifacts"]
    assert not any(key.startswith("controls/") for key in artifacts)
    assert client.get(BASE + f"/runs/{path.name}/controls/v9/artifacts/full_analysis.png").status_code == 404
