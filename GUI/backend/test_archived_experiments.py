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
from lab_workflows.acquisition_policy import ACQUISITION_EXCEPTIONS
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
from lab_workflows.experiments.archived import ARCHIVED_EXPERIMENTS
main.experiments()
for experiment_id in ARCHIVED_EXPERIMENTS:
    main.experiment(experiment_id)
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
    assert "dg-am-comp-transfer-check" in ACQUISITION_EXCEPTIONS
    assert "temperature-switch-pid-cycle" in ACQUISITION_EXCEPTIONS
    assert "aw-waveform-scope-check" in ACQUISITION_EXCEPTIONS
    for name in ("Temperature_Switch_PID_Cycle_Test.py", "Temperature_Switch_PID_Cycle_Test_plot.py"):
        assert not (ROOT / "experiments" / name).exists()
    for experiment_id, card in ARCHIVED_EXPERIMENTS.items():
        legacy = get_experiment(experiment_id)
        assert legacy.data_type == card.data_type
        assert legacy.schema_version == card.schema_version
        assert legacy.schema()["fields"]
        if card.execution_mode == "legacy_script":
            adapter = legacy.schema_provider.__self__
            assert adapter.script.resolve() == (ROOT / card.acquisition_program).resolve()
            if card.analysis_program:
                assert adapter.plot.resolve() == (ROOT / card.analysis_program).resolve()
            else:
                assert adapter.plot is None
                assert legacy.analysis_runner is None
                assert not legacy.auto_analyze
        assert (ROOT / card.acquisition_program).is_file()
        if card.analysis_program:
            assert (ROOT / card.analysis_program).is_file()


def test_aw_scope_archive_preserves_original_script_without_analyzer():
    experiment_id = "aw-waveform-scope-check"
    card = ARCHIVED_EXPERIMENTS[experiment_id]
    assert not (ROOT / "experiments/AW_Control_Waveform_Scope_Check.py").exists()
    assert card.data_type == "AW_Control_Waveform_Scope_Check"
    assert card.schema_version == 1 and card.execution_mode == "legacy_script"
    assert card.analysis_program is None
    legacy = get_experiment(experiment_id)
    assert not legacy.public()["can_analyze"]
    # AST 读取旧参数，不导入或执行带顶层硬件操作的归档脚本。
    values = legacy.schema_provider.__self__.defaults()
    assert values["KEEP_OUTPUTS_AFTER_EXIT"] is True
    assert values["ARB_WAVEFORM_FILE"] == "control_waveformAW.csv"
    assert (ROOT / "experiments" / values["ARB_WAVEFORM_FILE"]).is_file()


@pytest.mark.parametrize(("experiment_id", "script", "has_analysis"), [
    ("static-sensitivity-optimize", "Static_Magnetic_Field_Sensitivity_Optimize.py", False),
    ("aw-const-omega-check", "AW_ConstOmega_Calibration_Check.py", True),
    ("xy-dc-voltage-calibration", "XY_DC_Voltage_Calibration.py", True),
    ("xy-aw-voltage-calibration", "XY_AW_Voltage_Calibration.py", True),
])
def test_legacy_optimization_and_xy_archives_preserve_identity_and_entrypoints(experiment_id, script, has_analysis):
    card = ARCHIVED_EXPERIMENTS[experiment_id]
    assert card.data_type == Path(script).stem
    assert card.schema_version == 2 and card.execution_mode == "legacy_script"
    assert card.acquisition_program == f"{ARCHIVE_PATH}/experiments/{script}"
    programs = [script]
    if has_analysis:
        plot = f"{Path(script).stem}_plot.py"
        assert card.analysis_program == f"{ARCHIVE_PATH}/experiments/{plot}"
        programs.append(plot)
    else:
        assert card.analysis_program is None
    for program in programs:
        assert not (ROOT / "experiments" / program).exists()
        assert (ROOT / ARCHIVE_PATH / "experiments" / program).is_file()
    legacy = get_experiment(experiment_id)
    assert bool(legacy.analysis_runner) == has_analysis
    assert bool(legacy.public()["can_analyze"]) == has_analysis
    assert "SYSTEM_SETTLE_S" in {field["name"] for field in legacy.schema()["fields"]}
    active = {item.id for item in list_experiments()}
    assert experiment_id not in active
    assert {"static-sensitivity", "xy-direct-aw-dc-calibration"} <= active
    assert "static-sensitivity-optimize-local" not in active


