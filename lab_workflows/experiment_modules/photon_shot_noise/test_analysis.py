"""离线科学一致性、历史资格、指纹、产物保护及真实显式目录入口。"""
from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from scipy import signal
from scipy.optimize import curve_fit
from . import analysis, analysis_steps as steps, run_format, stages
from .models import PhotonShotNoiseParams
from .storage import save_npz

REGRESSION_CASES = {
    "photon-shot-noise": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/photon_shot_noise/analysis.py",
                "lab_workflows/experiment_modules/photon_shot_noise/analysis_steps.py",
                "lab_workflows/experiment_modules/photon_shot_noise/analysis_plotting.py",
                "lab_workflows/experiment_modules/photon_shot_noise/run_format.py",
            ],
            "tests": [
                "test_original_scientific_arrays_and_fit_unchanged",
                "test_full_and_stepwise_equal_and_recompute_archives_only_successors",
                "test_missing_dependencies_and_revision_rejected_readonly",
                "test_bad_inputs_rejected_before_moving_previous_science",
                "test_content_fingerprints_raw_and_artifact_even_same_size_mtime",
                "test_quality_failure_retains_diagnostics_and_blocks_plot",
                "test_nonfinite_records_stay_masked_not_interpolated",
                "test_parameter_and_raw_versions_independent",
                "test_unknown_format_running_and_path_escape_rejected",
                "test_options_isolated_and_impacts_match_manifest",
                "test_real_cli_requires_directory_and_writes_only_explicit_run",
            ],
        },
    },
}

ROOT = Path(__file__).resolve().parents[3]


def synthetic_run(path, *, legacy=False, schema=2, raw_version=1):
    path = Path(path)
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    p = replace(PhotonShotNoiseParams(), probe_power_points=5, acq_repeats=4,
        acq_duration=0.25, hf2_demod_rate=1024, psd_nperseg=64)
    powers = np.linspace(p.probe_power_start, p.probe_power_stop, p.probe_power_points)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, experiment_type=run_format.DATA_TYPE,
        data_type=run_format.DATA_TYPE, schema_version=schema, raw_format_version=raw_version,
        completion_status="completed", parameters=p.to_external())
    if legacy:
        config = dict(experiment_type=run_format.DATA_TYPE, purpose="shot_noise_calibration",
                      timestamp="0527_1750")
        for section, keys in run_format.LEGACY_SECTIONS.items():
            config[section] = {old:p.to_external()[external] for old, external in keys.items()}
        config["scan_params"]["variable"] = "Probe_laser_power"
    elif raw_version is None:
        config.pop("raw_format_version")
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    np.savez(path / "raw/scan_meta.npz", probe_powers=powers, actual_rate=1024,
        acq_duration=p.acq_duration, acq_repeats=p.acq_repeats)
    for index, power in enumerate(powers):
        rng = np.random.default_rng(index+372)
        x = rng.normal(size=(p.acq_repeats, 256)) * np.sqrt(1e-6+power*1e-4)
        y = rng.normal(size=x.shape) * np.sqrt(2e-6+power*2e-4)
        values = dict(probe_power=power, probe_power_index=index, x_waveforms=x, y_waveforms=y, actual_rate=1024)
        if not legacy:
            values.update(actual_rates_Sa_s=np.full(p.acq_repeats, 1024.0),
                time_s=np.tile(np.arange(256)/1024, (p.acq_repeats, 1)))
        np.savez(path / f"raw/waveforms_P{index:03d}.npz", **values)
    return path


def replace_npz(path, **changes):
    with np.load(path, allow_pickle=False) as raw:
        values = {key:raw[key] for key in raw.files}
    values.update(changes)
    np.savez(path, **values)


def frozen_inputs(run):
    return {path:path.read_bytes() for path in [run / "experiment_config.yaml", *sorted((run / "raw").glob("*"))]}


