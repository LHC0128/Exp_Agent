"""旧数据、原算法数值、完整/分步一致与真实薄入口的离线回归。"""
from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps as steps, run_format, stages, fitting
from .test_workflow import small_params, signal, ROOT

REGRESSION_CASES = {
    "bb-duty-scan-burst": {"analysis": {
        "sources": [
            "lab_workflows/experiment_modules/bb_duty_scan_burst/analysis.py",
            "lab_workflows/experiment_modules/bb_duty_scan_burst/analysis_steps.py",
            "lab_workflows/experiment_modules/bb_duty_scan_burst/fitting.py",
            "lab_workflows/experiment_modules/bb_duty_scan_burst/analysis_plotting.py",
            "lab_workflows/experiment_modules/bb_duty_scan_burst/run_format.py",
            "experiments/BB_Rpump_DutyScan_Burst_plot.py",
        ],
        "tests": ["test_original_numeric_golden_and_error_caps", "test_complete_steps_archives_and_historical_raw_readonly",
                  "test_missing_corrupt_identity_running_and_expired_inputs", "test_quality_failure_saves_diagnostics_and_blocks_plot",
                  "test_plot_thin_entrypoint_and_adapter_real_subprocess", "test_numpy_yaml_legacy_is_narrow_safe_and_readonly"],
    }},
}


def synthetic_run(path, *, legacy=False, bad=False):
    path = Path(path)
    (path / "raw").mkdir(parents=True)
    p = small_params()
    config = run_format.initial_metadata(p) | dict(timestamp="0527_1200", run_tag="synthetic", completion_status="completed")
    if legacy:
        for key in ("raw_format_version", "experiment_id", "data_type", "execution_mode", "completion_status"):
            config.pop(key, None)
    else:
        config.update(schema_version=p.schema_version, parameters=p.to_external())
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    files = []
    for i, duty in enumerate(p.duty_cycle_list):
        time_s, y = signal(p, duty)
        if bad: y[:] = 0
        name = f"shot_d{i:02d}_avg.npz"
        np.savez(path / "raw" / name, time_s=time_s, signal=y, duty_cycle=duty,
                 t_burst_start_s=0., t_burst_end_s=p.burst_duration_s)
        files.append(name)
    np.savez(path / "raw/duty_scan_summary.npz", duty_cycle=p.duty_cycle_list, files=files, n_shots=len(files))
    return path


def legacy_numpy_run(path):
    run = synthetic_run(path, legacy=True)
    config_path = run / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["burst_pump"]["duty_cycle_list"] = np.array(config["burst_pump"]["duty_cycle_list"])
    config_path.write_text(yaml.dump(config, allow_unicode=True), encoding="utf-8")
    return run


def test_numpy_yaml_legacy_is_narrow_safe_and_readonly(tmp_path, monkeypatch):
    run = legacy_numpy_run(tmp_path / "numpy")
    before = frozen_inputs(run)
    config_path = run / "experiment_config.yaml"
    config = run_format.read_config(config_path)
    assert config["burst_pump"]["duty_cycle_list"] == small_params().duty_cycle_list
    assert run_format.format_block_reason(config) == ""
    ctx = analysis.AnalysisWorkflow(run)
    ctx.execute(["B01", "B02"])
    assert frozen_inputs(run) == before
    probe = tmp_path / "malicious.yaml"
    monkeypatch.setattr(os, "system", lambda *args: pytest.fail("YAML 不得执行系统命令"))
    probe.write_text("!!python/object/apply:os.system [echo forbidden]", encoding="utf-8")
    assert run_format.read_config(probe) is None
    payload = config_path.read_text(encoding="utf-8")
    probe.write_text(payload.replace("- f8", "- O8"), encoding="utf-8")
    assert run_format.read_config(probe) is None
    probe.write_text(payload.replace("python/name:numpy.ndarray", "python/name:os.system"), encoding="utf-8")
    assert run_format.read_config(probe) is None


def frozen_inputs(run):
    return {p.relative_to(run).as_posix(): p.read_bytes() for p in [run / "experiment_config.yaml", *run.glob("raw/*")]}


def test_original_numeric_golden_and_error_caps(tmp_path):
    run = synthetic_run(tmp_path / "numeric")
    p = small_params()
    # 此数值在迁移前由原脚本的真实 fit_one_shot 产生；迁移时还逐键比较过全部拟合曲线/残差。
    expected = [
        [0.00047171058758216844, 0.0011999984348000923, 1286.6095163742038, 0.999999930791319, 0.999999965456759],
        [0.00038462814708464257, 0.0011999984358899572, 1766.579309048232, 0.9999999213582408, 0.9999999655037879],
        [0.0003125172206809563, 0.0011999984361281957, 2366.489250587048, 0.9999999031346232, 0.9999999655140619],
    ]
    results = [fitting.fit_one_shot(run / f"raw/shot_d{i:02d}_avg.npz", duty, post_dark_time_s=p.post_dark_time_s) for i, duty in enumerate(p.duty_cycle_list)]
    values = [[r[key] for key in ("tau_BB_s", "tau_dark_s", "R_pump_eff_per_s", "r2_build", "r2_decay")] for r in results]
    np.testing.assert_allclose(values, expected, rtol=2e-6, atol=1e-9)
    stats = steps.statistics(results)
    np.testing.assert_allclose(stats["R_eff"], stats["GBB"] - stats["GD"])
    np.testing.assert_allclose(stats["coeff"], np.polyfit(stats["duty_pct"], stats["R_eff"], 1, w=1 / np.maximum(stats["e_R"], 1)))
    capped = [dict(r, tau_BB_err_s=100., tau_dark_err_s=100.) for r in results]
    cap = steps.statistics(capped)
    np.testing.assert_array_equal(cap["e_GBB"], np.abs(cap["GBB"]))
    np.testing.assert_array_equal(cap["e_GD"], np.abs(cap["GD"]))
    assert not stats["suspect"].any()


