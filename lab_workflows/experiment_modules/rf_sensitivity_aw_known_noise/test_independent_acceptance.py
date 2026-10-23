"""本 ID 的独立科学公式、阶段复用和历史边界验收。"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil

import numpy as np
import pytest
import yaml
from scipy.signal import welch

from . import analysis, run_format, stages, workflow
from .models import RFSensitivityKnownNoiseParams
from .test_workflow import known_rig, rig, control_source, assert_shutdown
from .waveform import generate_noise


def scientific_run(path):
    """独立构造两个不同响应斜率的控制，避免把生产算法当作数值答案。"""
    params = replace(RFSensitivityKnownNoiseParams(), control_waveform_files=[
        f"v{i}/waveforms/optimal_control_waveform.csv" for i in (1, 2)],
        rf_amp_points=41, noise_n_avg=2, noise_nperseg=256,
        linewidth_mode="dispersive_equivalent", z_v_to_nt=2.0,
        z_phase_scan_start_deg=0.0, z_phase_scan_stop_deg=270.0, z_phase_scan_step_deg=90.0)
    config = dict(experiment_id=run_format.EXPERIMENT_ID, experiment_type=run_format.DATA_TYPE,
        schema_version=4, execution_mode="typed_workflow", acquisition_method=run_format.ACQUISITION_METHOD,
        completion_status="completed", phase_calibration_status="completed",
        xy_phase_calibration={"converged": True}, parameters=params.to_external())
    (path / "raw").mkdir(parents=True)
    (path / "results").mkdir()
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    field = np.linspace(-1, 1, params.rf_amp_points)
    for index, relative in enumerate(params.control_waveform_files, 1):
        child = path / "controls" / f"v{index}"
        (child / "raw").mkdir(parents=True)
        (child / "results").mkdir()
        child_config = deepcopy(config)
        child_config["parameters"].update(ARB_WAVEFORM_FILE=relative, CONTROL_WAVEFORM_FILES=[relative])
        (child / "experiment_config.yaml").write_text(yaml.safe_dump(child_config), encoding="utf-8")
        x = index * .02 * field / (field ** 2 + .25 ** 2) + .1
        np.savez(child / "raw/response_data.npz", amplitudes_V=field / 2, amplitudes_nT=field,
            demod3_x_V=x, demod3_y_V=np.full(len(field), .03),
            demod3_r_V=np.hypot(x, .03), z_v_to_nT=2.0, demod_idx=params.demod3_idx)
        for record in range(2):
            rate = 1024.0
            samples = np.random.default_rng(100 * index + record).normal(0, index * 1e-4, 4096)
            np.savez(child / "raw" / f"noise_D3_{record:04d}.npz", noise_x_V=samples,
                actual_rate_Sa_s=rate, signal_path="sample.x", demod_idx=params.demod3_idx)
        phase = np.array([0., 90., 180., 270.])
        np.savez(child / "raw/z_phase_scan.npz", scanned_phase_deg=phase,
            x_mean_v=np.cos(np.deg2rad(phase)), y_mean_v=np.sin(np.deg2rad(phase)),
            r_mean_v=np.ones(4), demod_idx=params.demod3_idx)
    return path


def manual_phase_run(path):
    scientific_run(path)
    for file in [path / "experiment_config.yaml", *path.glob("controls/*/experiment_config.yaml")]:
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        config["parameters"].update(Z_PHASE_SCAN_ENABLED=False, Z_BURST_PHASE_INIT=181.5)
        if file.parent != path:
            config["rf_phase_calibration"] = {"mode": "manual", "scan_skipped": True,
                "points_completed": 0, "selected_phase_deg": 181.5}
            (file.parent / "raw/z_phase_scan.npz").unlink()
            with np.load(file.parent / "raw/response_data.npz") as saved:
                values = dict(saved)
            values["z_burst_phase_deg"] = (181.5 + np.where(values["amplitudes_V"] < 0, 180, 0)) % 360
            np.savez(file.parent / "raw/response_data.npz", **values)
        file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.mark.parametrize("fault", ["missing_provenance", "parent_phase", "parent_mode", "response_phase"])
def test_manual_phase_records_must_match_all_controls_and_response(tmp_path, fault):
    path = manual_phase_run(tmp_path / "manual")
    point = path / "controls/v1"
    file = point / "experiment_config.yaml"
    config = yaml.safe_load(file.read_text(encoding="utf-8"))
    if fault == "missing_provenance":
        config.pop("rf_phase_calibration")
    elif fault == "parent_phase":
        config["parameters"]["Z_BURST_PHASE_INIT"] = 182.5
        config["rf_phase_calibration"]["selected_phase_deg"] = 182.5
    elif fault == "parent_mode":
        config["parameters"]["Z_PHASE_SCAN_ENABLED"] = True
    else:
        with np.load(point / "raw/response_data.npz") as saved:
            values = dict(saved)
        values["z_burst_phase_deg"] += 1
        np.savez(point / "raw/response_data.npz", **values)
    file.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="相位"):
        analysis.AnalysisWorkflow(path).execute(["B01"])


def assert_independent_science(path, nperseg):
    """用显式 Welch、导数、单位换算及响应补偿核对每个版本。"""
    for index in (1, 2):
        child = path / "controls" / f"v{index}"
        spectra = []
        for source in sorted((child / "raw").glob("noise_D3_*.npz")):
            with np.load(source, allow_pickle=False) as raw:
                values = raw["noise_x_V"]
                frequency, spectrum = welch(values - values.mean(), fs=float(raw["actual_rate_Sa_s"]),
                    nperseg=nperseg, noverlap=nperseg // 2, scaling="density")
                spectra.append(spectrum)
        expected_psd = np.mean(spectra, axis=0)
        expected_slope = index * .02 / .25 ** 2 / 1_000_000
        with np.load(child / "results/response_fit.npz", allow_pickle=False) as fitted:
            assert float(fitted["slope_V_per_fT"]) == pytest.approx(expected_slope, rel=1e-6)
        with np.load(child / "results/sensitivity.npz", allow_pickle=False) as result:
            np.testing.assert_allclose(result["psd_avg_V2_per_Hz"], expected_psd, rtol=1e-12)
            np.testing.assert_array_equal(result["freq_Hz"], frequency)
            np.testing.assert_allclose(result["sensitivity_raw_fT_per_sqrtHz"],
                np.sqrt(expected_psd) / expected_slope, rtol=1e-6)
            hwhm = .25 * (9.2740100783e-24 / (4 * np.pi * 1.054571817e-34) * 1e-9)
            np.testing.assert_allclose(result["correction_factor"], np.sqrt(1 + (frequency / hwhm) ** 2), rtol=1e-6)
            np.testing.assert_allclose(result["sensitivity_fT_per_sqrtHz"],
                np.sqrt(expected_psd) / expected_slope * np.sqrt(1 + (frequency / hwhm) ** 2), rtol=1e-6)


def test_complete_and_separate_analysis_match_independent_formulas(tmp_path):
    full = scientific_run(tmp_path / "full")
    staged = tmp_path / "staged"
    shutil.copytree(full, staged)
    assert analysis.analyze(full)["status"] == "completed"
    ctx = analysis.AnalysisWorkflow(staged)
    with pytest.raises(ValueError, match="前置"):
        ctx.execute(["B03"])
    for item in ("B01", "B02", "B03", "B04"):
        ctx.execute([item], expected_revision=ctx.revision)
    for directory in (full, staged):
        assert_independent_science(directory, 256)
        for name in ("full_analysis.png", "full_analysis.pdf", "z_phase_response_comparison.png", "z_phase_response_comparison.pdf"):
            assert (directory / "results" / name).stat().st_size > 1000
    with np.load(full / "results/sensitivity.npz") as first, np.load(staged / "results/sensitivity.npz") as second:
        assert first.files == second.files
        for key in first.files:
            np.testing.assert_equal(first[key], second[key])


def test_analysis_input_and_child_artifact_drift_and_archive(tmp_path):
    directory = scientific_run(tmp_path / "drift")
    ctx = analysis.AnalysisWorkflow(directory)
    ctx.execute(["B01", "B02", "B03"])
    fit_path = directory / "controls/v1/results/response_fit.npz"
    fit_before = fit_path.read_bytes()
    original_config = (directory / "experiment_config.yaml").read_bytes()
    changed = analysis.AnalysisWorkflow(directory, {"NOISE_NPERSEG": 128})
    assert changed.states["B02"]["status"] == "completed"
    assert changed.states["B03"]["status"] == "stale"
    changed.execute(["B03"])
    assert fit_path.read_bytes() == fit_before
    assert list((directory / "controls/v1/results").glob("before_*/psd_avg.npz"))
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"] == yaml.safe_load(original_config)["parameters"]
    artifact = directory / "controls/v2/results/psd_avg.npz"
    artifact.write_bytes(artifact.read_bytes() + b"drift")
    assert analysis.AnalysisWorkflow(directory).states["B03"]["status"] == "stale"
    source = directory / "controls/v1/raw/noise_D3_0000.npz"
    with np.load(source) as data:
        replacement = dict(data)
    replacement["noise_x_V"] = replacement["noise_x_V"] * 2
    np.savez(source, **replacement)
    drifted = analysis.AnalysisWorkflow(directory)
    assert drifted.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="前置"):
        drifted.execute(["B03"])


@pytest.mark.parametrize("fault", ["missing_response", "wrong_identity", "phase_failed", "noise_mismatch", "missing_phase"])
def test_bad_control_input_blocks_b01_without_successor_outputs(tmp_path, fault):
    directory = scientific_run(tmp_path / "bad")
    child = directory / "controls/v2"
    if fault == "missing_response":
        (child / "raw/response_data.npz").unlink()
    elif fault == "missing_phase":
        (child / "raw/z_phase_scan.npz").unlink()
    else:
        path = child / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if fault == "wrong_identity":
            config["experiment_id"] = "rf-sensitivity-aw"
        elif fault == "phase_failed":
            config["phase_calibration_status"] = "failed"
        else:
            config["parameters"]["NOISE_SEED"] += 1
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
    ctx = None
    with pytest.raises((OSError, ValueError)):
        ctx = analysis.AnalysisWorkflow(directory)
        ctx.execute(["B01", "B02", "B03", "B04"])
    if ctx is not None:
        assert ctx.states["B01"]["status"] in ("failed", "quality_failed")
    assert not list(directory.rglob("response_fit.npz"))


def test_real_schema_all_fields_and_parameter_impacts_are_executable(known_rig):
    fields = known_rig.params.schema()["fields"]
    fixture = json.loads((Path(__file__).resolve().parents[3] / "GUI/frontend/src/pages/rfKnownNoiseAcceptance.fixture.json").read_text(encoding="utf-8"))["fields"]
    assert [(x["name"], x["label"], x["type"], x["group"]) for x in fields] == [
        (x["name"], x["label"], x["type"], x["group"]) for x in fixture]
    flow = stages.definition()
    workflow_fixture = Path(__file__).resolve().parents[3] / "GUI/frontend/src/pages/rfKnownNoiseWorkflow.fixture.json"
    assert json.loads(workflow_fixture.read_text(encoding="utf-8")) == json.loads(json.dumps(flow))
    shown = {key for group in ("acquisition", "analysis") for stage in flow[group] for key in stage["display_parameters"]}
    assert shown == {field["name"] for field in fields}
    for parameters in ({"NOISE_BAND_PSDS_V2_PER_HZ": [2e-11]}, {"NOISE_N_AVG": 3}, {"RF_AMP_POINTS": 11}):
        ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
        for record in ctx.states.values():
            record["status"] = "completed"
        ctx.update_parameters(parameters)
        expected = {item for key in parameters for item in flow["parameter_impacts"]["acquisition"][key]}
        assert {key for key, value in ctx.states.items() if value["status"] == "stale"} == expected
    assert not known_rig.events
    empty = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    empty.execute(["A16"])
    assert empty.closed and empty.states["A16"]["status"] == "completed"
    assert not known_rig.events


def test_noise_spectrum_matches_independent_fft():
    noise = generate_noise(RFSensitivityKnownNoiseParams())
    voltage = noise["noise_V"]
    spectrum = np.fft.rfft(voltage)
    frequencies = np.fft.rfftfreq(len(voltage), 1 / noise["sample_rate_Sa_s"])
    psd = 2 * abs(spectrum) ** 2 / (noise["sample_rate_Sa_s"] * len(voltage))
    for frequency, expected in zip(noise["frequency_Hz"], noise["actual_psd_V2_per_Hz"]):
        index = np.argmin(abs(frequencies - frequency))
        assert frequencies[index] == pytest.approx(frequency)
        assert psd[index] == pytest.approx(expected, rel=1e-10)


def test_complete_and_staged_acquisition_share_data_and_finish_ignores_draft(known_rig):
    full = workflow.run(known_rig.params, project_root=known_rig.tmp_path)
    ctx = workflow.Workflow(known_rig.params, project_root=known_rig.tmp_path)
    try:
        ctx.execute([f"A{i:02d}" for i in range(1, 13)])
        ctx.execute(["A13"])
        ctx.execute(["A14"])
        baseline = ctx.response_config
        before_events = len(known_rig.events)
        ctx.update_parameters({"DEMOD3_IDX": 2, "DEMOD3_RATE": known_rig.params.demod3_rate * 2})
        assert len(known_rig.events) == before_events
        assert ctx.response_config is baseline
        for version in ("v1", "v2"):
            for name in ("response_data.npz", "noise_D3_0000.npz", "noise_D3_0001.npz"):
                with np.load(full / "controls" / version / "raw" / name) as first, np.load(ctx.directory.root / "controls" / version / "raw" / name) as second:
                    for key in first.files:
                        np.testing.assert_equal(first[key], second[key])
        snapshot = (ctx.directory.root / "experiment_config.yaml").read_bytes()
        ctx.finish()
        assert yaml.safe_load(snapshot)["parameters"] == yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_bytes())["parameters"]
        assert ctx.devices["hf2"].get_double(ctx.devices["hf2"].demod_path(baseline.demod_index) + "/rate") == pytest.approx(known_rig.params.demod3_rate * .9)
    finally:
        ctx.finish()
    assert_shutdown(known_rig)
