"""合成 R 与不相关的 X，验证中心标定、PSD、回退和只读历史对比。"""
import hashlib
import json
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest
import yaml
from scipy.signal import welch
from sensitivity_analysis.fitting import LARMOR_PER_NT
from ..mx_y_rf_sensitivity.analysis_core import absolute_dispersive_response
from ..rf_sensitivity_direct_aw.test_analysis import synthetic_run as shared_synthetic_run
from ..rf_sensitivity_direct_aw.analysis_steps import fit_response_curve
from .analysis import analyze, AnalysisWorkflow
from .comparison import compare_runs
from .definition import ADAPTER, DEFINITION
from .models import RFSensitivityDirectAWRParams
from .run_format import EXPERIMENT_ID, DATA_TYPE


def synthetic_run(directory, *, demod_idx=3):
    config, slope = shared_synthetic_run(directory, demod_idx=demod_idx)
    values = {key: value for key, value in config["parameters"].items() if key in RFSensitivityDirectAWRParams.external_names()}
    config.update(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE, acquisition_method="direct_aw_r",
        response_component="R", noise_component="X",
        parameters=RFSensitivityDirectAWRParams.from_external(values | {
            "Z_RF_FREQ": 3020.0, "Z_RF_NOISE_FREQ": 3000.0}).to_external(),
        stage_frequencies={stage: {"rf_frequency_Hz": frequency, "demod3_frequency_Hz": frequency}
                          for stage, frequency in (("response", 3020.0), ("calibration", 3000.0), ("noise", 3000.0))})
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    path = directory / "raw/response_data.npz"
    with np.load(path) as saved:
        data = dict(saved)
    data.update(demod3_r_V=absolute_dispersive_response(data["amplitudes_nT"], 8, 80, 0, 0.01),
                demod3_x_V=np.full(41, 0.5), demod3_y_V=np.full(41, -0.3), z_rf_freq_Hz=3020.0)
    np.savez(path, **data)
    return config, slope


def test_non_three_rf_index_and_recorded_chain_survive_r_analysis(tmp_path):
    config, slope = synthetic_run(tmp_path, demod_idx=1)
    config["signal_chain"] = "Demod0 Y -> AuxOut1 -> Aux Input1 -> RF demodulator (index 1) R calibration / X noise"
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result = analyze(tmp_path)
    assert result["rf_demod_index"] == 1
    assert result["signal_chain"] == config["signal_chain"]
    assert result["response_fit"]["slope_V_per_nT"] == pytest.approx(slope, rel=1e-5)


def test_absolute_fit_parameters_and_underlying_center_slope():
    field = np.linspace(-50, 50, 201)
    response = absolute_dispersive_response(field, 0.5, 10, 2, 0.001)
    fit = fit_response_curve(field, response, component="R")
    assert fit["success"] and fit["method"] == "absolute_dispersive_r"
    assert fit["popt"] == pytest.approx([0.5, 10, 2, 0.001], rel=1e-5)
    assert fit["slope_V_per_nT"] == pytest.approx(0.005, rel=1e-5)


def test_offline_psd_raw_corrected_sensitivity_and_staged_cache(tmp_path, monkeypatch):
    _, slope = synthetic_run(tmp_path)
    monkeypatch.setattr("lab_workflows.experiment_modules.rf_sensitivity_direct_aw.preparation.connect_devices",
        lambda *args: (_ for _ in ()).throw(AssertionError("离线分析不得连接硬件")))
    ctx = AnalysisWorkflow(tmp_path)
    ctx.execute(["B01", "B02"])
    fit_bytes = (tmp_path / "results/response_fit.npz").read_bytes()
    ctx = AnalysisWorkflow(tmp_path)
    result = ctx.execute(["B03", "B04"])
    assert (tmp_path / "results/response_fit.npz").read_bytes() == fit_bytes
    assert result["response_component"] == "R" and result["noise_component"] == "X"
    assert result["response_fit"]["slope_V_per_nT"] == pytest.approx(slope, rel=1e-5)
    assert not result["response_fit"]["slope_estimated"]
    assert not result["calibration_assumption"]["gain_equivalence_verified"]
    spectra, frequency = [], None
    for index in range(2):
        with np.load(tmp_path / f"raw/noise_D3_{index:04d}.npz") as saved:
            values = saved["noise_x_V"]
            axis, psd = welch(values - values.mean(), fs=float(saved["actual_rate_Sa_s"]), nperseg=400, noverlap=200)
        if frequency is None:
            frequency = axis
        else:
            psd = np.interp(frequency, axis, psd)
        spectra.append(psd)
    average = np.mean(spectra, axis=0)
    raw = np.sqrt(average) / (slope / 1e6)
    correction = np.sqrt(1 + (frequency / (80 * LARMOR_PER_NT))**2)
    with np.load(tmp_path / "results/sensitivity.npz") as saved:
        assert np.allclose(saved["psd_avg_V2_per_Hz"], average)
        assert np.allclose(saved["sensitivity_raw_fT_per_sqrtHz"], raw, rtol=1e-5)
        assert np.allclose(saved["sensitivity_corrected_fT_per_sqrtHz"], raw * correction, rtol=1e-5)
    for name in ("response_curve", "noise_psd", "sensitivity", "full_analysis"):
        for extension in ("png", "pdf"):
            assert (tmp_path / f"results/{name}.{extension}").stat().st_size > 100