def test_local_optimization_archive_preserves_shared_identity_and_recovery_files():
    experiment_id = "static-sensitivity-optimize-local"
    card = ARCHIVED_EXPERIMENTS[experiment_id]
    assert card.data_type == get_experiment("static-sensitivity").data_type
    assert card.execution_mode == "typed_workflow" and card.schema_version == 3
    assert not card.public()["can_analyze"] and not card.public()["auto_analyze"]
    assert (ROOT / "params/experiments/static-sensitivity-optimize-local.yaml").is_file()
    for path in (card.acquisition_program, card.analysis_program,
            "old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/workflow.excalidraw",
            "old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/test_workflow.py",
            "old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/test_analysis.py",
            "old/experiments/GUI/backend/test_static_sensitivity_optimize_local.py",
            "old/experiments/GUI/frontend/src/pages/StaticSensitivityOptimizeLocalPage.test.tsx"):
        assert (ROOT / path).is_file(), path
    restored = get_experiment(experiment_id)
    assert restored.id == experiment_id and restored.schema()["fields"]
    assert experiment_id not in main.WORKFLOW_RUN_FORMATS
    assert not hasattr(main, "static_local_sessions")


def test_shared_static_history_keeps_main_and_blocks_archived_alias_access(client, tmp_path):
    base = tmp_path / "data" / "Static_Magnetic_Field_Sensitivity"
    for run_id, experiment_id in (("main_run", "static-sensitivity"), ("local_batch", "static-sensitivity-optimize-local")):
        path = base / run_id
        (path / "results").mkdir(parents=True)
        (path / "experiment_config.yaml").write_text(json.dumps({
            "experiment_id": experiment_id, "experiment_type": "Static_Magnetic_Field_Sensitivity",
        }), encoding="utf-8")
        (path / "results/record.txt").write_text("保留原始历史", encoding="utf-8")
    result = client.get("/api/runs?experiment_id=static-sensitivity").json()
    assert result["total"] == 1 and result["runs"][0]["id"] == "main_run"
    archived = base / "local_batch"
    before = {path: path.read_bytes() for path in archived.rglob("*") if path.is_file()}
    with patch.object(main.manager, "create", side_effect=AssertionError("归档批次不得借主实验启动分析")):
        assert client.post("/api/runs/local_batch/analyze", json={"experiment_id": "static-sensitivity"}).status_code == 404
        assert client.delete("/api/runs/static-sensitivity/local_batch").status_code == 404
        assert client.get("/api/runs/static-sensitivity/local_batch/artifacts/record.txt").status_code == 404
    assert {path: path.read_bytes() for path in before} == before


def test_rf_am_frequency_archive_preserves_directaw_shared_analysis():
    card = ARCHIVED_EXPERIMENTS["rf-sensitivity-aw-frequency"]
    shared_plot = ROOT / "experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py"
    direct = get_experiment("rf-sensitivity-direct-aw-frequency")
    assert direct.id in {item.id for item in list_experiments()}
    assert (ROOT / direct.analysis_program).resolve() == shared_plot.resolve()
    assert shared_plot.read_bytes() == (ROOT / card.analysis_program).read_bytes()
    assert not (ROOT / "experiments/RF_Field_Sensitivity_AW_FreqSweep.py").exists()


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
        directory = tmp_path / "data" / ARCHIVED_EXPERIMENTS[experiment_id].data_type / "old_run"
        (directory / "results").mkdir(parents=True)
        (directory / "experiment_config.yaml").write_text(json.dumps({
            "experiment_id": experiment_id, "experiment_type": ARCHIVED_EXPERIMENTS[experiment_id].data_type,
        }), encoding="utf-8")
    active_types = {item.data_type for item in list_experiments()}
    def read_shared_config(path):
        # 共用类型目录必须读身份才能区分；独占归档目录仍不得读取。
        assert path.parent.parent.name in active_types, "全局历史不应读取独占归档目录"
        return json.loads(path.read_text(encoding="utf-8"))
    with patch.object(main, "_read_yaml", side_effect=read_shared_config):
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
