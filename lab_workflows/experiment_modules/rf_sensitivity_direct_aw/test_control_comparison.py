"""多控制与旧单控制混合对比；保持来源只读和按运行选择的 UI 契约。"""
import json
from pathlib import Path
import numpy as np
import pytest
import yaml
from .test_comparison import result_run, snapshot
from .comparison import compare_runs
from .run_format import comparison_block_reason


def multi_result_run(directory):
    files = [f"{name}/waveforms/optimal_control_waveform.csv" for name in ("v1", "v2")]
    controls = {}
    for name, relative in zip(("v1", "v2"), files):
        child = directory / "controls" / name
        result_run(child)
        config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        config.update(raw_format_version=1, control_layout="single")
        config["parameters"].update(ARB_WAVEFORM_FILE=relative, CONTROL_WAVEFORM_FILES=[relative])
        (child / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        controls[name] = {"source_file": relative, "xy_phase_calibration": {"converged": True},
                          "steps": {"A12": {"status": "completed"}}}
    config.update(schema_version=4, raw_format_version=2, control_layout="controls", controls=controls, controls_sealed=True)
    config["parameters"].update(ARB_WAVEFORM_FILE=files[0], CONTROL_WAVEFORM_FILES=files)
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    output = directory / "results"
    output.mkdir()
    (output / "analysis.yaml").write_text(yaml.safe_dump({"controls": {name: {} for name in controls}}), encoding="utf-8")
    (output / "response_fits.json").write_text("{}", encoding="utf-8")
    np.savez(output / "sensitivity.npz", placeholder=np.array([1]))
    return {"run_id": directory.name, "legend": "Multi", "run_dir": str(directory)}


def test_mixed_comparison_expands_curves_without_duplicate_run_selection(tmp_path):
    multi = multi_result_run(tmp_path / "multi")
    legacy = result_run(tmp_path / "legacy")
    before = snapshot(tmp_path)
    result = compare_runs([multi, legacy], tmp_path / "comparison")
    assert len(result["runs"]) == 2 and len(result["curves"]) == 3
    assert [row["run_id"] for row in result["runs"]] == ["multi", "legacy"]
    assert [row["legend"] for row in result["curves"]] == ["Multi / v1", "Multi / v2", "legacy"]
    assert all((tmp_path / name).read_bytes() for name in before)
    for relative, digest in before.items():
        assert snapshot(tmp_path)[relative] == digest
    for name in ("full_analysis_comparison.png", "full_analysis_comparison.pdf", "comparison.json"):
        assert (tmp_path / "comparison" / name).is_file()
    with pytest.raises(ValueError, match="源运行目录"):
        compare_runs([multi, legacy], Path(multi["run_dir"]) / "forbidden")


def test_unsealed_container_and_incomplete_child_are_not_comparable(tmp_path):
    item = multi_result_run(tmp_path / "multi")
    directory = Path(item["run_dir"])
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["controls_sealed"] = False
    assert "封存" in comparison_block_reason(directory, config)
    config["controls_sealed"] = True
    (directory / "controls/v2/results/sensitivity.npz").unlink()
    assert comparison_block_reason(directory, config)
