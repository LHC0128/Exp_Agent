"""T1 算法、已验证历史格式、阶段指纹/归档和真实离线子进程回归。"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps as steps, run_format
from .models import T1CalibrationParams
from .fitting import fit_decay
from .storage import save_npz
from .test_workflow import ROOT, signal, small_params

REGRESSION_CASES = {"t1-calibration": {"analysis": {
    "sources": ["lab_workflows/experiment_modules/t1_calibration/analysis.py",
        "lab_workflows/experiment_modules/t1_calibration/analysis_steps.py", "lab_workflows/experiment_modules/t1_calibration/analysis_plotting.py",
        "lab_workflows/experiment_modules/t1_calibration/fitting.py", "lab_workflows/experiment_modules/t1_calibration/run_format.py",
        "experiments/T1_Calibration_plot.py"],
    "tests": ["test_whole_staged_numeric_figures_and_raw_preserved", "test_old_summary_only_and_raw_only_single",
        "test_real_historical_copies_match_saved_scientific_values", "test_real_offline_subprocess", "test_bad_fit_exports_diagnostics"]}}}


def synthetic_run(path, *, legacy=False, scan=True, waveforms=True):
    path = Path(path); (path/"raw").mkdir(parents=True)
    p = small_params(); p.do_power_scan = scan
    config = dict(experiment_type=run_format.DATA_TYPE, timestamp="0529_1405", pump_switch_freq_Hz=p.pump_switch_freq,
        pump_mod_amplitude_V=p.pump_mod_amplitude, pump_prep_time_s=p.pump_prep_time, probe_power_V=p.probe_power,
        x_field_amplitude_V=p.x_field_amplitude, temperature_C=p.tec_temperature,
        scope=dict(sampling_rate_Hz=p.scope_sampling_rate, acq_duration_s=p.acq_duration, acq_repeats=p.acq_repeats,
            trigger_source=p.trigger_source, volt_scale_V_per_div=p.scope_volt_scale))
    if not legacy:
        config.update(experiment_id=run_format.EXPERIMENT_ID, raw_format_version=1, schema_version=p.schema_version,
                      parameters=p.to_external(), completion_status="completed")
    (path/"experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    t, y = signal()
    if scan:
        powers = np.array([.03, .06, .09])
        waves = [y*(1+i*.1) for i in range(3)]
        fits = [fit_decay(t, wave, p.t1_bounds) for wave in waves]
        save_npz(path/"raw/power_scan_results.npz", probe_power=powers, T1=[f["T1"] for f in fits],
                 T1_err=[f["T1_err"] for f in fits], scale_used=[.05]*3, n_acquired=[2]*3)
        if waveforms:
            save_npz(path/"raw/power_scan_waveforms.npz", t_axis=t, vdiff_stack=waves,
                     popt_stack=[f["popt"] for f in fits], probe_power=powers)
    else:
        fit = fit_decay(t, y, p.t1_bounds)
        save_npz(path/"raw/t1_measurement.npz", t_axis=t[t >= 0], vdiff_avg=y[t >= 0], probe_power=.05,
                 T1=fit["T1"], T1_err=fit["T1_err"], popt=fit["popt"], pcov=fit["pcov"], t0=0, scale_used=.05)
    return path


def frozen_inputs(run):
    paths = sorted((run/"raw").glob("*"))
    if (run/"experiment_config.yaml").is_file(): paths.append(run/"experiment_config.yaml")
    return {p.relative_to(run).as_posix(): steps.signature(p) for p in paths if p.is_file()}


def test_whole_staged_numeric_figures_and_raw_preserved(tmp_path):
    one = synthetic_run(tmp_path/"one"); two = synthetic_run(tmp_path/"two")
    before = frozen_inputs(one)
    (one/"results").mkdir()
    (one/"results/T1_vs_probe_power.png").write_bytes(b"legacy-plot-without-state-or-manifest")
    result = analysis.analyze(one)
    assert any((p/"T1_vs_probe_power.png").read_bytes() == b"legacy-plot-without-state-or-manifest"
        for p in (one/"results").glob("before_*") if (p/"T1_vs_probe_power.png").is_file())
    ctx = analysis.AnalysisWorkflow(two)
    with pytest.raises(ValueError, match="缺少前置"): ctx.execute(["B02"])
    for item in ("B01", "B02", "B03"): ctx.execute([item])
    assert result["status"] == "completed" and frozen_inputs(one) == before
    with np.load(one/"results/fit_points.npz") as a, np.load(two/"results/fit_points.npz") as b:
        for key in a.files: np.testing.assert_array_equal(a[key], b[key])
        np.testing.assert_allclose(a["T1"], .025, rtol=.001)
    expected = ["T1_vs_probe_power", "inv_T1_vs_probe_power", "T1_all_decay_curves"]
    for name in expected:
        assert (one/f"results/{name}.png").is_file() and (one/f"results/{name}.pdf").is_file()
    assert ctx.snapshot()["options"] == {"T1_MIN_S": .0005, "T1_MAX_S": .5}


def test_point_indices_above_99_keep_acquisition_order(tmp_path):
    run = synthetic_run(tmp_path/"large")
    t, y = signal()
    for index, voltage in zip((99, 100, 101), (.03, .06, .09)):
        save_npz(run/f"raw/point_p{index:02d}.npz", t_axis=t, vdiff_avg=y, probe_power=voltage)
    data = steps.load_inputs(run)
    assert [p["probe_power"] for p in data["points"]] == [.03, .06, .09]


def test_old_summary_only_and_raw_only_single(tmp_path):
    scan = synthetic_run(tmp_path/"scan", legacy=True, waveforms=False)
    before = frozen_inputs(scan); result = analysis.analyze(scan)
    assert not result["has_waveforms"] and result["linear_fit_available"]
    assert not (scan/"results/T1_all_decay_curves.png").exists()
    assert frozen_inputs(scan) == before
    with np.load(scan/"raw/power_scan_results.npz") as raw, np.load(scan/"results/fit_points.npz") as fit:
        np.testing.assert_array_equal(raw["T1"], fit["T1"])
    single = synthetic_run(tmp_path/"single", legacy=True, scan=False)
    (single/"experiment_config.yaml").unlink()
    before = frozen_inputs(single)
    result = analysis.analyze(single)
    assert result["T1"] == pytest.approx(.025, rel=.001) and frozen_inputs(single) == before
    assert (single/"results/PDB_T1_decay.png").is_file() and (single/"results/T1_fit_residual.pdf").is_file()


def test_fingerprint_changed_options_archive_only_successors(tmp_path):
    run = synthetic_run(tmp_path/"run"); analysis.analyze(run)
    manifest = (run/"results/input_manifest.json").read_bytes()
    ctx = analysis.AnalysisWorkflow(run, {"T1_MAX_S": .4})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError): ctx.execute(["B03"])
    ctx.execute(["B02", "B03"])
    assert (run/"results/input_manifest.json").read_bytes() == manifest
    backups = list((run/"results").glob("before_*"))
    assert backups and not any((p/"input_manifest.json").exists() for p in backups)
    assert any((p/"T1_all_decay_curves.png").exists() for p in backups)
    (run/"results/T1_vs_probe_power.png").unlink()
    assert analysis.AnalysisWorkflow(run).states["B03"]["status"] == "stale"
    save_npz(run/"raw/power_scan_waveforms.npz", t_axis=[0, 1], vdiff_stack=[[1, 2]], probe_power=[.03])
    ctx = analysis.AnalysisWorkflow(run)
    previous = (run/"results/analysis.yaml").read_bytes()
    with pytest.raises(ValueError): ctx.execute(["B01"])
    assert (run/"results/analysis.yaml").read_bytes() == previous
    assert ctx.states["B01"]["status"] == "quality_failed"


def test_bad_fit_exports_diagnostics(tmp_path):
    run = synthetic_run(tmp_path/"bad", legacy=True, waveforms=False)
    save_npz(run/"raw/power_scan_results.npz", probe_power=[.03, .06], T1=[np.nan, np.nan], T1_err=[np.nan, np.nan])
    result = analysis.analyze(run)
    assert result["status"] == "completed" and result["scientific_quality"] == "diagnostic_only"
    assert result["valid_points"] == 0 and not result["linear_fit_available"]
    assert json.loads((run/"results/analysis.json").read_text(encoding="utf-8"))["rejection_reasons"]
    fit = fit_decay(np.arange(20)*.001, np.full(20, np.nan), (.0005, .5))
    assert np.isnan(fit["T1"]) and fit["reason"]


@pytest.mark.parametrize("edit", ["identity", "version", "running", "future_schema"])
def test_identity_version_and_running_rejected(tmp_path, edit):
    run = synthetic_run(tmp_path/"run")
    path = run/"experiment_config.yaml"; config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if edit == "identity": config["experiment_id"] = "other"
    elif edit == "version": config["raw_format_version"] = 99
    elif edit == "running": config["completion_status"] = "running"
    else: config["schema_version"] = 99
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError): analysis.AnalysisWorkflow(run)
    assert not (run/"results").exists()


def test_real_offline_subprocess(tmp_path):
    run = synthetic_run(tmp_path/"single", scan=False)
    before = frozen_inputs(run)
    result = subprocess.run([sys.executable, str(ROOT/"experiments/T1_Calibration_plot.py"), str(run)],
        cwd=tmp_path, env=os.environ | {"PYTHONUTF8": "1", "MPLBACKEND": "Agg"}, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    assert (run/"results/PDB_T1_decay.png").is_file() and frozen_inputs(run) == before


def test_real_historical_copies_match_saved_scientific_values(tmp_path):
    # 仅读取真实历史，所有再分析均在临时副本；未要求原件存在时不伪造历史证据。
    source = ROOT/"data/T1_Calibration/0529_1405_test"
    if not source.is_dir():
        raise AssertionError("本轮历史验收来源不存在，请提供真实 T1 历史数据")
    run = tmp_path/source.name; shutil.copytree(source, run)
    for name in ("results",):
        if (run/name).exists(): shutil.rmtree(run/name)
    before = frozen_inputs(source)
    result = analysis.analyze(run)
    with np.load(source/"raw/power_scan_results.npz") as old, np.load(run/"results/fit_points.npz") as new:
        np.testing.assert_allclose(new["T1"], old["T1"], rtol=1e-8, atol=1e-12, equal_nan=True)
        np.testing.assert_allclose(new["T1_err"], old["T1_err"], rtol=1e-6, atol=1e-12, equal_nan=True)
        valid = np.isfinite(old["T1"]) & (old["T1"] > 0)
        inv = 1/old["T1"][valid]; err = old["T1_err"][valid]/old["T1"][valid]**2
        coeff = np.polyfit(old["probe_power"][valid], inv, 1, w=1/np.maximum(err, 1e-6))
        assert result["alpha"] == pytest.approx(coeff[0], rel=1e-6)
        assert result["T1_0_inv"] == pytest.approx(coeff[1], rel=1e-8)
    assert frozen_inputs(source) == before