def test_complete_steps_archives_and_historical_raw_readonly(tmp_path):
    complete, staged = synthetic_run(tmp_path / "complete"), synthetic_run(tmp_path / "staged", legacy=True)
    before = frozen_inputs(staged)
    full = analysis.analyze(complete)
    ctx = analysis.AnalysisWorkflow(staged)
    for item in ("B01", "B02", "B03"):
        ctx.execute([item], ctx.revision)
    assert full["quality_passed"] and ctx.snapshot()["summary"]["status"] == "completed"
    report_a = json.loads((complete / "results/analysis_pub.json").read_text(encoding="utf-8"))
    report_b = json.loads((staged / "results/analysis_pub.json").read_text(encoding="utf-8"))
    assert report_a["per_point"] == report_b["per_point"]
    for name in stages.PLOTS:
        for suffix in (".png", ".pdf"):
            assert (staged / "results" / (name + suffix)).stat().st_size > 1000
    inputs = (staged / "results/inputs.json").read_bytes()
    ctx.execute(["B03"])
    assert (staged / "results/inputs.json").read_bytes() == inputs
    assert not list((staged / "results").glob("before_*/inputs.json"))
    assert list((staged / "results").glob("before_*/*.png"))
    options = analysis.AnalysisWorkflow(staged, {"POST_DARK_TIME_S": 0.002})
    assert options.states["B01"]["status"] == "completed" and options.states["B02"]["status"] == "stale"
    assert analysis.AnalysisWorkflow(complete).options["POST_DARK_TIME_S"] == 0.003
    options.execute(["B02", "B03"])
    assert frozen_inputs(staged) == before


def test_missing_corrupt_identity_running_and_expired_inputs(tmp_path):
    run = synthetic_run(tmp_path / "invalid")
    ctx = analysis.AnalysisWorkflow(run)
    with pytest.raises(ValueError, match="前置"): ctx.execute(["B02"])
    ctx.execute(["B01", "B02"])
    before = (run / "results/analysis_pub.json").read_bytes()
    path = run / "raw/shot_d00_avg.npz"
    path.unlink()
    stale = analysis.AnalysisWorkflow(run)
    assert stale.states["B01"]["status"] == "stale"
    with pytest.raises((ValueError, OSError)): stale.execute(["B01", "B02"])
    assert (run / "results/analysis_pub.json").read_bytes() == before
    config_path = run / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for change in ({"experiment_id": "bb-power-scan-burst"}, {"raw_format_version": 99}, {"completion_status": "running"}):
        config_path.write_text(yaml.safe_dump(config | change), encoding="utf-8")
        with pytest.raises(ValueError): analysis.AnalysisWorkflow(run)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError): analysis.AnalysisWorkflow(run, {"POST_DARK_TIME_S": float("nan")})
    with pytest.raises(ValueError): analysis.AnalysisWorkflow(run, {"unknown": 1})
    for version in (1, 2):
        assert run_format.format_block_reason(config | {"schema_version": version}) == ""
    invalid = synthetic_run(tmp_path / "bad_path")
    np.savez(invalid / "raw/duty_scan_summary.npz", duty_cycle=[0.02], files=["../escape.npz"], n_shots=1)
    with pytest.raises(ValueError, match="索引"): analysis.analyze(invalid)


def test_quality_failure_saves_diagnostics_and_blocks_plot(tmp_path):
    run = synthetic_run(tmp_path / "quality", bad=True)
    before = frozen_inputs(run)
    ctx = analysis.AnalysisWorkflow(run)
    with pytest.raises(steps.AnalysisQualityError): ctx.execute(["B01", "B02", "B03"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert ctx.states["B03"]["status"] != "completed"
    report = json.loads((run / "results/analysis_pub.json").read_text(encoding="utf-8"))
    assert not report["quality_passed"] and report["quality_errors"]
    assert all(p["suspect"] and p["R_pump_eff_per_s"] is None for p in report["per_point"])
    assert (run / "results/fits.npz").is_file() and frozen_inputs(run) == before
    with pytest.raises(ValueError, match="前置"): ctx.execute(["B03"])


def test_plot_thin_entrypoint_and_adapter_real_subprocess(tmp_path):
    run = synthetic_run(tmp_path / "entry", legacy=True)
    before = frozen_inputs(run)
    env = dict(os.environ, PYTHONUTF8="1", MPLBACKEND="Agg")
    result = subprocess.run([sys.executable, str(ROOT / "experiments/BB_Rpump_DutyScan_Burst_plot.py"), str(run)],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stderr
    assert (run / "results/rpump_eff_vs_duty_cycle_pub.png").is_file()
    from .definition import ADAPTER
    ADAPTER.analyze(run)
    assert json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))["summary"]["status"] == "completed"
    assert frozen_inputs(run) == before