def test_numeric_fallback_disables_linewidth_compensation(tmp_path):
    config, _ = synthetic_run(tmp_path)
    config["parameters"]["RF_AMP_POINTS"] = 3
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    field = np.array([-2.0, 0, 2])
    np.savez(tmp_path / "raw/response_data.npz", amplitudes_nT=field, amplitudes_V=field / 1758.5,
             demod3_r_V=[1.0, 0, 1.0], demod3_x_V=[100, 100, 100], demod3_y_V=[0, 0, 0], z_v_to_nT=1758.5)
    result = analyze(tmp_path)
    assert result["response_fit"]["method"] == "numeric_gradient"
    assert result["response_fit"]["slope_V_per_nT"] == 0.5
    assert result["response_fit"]["slope_estimated"]
    assert not result["frequency_response_correction"]["applied"]
    with np.load(tmp_path / "results/sensitivity.npz") as saved:
        assert np.all(saved["correction_factor"] == 1)
        assert np.array_equal(saved["sensitivity_raw_fT_per_sqrtHz"], saved["sensitivity_corrected_fT_per_sqrtHz"])


@pytest.mark.parametrize("key,value", [
    ("actual_rate_Sa_s", 0.0), ("signal_path", "sample.r"), ("demod_idx", 0),
    ("noise_x_V", [0.0]), ("noise_x_V", [0.0, np.nan]),
])
def test_invalid_r_noise_preserves_calibration_diagnostic_and_blocks_sensitivity(tmp_path, key, value):
    synthetic_run(tmp_path)
    path = tmp_path / "raw/noise_D3_0000.npz"
    with np.load(path, allow_pickle=False) as saved:
        arrays = dict(saved)
    np.savez(path, **(arrays | {key: value}))
    ctx = AnalysisWorkflow(tmp_path)
    ctx.execute(["B01", "B02"])
    calibration = (tmp_path / "results/response_fit.npz").read_bytes()
    with pytest.raises(ValueError, match="噪声记录"):
        ctx.execute(["B03"])
    state = json.loads((tmp_path / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["B03"]["status"] == "quality_failed"
    assert state["steps"]["B03"]["error"]
    assert (tmp_path / "results/response_fit.npz").read_bytes() == calibration
    with pytest.raises(ValueError, match="缺少前置"):
        AnalysisWorkflow(tmp_path).execute(["B04"])
    assert not (tmp_path / "results/sensitivity.npz").exists()


def test_r_comparison_reads_r_and_preserves_inputs(tmp_path):
    from ..rf_sensitivity_direct_aw.comparison import _load_result
    from . import run_format
    entries = []
    for name in ("first", "second"):
        directory = tmp_path / name
        synthetic_run(directory)
        analyze(directory)
        entries.append({"run_id": name, "run_dir": str(directory), "legend": f"Run {name}"})
    sources = {path: hashlib.sha256(path.read_bytes()).hexdigest()
               for name in ("first", "second") for path in (tmp_path / name).rglob("*") if path.is_file()}
    loaded = _load_result(entries[0], identity=run_format)
    assert not np.all(loaded["response"] == 0.5)
    result = compare_runs(entries, tmp_path / "comparison")
    assert result["experiment_id"] == EXPERIMENT_ID and result["response_component"] == "R"
    assert (tmp_path / "comparison/full_analysis_comparison.png").read_bytes().startswith(b"\x89PNG")
    assert (tmp_path / "comparison/full_analysis_comparison.pdf").read_bytes().startswith(b"%PDF")
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in sources.items())
    with pytest.raises(ValueError, match="旧数据"):
        _load_result(entries[0])


def test_real_analysis_subprocess_and_wrong_identity(tmp_path):
    config, _ = synthetic_run(tmp_path)
    assert ADAPTER.analyze(tmp_path, None)["artifacts"]
    assert json.loads((tmp_path / "results/analysis.json").read_text(encoding="utf-8"))["response_component"] == "R"
    config["experiment_id"] = "rf-sensitivity-aw"
    path = tmp_path / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        DEFINITION.analyze(tmp_path)
    assert path.read_bytes() == before


def test_imports_without_runtime_or_repository_cwd(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    environment = os.environ.copy() | {"PYTHONPATH": str(Path(__file__).resolve().parents[3]),
        "LAB_TYPED_PARAMETERS": "invalid JSON", "LAB_RUN_DIR": "missing"}
    code = "from lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r import models, workflow, analysis, comparison; import experiments.RF_Field_Sensitivity_AW_R; import experiments.RF_Field_Sensitivity_AW_R_plot"
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


def test_full_and_separate_analysis_have_identical_science_arrays(tmp_path):
    complete, staged = tmp_path / "complete", tmp_path / "staged"
    synthetic_run(complete)
    synthetic_run(staged)
    whole = analyze(complete)
    for step in ("B01", "B02", "B03", "B04"):
        AnalysisWorkflow(staged).execute([step])
    separate = json.loads((staged / "results/analysis.json").read_text(encoding="utf-8"))
    for key in ("response_fit", "sensitivity", "frequency_response_correction", "calibration_assumption", "stage_frequencies"):
        assert separate[key] == whole[key]
    for name in ("response_fit.npz", "psd_avg.npz", "sensitivity.npz"):
        with np.load(complete / "results" / name) as first, np.load(staged / "results" / name) as second:
            assert first.files == second.files
            for key in first.files:
                np.testing.assert_equal(first[key], second[key])


def test_zero_r_slope_blocks_dependents_and_preserves_analysis_diagnostic(tmp_path):
    config, _ = synthetic_run(tmp_path)
    path = tmp_path / "raw/response_data.npz"
    with np.load(path) as data:
        arrays = dict(data)
    config["parameters"]["RF_AMP_POINTS"] = 3
    (tmp_path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for key in ("amplitudes_V", "amplitudes_nT", "demod3_x_V", "demod3_y_V", "demod3_r_V"):
        arrays[key] = arrays[key][:3]
    arrays["demod3_r_V"] = np.zeros(3)
    np.savez(path, **arrays)
    ctx = AnalysisWorkflow(tmp_path)
    with pytest.raises(ValueError, match="斜率"):
        ctx.execute(["B01", "B02"])
    saved = json.loads((tmp_path / "analysis_state.json").read_text(encoding="utf-8"))
    assert saved["steps"]["B02"]["status"] == "quality_failed"
    assert "斜率" in saved["steps"]["B02"]["error"]
    with pytest.raises(ValueError, match="缺少前置"):
        AnalysisWorkflow(tmp_path).execute(["B03"])
    assert not (tmp_path / "results/sensitivity.npz").exists()


def test_analysis_fingerprints_override_archive_and_input_drift_are_isolated(tmp_path):
    synthetic_run(tmp_path)
    ctx = AnalysisWorkflow(tmp_path)
    ctx.execute(["B01", "B02", "B03"])
    response = (tmp_path / "results/response_fit.npz").read_bytes()
    psd = (tmp_path / "results/psd_avg.npz").read_bytes()
    config = yaml.safe_load((tmp_path / "experiment_config.yaml").read_text(encoding="utf-8"))
    raw = {path.name: path.read_bytes() for path in (tmp_path / "raw").glob("*")}
    override = AnalysisWorkflow(tmp_path, {"NOISE_NPERSEG": 128})
    assert override.states["B02"]["status"] == "completed"
    assert override.states["B03"]["status"] == "stale"
    with pytest.raises(ValueError, match="刷新"):
        override.execute(["B03"], expected_revision=override.revision + 1)
    override.execute(["B03"])
    backups = list((tmp_path / "results").glob("before_*"))
    assert any((path / "psd_avg.npz").read_bytes() == psd for path in backups if (path / "psd_avg.npz").is_file())
    assert all(not (path / "response_fit.npz").exists() for path in backups)
    assert (tmp_path / "results/response_fit.npz").read_bytes() == response
    assert yaml.safe_load((tmp_path / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == config["parameters"]
    assert raw == {path.name: path.read_bytes() for path in (tmp_path / "raw").glob("*")}
    assert AnalysisWorkflow(tmp_path).options == {"noise_nperseg": 128}
    with (tmp_path / "results/response_fit.npz").open("ab") as stream:
        stream.write(b"changed")
    assert AnalysisWorkflow(tmp_path).states["B02"]["status"] == "stale"
    with (tmp_path / "raw/response_data.npz").open("ab") as stream:
        stream.write(b"changed input")
    drift = AnalysisWorkflow(tmp_path)
    assert drift.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        drift.execute(["B04"])


def test_legacy_parameter_migration_and_old_raw_remain_compatible(tmp_path):
    from ...common import find_project_root
    from .run_format import analysis_block_reason
    params = RFSensitivityDirectAWRParams.from_external({"RF_STEP_SETTLE_S": 0.2, "TEMP_OFF_SETTLE_S": 0.7})
    assert params.system_settle_s == 0.7
    explicit = RFSensitivityDirectAWRParams.from_external({"RF_STEP_SETTLE_S": 0.7, "TEMP_OFF_SETTLE_S": 0.9, "SYSTEM_SETTLE_S": 0.1})
    assert explicit.system_settle_s == 0.1
    current = RFSensitivityDirectAWRParams.from_yaml(find_project_root() / "params/experiments/rf-sensitivity-aw-r.yaml")
    assert RFSensitivityDirectAWRParams.from_external(current.to_external()).to_external() == current.to_external()
    config, _ = synthetic_run(tmp_path)
    config["schema_version"] = 2
    config.pop("raw_format_version", None)
    assert not analysis_block_reason(config)
    path = tmp_path / "experiment_config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    raw = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "raw").iterdir()}
    AnalysisWorkflow(tmp_path).execute(["B01", "B02", "B03"])
    assert raw == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "raw").iterdir()}
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"] == 2