def legacy_calculation(run):
    """逐式保留迁移前算法作独立回归基准，不使用新分析函数计算期望值。"""
    config, p = steps.load_config(run)
    with np.load(run / "raw/scan_meta.npz") as meta:
        powers, rate = meta["probe_powers"], float(meta["actual_rate"])
    asds, psds = {key:[] for key in ("x","y")}, {key:[] for key in ("x","y")}
    rms = []
    for i in range(len(powers)):
        with np.load(run / f"raw/waveforms_P{i:03d}.npz") as wave:
            rms.append([np.std(row) for row in wave["x_waveforms"]])
            for key in ("x","y"):
                record_asd, record_psd = [], []
                for row in wave[key+"_waveforms"]:
                    freq, psd = signal.welch(row, fs=rate, nperseg=min(p.psd_nperseg,len(row)//2),
                        scaling="density", detrend="constant")
                    band = (freq > 0) & (freq <= rate*0.05)
                    record_asd.append(np.median(np.sqrt(np.maximum(psd[band],0))))
                    record_psd.append(psd)
                asds[key].append(record_asd)
                psds[key].append(np.mean(record_psd,axis=0))
    result, fit = dict(frequencies=freq, all_rms=np.asarray(rms)), {}
    for key in ("x","y"):
        values = np.asarray(asds[key])
        mean, sd = np.nanmean(values,axis=1), np.nanstd(values,axis=1)
        result.update({f"all_asd_{key}":values, f"mean_asd_{key}":mean,
            f"std_asd_{key}":sd, f"psd_{key}m":np.asarray(psds[key])})
        best, cov = curve_fit(lambda x,a,b:a*x+b, powers[2:], mean[2:]**2,
            sigma=2*mean[2:]*sd[2:], absolute_sigma=True)
        fit.update({f"alpha_{key}":best[0], f"beta_{key}":best[1],
                    f"alpha_{key}_err":np.sqrt(cov[0,0]), f"beta_{key}_err":np.sqrt(cov[1,1])})
    return result, fit


@pytest.mark.parametrize("legacy", [False, True])
def test_original_scientific_arrays_and_fit_unchanged(tmp_path, legacy):
    run = synthetic_run(tmp_path / "run", legacy=legacy)
    before = frozen_inputs(run)
    arrays, fits = legacy_calculation(run)
    result = analysis.analyze(run)
    actual = steps.statistics(run)
    for key, value in arrays.items():
        np.testing.assert_allclose(actual[key], value, rtol=1e-12, atol=1e-18, equal_nan=True)
    for key, value in fits.items():
        np.testing.assert_allclose(result["fit"][key], value, rtol=1e-12, atol=1e-18)
    assert frozen_inputs(run) == before
    for name in stages.PLOTS:
        assert (run / f"results/{name}.png").stat().st_size > 100
        assert (run / f"results/{name}.pdf").stat().st_size > 100
    assert yaml.safe_load((run / "results/analysis.yaml").read_text())["method"] == "Welch PSD->ASD flat median->ASD^2 fit"


def test_full_and_stepwise_equal_and_recompute_archives_only_successors(tmp_path):
    one, two = synthetic_run(tmp_path / "full"), synthetic_run(tmp_path / "steps")
    expected = analysis.analyze(one)
    for sid in ("B01", "B02", "B03"):
        ctx = analysis.AnalysisWorkflow(two)
        actual = ctx.execute([sid], ctx.revision)
    assert actual["fit"] == expected["fit"]
    cache = (two / "results/noise_statistics.npz").read_bytes()
    ctx = analysis.AnalysisWorkflow(two, {"FIT_START_IDX": 1})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    ctx.execute(["B02", "B03"])
    assert (two / "results/noise_statistics.npz").read_bytes() == cache
    archives = list((two / "results").glob("before_*"))
    assert len(archives) == 1 and not (archives[0] / "noise_statistics.npz").exists()
    assert (archives[0] / "analysis.yaml").exists()


def test_missing_dependencies_and_revision_rejected_readonly(tmp_path):
    run = synthetic_run(tmp_path / "run")
    ctx = analysis.AnalysisWorkflow(run)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B01"], expected_revision=5)
    assert not (run / "analysis_state.json").exists()


@pytest.mark.parametrize("what", ["missing", "rate", "shape", "index", "time", "identity"])
def test_bad_inputs_rejected_before_moving_previous_science(tmp_path, what):
    run = synthetic_run(tmp_path / "run")
    analysis.analyze(run)
    old = {p:p.read_bytes() for p in (run / "results").glob("*") if p.is_file()}
    wave = run / "raw/waveforms_P001.npz"
    if what == "missing":
        wave.unlink()
    elif what == "rate":
        replace_npz(wave, actual_rates_Sa_s=np.full(4, 512))
    elif what == "shape":
        replace_npz(wave, y_waveforms=np.zeros((4,255)))
    elif what == "index":
        replace_npz(wave, probe_power_index=3)
    elif what == "time":
        replace_npz(wave, time_s=np.zeros((4,256)))
    else:
        config = yaml.safe_load((run / "experiment_config.yaml").read_text())
        config["experiment_id"] = "wrong"
        (run / "experiment_config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises((ValueError, FileNotFoundError)):
        analysis.analyze(run)
    assert {p:p.read_bytes() for p in old} == old
    assert not list((run / "results").glob("before_*"))


def test_content_fingerprints_raw_and_artifact_even_same_size_mtime(tmp_path):
    run = synthetic_run(tmp_path / "run")
    analysis.analyze(run)
    wave = run / "raw/waveforms_P001.npz"
    before = wave.stat()
    with np.load(wave) as values:
        x = values["x_waveforms"] * 1.01
    replace_npz(wave, x_waveforms=x)
    os.utime(wave, ns=(before.st_atime_ns,before.st_mtime_ns))
    assert wave.stat().st_size == before.st_size
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    ctx.execute(["B01","B02","B03"])
    cache = run / "results/noise_statistics.npz"
    before = cache.stat()
    with np.load(cache) as values:
        mean = values["mean_asd_x"] * 1.01
    replace_npz(cache, mean_asd_x=mean)
    os.utime(cache, ns=(before.st_atime_ns,before.st_mtime_ns))
    assert cache.stat().st_size == before.st_size
    assert analysis.AnalysisWorkflow(run).states["B01"]["status"] == "stale"


def test_quality_failure_retains_diagnostics_and_blocks_plot(tmp_path):
    run = synthetic_run(tmp_path / "run")
    for path in (run / "raw").glob("waveforms_P*.npz"):
        replace_npz(path, x_waveforms=np.zeros((4,256)), y_waveforms=np.zeros((4,256)))
    with pytest.raises(ValueError, match="三个"):
        analysis.analyze(run)
    ctx = analysis.AnalysisWorkflow(run)
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert json.loads((run / "results/fit_summary.json").read_text(encoding="utf-8"))["status"] == "quality_failed"
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    assert not list((run / "results").glob("*.png"))


def test_nonfinite_records_stay_masked_not_interpolated(tmp_path):
    run = synthetic_run(tmp_path / "run")
    replace_npz(run / "raw/waveforms_P000.npz", x_waveforms=np.full((4,256),np.nan))
    path = run / "raw/waveforms_P001.npz"
    with np.load(path) as values:
        y = values["y_waveforms"].copy()
    y[0,0] = np.inf
    replace_npz(path, y_waveforms=y)
    analysis.analyze(run)
    data = steps.statistics(run)
    assert np.isnan(data["mean_asd_x"][0]) and not data["valid_records"][0].any()
    assert not data["valid_records"][1,0] and np.isnan(data["all_asd_y"][1,0])


@pytest.mark.parametrize("schema,raw", [(1,None),(2,None),(1,1),(2,1)])
def test_parameter_and_raw_versions_independent(tmp_path, schema, raw):
    run = synthetic_run(tmp_path / "run", schema=schema, raw_version=raw)
    assert not run_format.analysis_block_reason(yaml.safe_load((run / "experiment_config.yaml").read_text()))
    analysis.AnalysisWorkflow(run).execute(["B01"])


def test_unknown_format_running_and_path_escape_rejected(tmp_path):
    run = synthetic_run(tmp_path / "run", raw_version=99)
    with pytest.raises(ValueError, match="原始数据"):
        analysis.AnalysisWorkflow(run)
    config = yaml.safe_load((run / "experiment_config.yaml").read_text())
    config.update(raw_format_version=1, completion_status="running")
    assert "仍在执行" in run_format.analysis_block_reason(config)
    with pytest.raises(ValueError, match="越出"):
        steps.output_path(run, "../escape")


def test_options_isolated_and_impacts_match_manifest(tmp_path):
    one, two = synthetic_run(tmp_path / "one"), synthetic_run(tmp_path / "two")
    analysis.AnalysisWorkflow(one).execute(["B01","B02"])
    ctx = analysis.AnalysisWorkflow(one, {"FIT_START_IDX": 1, "PSD_NPERSEG": 32})
    affected = set(stages.definition()["parameter_impacts"]["analysis"]["PSD_NPERSEG"])
    assert {key for key,v in ctx.states.items() if v["status"] == "stale"} == affected - {"B03"}
    assert ctx.options["FIT_START_IDX"] == 1
    assert analysis.AnalysisWorkflow(two).options["FIT_START_IDX"] == 2
    with pytest.raises(ValueError, match="不支持"):
        analysis.AnalysisWorkflow(one, {"ACQ_DURATION": 5})
    for value in (0, float("nan"), True):
        with pytest.raises((TypeError,ValueError)):
            analysis.AnalysisWorkflow(one, {"PSD_NPERSEG":value})


def test_real_cli_requires_directory_and_writes_only_explicit_run(tmp_path):
    run, other = synthetic_run(tmp_path / "one"), synthetic_run(tmp_path / "two")
    before = frozen_inputs(run)
    script = ROOT / "experiments/Photon_shot_noise_plot.py"
    env = dict(os.environ, MPLBACKEND="Agg")
    result = subprocess.run([sys.executable,"-X","utf8",str(script),str(run)], cwd=ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    assert (run / "results/shot_noise_x.png").is_file()
    assert not (other / "analysis_state.json").exists() and frozen_inputs(run) == before
    result = subprocess.run([sys.executable,"-X","utf8",str(script)], cwd=ROOT,
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 2 and "run_dir" in result.stderr
