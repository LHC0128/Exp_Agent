"""多控制的摘要、逐控制审计下载与路径隔离。"""
import json
from .test_rf_sensitivity_direct_aw import client, BASE
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_control_comparison import multi_result_run
from lab_workflows.experiment_modules.rf_sensitivity_direct_aw.run_format import DATA_TYPE


def test_multi_summary_exposes_independent_plots_and_quality_files(client, tmp_path):
    directory = tmp_path / "data" / DATA_TYPE / "multi"
    multi_result_run(directory)
    output = directory / "controls/v1/results"
    (output / "full_analysis.png").write_bytes(b"control image")
    (output / "noise_rejection_summary.json").write_text(json.dumps({"n_records": 5, "n_accepted": 4, "n_rejected": 1}), encoding="utf-8")
    (output / "noise_record_quality.csv").write_text("filename,accepted\na,True\n", encoding="utf-8")
    response = client.get(f"{BASE}/runs/multi/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["can_analyze"] and data["can_compare"]
    assert client.get(data["artifacts"]["controls/v1/full_analysis.png"]).content == b"control image"
    quality = client.get(data["artifacts"]["controls/v1/noise_rejection_summary.json"])
    assert quality.status_code == 200 and quality.json()["n_rejected"] == 1
    assert client.get(data["artifacts"]["controls/v1/noise_record_quality.csv"]).status_code == 200
    assert client.get(f"{BASE}/runs/multi/controls/v1/artifacts/experiment_config.yaml").status_code == 404
    assert client.get(f"{BASE}/runs/multi/controls/v2/artifacts/full_analysis.png").status_code == 404
    assert client.get(f"{BASE}/runs/multi/controls/..%5Coutside/artifacts/full_analysis.png").status_code == 400
