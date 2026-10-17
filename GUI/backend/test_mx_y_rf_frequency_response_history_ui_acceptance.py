"""历史诊断补修的独立只读路由验收；可变分析只写 pytest 临时目录。"""
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis import AnalysisWorkflow
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis_steps import AnalysisQualityError
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.test_analysis_stages import build_run
from lab_workflows.experiment_modules.mx_y_rf_frequency_response.test_workflow import copy_config
from .jobs import JobManager
from .mx_y_rf_frequency_response import MxYRFFrequencyResponseSessionService, create_router


BASE = "/api/experiments/mx-y-rf-frequency-response"


def inventory(directory):
    return {p.relative_to(directory).as_posix(): {
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "mtime_ns": p.stat().st_mtime_ns, "size": p.stat().st_size,
    } for p in directory.rglob("*") if p.is_file()}


def readonly_client(root, monkeypatch):
    manager = JobManager()

    def reject_job(*args, **kwargs):
        raise AssertionError("只读 GET 不得创建任务")

    monkeypatch.setattr(manager, "create", reject_job)
    service = MxYRFFrequencyResponseSessionService(lambda: root, manager)
    monkeypatch.setattr(service.adapter, "_execute", reject_job)
    app = FastAPI()
    app.include_router(create_router(service, include_history=True))
    return TestClient(app), manager


def strict_json(response):
    assert response.status_code == 200, response.text

    def reject_constant(value):
        raise AssertionError(f"正规只读 API 返回了非标准 JSON 常量 {value}")

    return json.loads(response.content, parse_constant=reject_constant)


@pytest.mark.parametrize("run_id", ["0817_105712_mx_y_rf_freq_resp", "0817_115725_mx_y_rf_freq_resp"])
def test_real_history_normal_readonly_get_strict_json_and_download_bytes(monkeypatch, record_property, run_id):
    root = Path(__file__).resolve().parents[2]
    directory = root / "data/Mx_Y_RF_Frequency_Response" / run_id
    if not directory.is_dir():
        pytest.skip("本机未提供此只读历史样本")
    before = inventory(directory)
    report_bytes = (directory / "results/analysis.json").read_bytes()
    assert b"NaN" in report_bytes
    expected = json.loads(report_bytes, parse_constant=lambda value: None)
    client, manager = readonly_client(root, monkeypatch)
    with client:
        summary = strict_json(client.get(f"{BASE}/runs/{run_id}/summary"))
        state = strict_json(client.get(f"{BASE}/runs/{run_id}/analysis"))
        assert state["summary"] == expected
        curves = state["summary"]["per_amplitude"]
        assert len(curves) == 5
        assert sum(curve["uncertainties"]["gamma_hz"] is None for curve in curves) == 2
        assert sum(curve["relative_gamma_uncertainty"] is None for curve in curves) == 2
        assert {name: value["status"] for name, value in state["steps"].items()} == {
            "B01": "pending", "B02": "pending", "B03": "pending"}
        assert summary["analysis_status"] == "not_started"
        downloads = {}
        for name in ("analysis.json", "frequency_response.png", "s_vs_amplitude.png"):
            response = client.get(summary["artifacts"]["results/" + name])
            original = (directory / "results" / name).read_bytes()
            assert response.status_code == 200 and response.content == original
            if name.endswith(".png"):
                assert response.headers["content-type"] == "image/png"
                assert original.startswith(b"\x89PNG\r\n\x1a\n")
            downloads[name] = {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}
    assert inventory(directory) == before
    assert not manager.jobs and not manager.hardware_lock.locked()
    record_property("readonly_history", json.dumps({"run_id": run_id,
        "mode": "正规 create_router GET，只读真实目录，不执行分析",
        "curve_count": 5, "null_gamma_uncertainty_count": 2, "original_files": len(before),
        "sha_mtime_size_unchanged": True, "downloads": downloads}, ensure_ascii=False))


def test_current_real_quality_failure_fingerprints_win_over_broken_legacy_json(tmp_path, monkeypatch):
    copy_config(tmp_path)
    directory = build_run(tmp_path, amplitudes=(0.01,), noise=0.02, seed=19)
    analysis = AnalysisWorkflow(directory, {"FIT_R_SQUARED_MIN": 1.0})
    with pytest.raises(AnalysisQualityError):
        analysis.execute(["B01", "B02"])
    state_path = directory / "analysis_state.json"
    recorded = json.loads(state_path.read_text(encoding="utf-8"))
    (directory / "results/analysis.json").write_text("{broken legacy JSON", encoding="utf-8")
    before = inventory(directory)
    client, manager = readonly_client(tmp_path, monkeypatch)
    with client:
        returned = strict_json(client.get(f"{BASE}/runs/{directory.name}/analysis"))
    assert returned["summary"] == recorded["summary"]
    assert returned["summary"]["status"] == "quality_failed"
    assert returned["fingerprint"] == recorded["fingerprint"] == analysis.input_fingerprint()
    failed = returned["steps"]["B02"]
    for key, value in recorded["steps"]["B02"].items():
        assert failed[key] == value
    for name, signature in failed["files"].items():
        content = (directory / "results" / name).read_bytes()
        assert signature == [len(content), hashlib.sha256(content).hexdigest()]
    assert returned["steps"]["B03"]["status"] == "pending"
    assert inventory(directory) == before
    assert not manager.jobs and not manager.hardware_lock.locked()
