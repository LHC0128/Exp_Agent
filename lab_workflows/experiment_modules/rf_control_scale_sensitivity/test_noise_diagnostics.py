"""独立诊断复用真实分析算法，验证分类和只读入口。"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import numpy as np
import pytest
from .noise_diagnostics import classify_groups, diagnose
from .test_analysis import synthetic_run


def test_classification_distinguishes_stationary_bad_outlier_and_mixed():
    def row(scale, median, q25, rejected=0):
        return dict(control="C", scale=scale, n_rejected=rejected,
                    kept_median_record_fT_per_sqrtHz=median, kept_q25_record_fT_per_sqrtHz=q25)
    groups = classify_groups([row(1., 10., 9.), row(.9, 100., 90.), row(1.1, 10., 9., 1), row(.95, 100., 90., 1)])
    assert [group["classification"] for group in groups] == ["no_clear_problem", "group_wide", "outlier_driven", "mixed"]
    assert not classify_groups([row(.9, 100., 90.)])[0]["whole_group_assessed"]
    with pytest.raises(ValueError):
        classify_groups([], whole_group_ratio=1)


def test_diagnostics_is_read_only_and_exports_record_audit(tmp_path):
    run = synthetic_run(tmp_path / "run", control_name="常数控制", noise_records=9, raw_version=2)
    file = next(run.glob("controls/*/scales/*/raw/noise_D3_0003.npz"))
    with np.load(file) as saved:
        values = dict(saved)
    values["noise_x_V"] *= 100
    np.savez(file, **values)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in run.rglob("*") if path.is_file()}
    report = diagnose(run)
    output = Path(report["output_dir"])
    assert report["rejected_records"] == 1 and report["total_records"] == 27
    assert report["phase_calibration_scope"] == "per_control_scale"
    assert report["groups"][0]["classification"] == "outlier_driven"
    assert report["groups"][0]["rejected_records"] == ["noise_D3_0003.npz"]
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert not (run / "analysis_state.json").exists()
    assert not (run / "results/full_analysis.png").exists()
    for name in ("report.md", "group_summary.csv", "record_scores.csv", "diagnostics.json",
                 "noise_record_diagnostics.png", "noise_record_diagnostics.pdf"):
        assert (output / name).is_file()
    with pytest.raises(ValueError, match="已存在"):
        diagnose(run, output)
    with pytest.raises(ValueError, match="不能写入原始"):
        diagnose(run, run / "raw/report")


def test_reusable_module_entrypoint_accepts_explicit_directory(tmp_path):
    run = synthetic_run(tmp_path / "run")
    output = tmp_path / "diagnosis"
    result = subprocess.run([sys.executable, "-m",
        "lab_workflows.experiment_modules.rf_control_scale_sensitivity.noise_diagnostics",
        str(run), "--output", str(output)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
    assert report["total_groups"] == 3 and report["rejected_records"] == 0
