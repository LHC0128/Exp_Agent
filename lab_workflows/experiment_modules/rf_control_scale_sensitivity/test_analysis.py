"""合成数据检查 DirectAW 色散绝对斜率、两类线宽、频带中位数及离线入口。"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import warnings
import numpy as np
import pytest
import yaml
from ..mx_y_rf_sensitivity.analysis_core import lorentzian_response
from .models import RFControlScaleParams
from . import run_format, stages
from .analysis import AnalysisWorkflow, analyze
from .analysis_steps import fit_dispersive_slope, select_noise_records
from ..rf_sensitivity_direct_aw.analysis_steps import dispersive, fit_response_curve
from sensitivity_analysis.fitting import LARMOR_PER_NT


def synthetic_run(path, *, include_noise=True, control_name="ControlA", noise_records=2, raw_version=1,
                  scales=None, control_scale_factors=None):
    path.mkdir(parents=True)
    (path / "raw").mkdir()
    (path / "results").mkdir()
    params = replace(RFControlScaleParams(), control_waveform_files=[f"{control_name}/waveforms/optimal_control_waveform.csv"],
        arb_waveform_file=f"{control_name}/waveforms/optimal_control_waveform.csv", control_scales=[0.9, 1.0, 1.1],
        rf_amp_points=21, z_rf_freq=3000., z_v_to_nt=1., bw_freq_points=41, noise_n_avg=noise_records,
        include_noise=include_noise, noise_nperseg=1000)
    params = replace(params, control_scales=list(scales) if scales is not None else params.control_scales,
        control_scale_factors=control_scale_factors or {})
    config = dict(experiment_id=run_format.EXPERIMENT_ID, execution_mode="typed_workflow",
        schema_version=1 if control_scale_factors is None else 2, raw_format_version=raw_version, acquisition_method=run_format.ACQUISITION_METHOD,
        completion_status="completed", phase_calibration_status="completed",
        timestamp="2026-01-01T00:00:00", run_tag="synthetic", parameters=params.to_external())
    manifest = {"raw_format_version": raw_version, "points": []}
    if raw_version == 2:
        config["phase_calibration_scope"] = manifest["phase_calibration_scope"] = run_format.PHASE_CALIBRATION_SCOPE
    for scale in params.control_scales:
        relative = f"controls/{control_name}/scales/scale_{scale:.9f}"
        point = path / relative
        (point / "raw").mkdir(parents=True)
        (point / "results").mkdir()
        child = deepcopy(config)
        factor = params.control_scale_factors.get(control_name, 1.)
        child.update(control_scale=scale, calibration_scale=1.0,
            control_scale_factor=factor, effective_control_scale=scale * factor)
        xy_phase, rf_phase = 10 * scale, 30 * scale
        if raw_version == 2:
            child.update(calibration_scale=scale, calibration_source=".",
                phase_calibration_steps={key: {"status": "completed", "error": ""} for key in run_format.POINT_CALIBRATION_STAGES},
                calibration={"demod0": {"phase_shift_deg": 2 * scale}, "demod3": {"phase_shift_deg": 3 * scale}},
                xy_phase_calibration={"converged": True, "final_phase_deg": xy_phase},
                rf_phase_calibration={"selected_phase_deg": rf_phase})
            np.savez(point / "raw/z_phase_scan.npz", selected_phase_deg=rf_phase)
            np.savez(point / "raw/xy_aw_waveforms_initial.npz", omega_ctrl_Hz=np.array([1., 2.]) * scale)
        (point / "experiment_config.yaml").write_text(yaml.safe_dump(child), encoding="utf-8")
        field = np.linspace(-2, 2, 21)
        gamma = 1.2 * (1 + 2 * (scale - 1))
        response = dispersive(field, scale * 2 * gamma**2, gamma, .2, .3)
        np.savez(point / "raw/response_data.npz", amplitudes_nT=field, amplitudes_V=field,
                 demod3_x_V=response, demod3_y_V=np.zeros(21), demod3_r_V=np.abs(response), z_rf_freq_Hz=3000.,
                 xy_ctrl_phase_deg=xy_phase, z_burst_phase_deg=(rf_phase + np.where(field < 0, 180, 0)) % 360)
        frequency = np.linspace(2000, 4000, 41)
        np.savez(point / "raw/bandwidth_data.npz", frequency_Hz=frequency,
                 demod3_r_V=lorentzian_response(frequency, 1., 300., 3000 + (scale - 1) * 100, .01),
                 z_rf_amplitude_vpp=.01, demod_idx=3)
        np.savez(point / "raw/xy_aw_waveforms.npz", omega_ctrl_Hz=np.array([1., 2.]) * scale)
        if include_noise:
            for index in range(noise_records):
                data = np.random.default_rng(100 + index).normal(size=2000) * scale**2
                np.savez(point / "raw" / f"noise_D3_{index:04d}.npz", noise_x_V=data, actual_rate_Sa_s=1000., demod_idx=3)
        manifest["points"].append(dict(control=control_name, scale=scale, path=relative, status="completed",
            control_scale_factor=factor, effective_control_scale=scale * factor))
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (path / "raw/point_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_no_nominal_scale_keeps_absolute_metrics_and_compensation(tmp_path, monkeypatch):
    from . import plotting
    monkeypatch.setattr(plotting, "plot_results", lambda *args, **kwargs: None)
    path = synthetic_run(tmp_path / "arbitrary", scales=[1.5, 0., -.5],
        include_noise=False, raw_version=2, control_scale_factors={"ControlA": 1.25})
    before = {p: p.read_bytes() for p in path.rglob("*.npz")}
    result = analyze(path)
    assert [row["scale"] for row in result["points"]] == [1.5, 0., -.5]
    assert result["controls"]["ControlA"]["normalization_scale"] is None
    assert "未采集" in result["controls"]["ControlA"]["normalization_reason"]
    assert not result["points"][1]["valid"]
    assert all("normalized_slope" not in row for row in result["points"])
    assert [row["effective_control_scale"] for row in result["points"]] == [1.875, 0., -.625]
    assert all(row["control_scale_factor"] == 1.25 for row in result["points"])
    assert all(p.read_bytes() == content for p, content in before.items())
    config = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
    child_path = path / "controls/ControlA/scales/scale_1.500000000/experiment_config.yaml"
    child = yaml.safe_load(child_path.read_text(encoding="utf-8"))
    child["effective_control_scale"] = 99.
    child_path.write_text(yaml.safe_dump(child), encoding="utf-8")
    from .analysis_steps import load_inputs
    with pytest.raises(ValueError, match="补偿倍率"):
        load_inputs(path, config)


def test_v2_independent_phase_analysis_and_v1_history_keep_separate_meanings(tmp_path, monkeypatch):
    from . import plotting
    monkeypatch.setattr(plotting, "plot_results", lambda *args, **kwargs: None)
    for version, scope in ((1, "nominal_control"), (2, "per_control_scale")):
        path = synthetic_run(tmp_path / f"v{version}", raw_version=version)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*.npz")}
        result = analyze(path)
        assert result["raw_format_version"] == version and result["phase_calibration_scope"] == scope
        assert ("固定标称相位" in result["sensitivity_assumption"]) == (version == 1)
        assert [p["slope_V_per_nT"] for p in result["points"]] == pytest.approx([1.8, 2., 2.2])
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())


@pytest.mark.parametrize("fault", ["wrong_scale", "missing_step", "nonfinite", "mixed_version", "missing_phase_raw", "wrong_response_phase"])
def test_v2_rejects_missing_stale_or_mixed_phase_calibration(tmp_path, fault):
    path = synthetic_run(tmp_path / "run", raw_version=2)
    point = next(path.glob("controls/*/scales/*"))
    file = point / "experiment_config.yaml"
    config = yaml.safe_load(file.read_text(encoding="utf-8"))
    if fault == "wrong_scale":
        config["calibration_scale"] = 1.0
    elif fault == "missing_step":
        config["phase_calibration_steps"].pop("A11")
    elif fault == "nonfinite":
        config["calibration"]["demod3"]["phase_shift_deg"] = float("nan")
    elif fault == "mixed_version":
        config["raw_format_version"] = 1
    elif fault == "missing_phase_raw":
        (point / "raw/z_phase_scan.npz").unlink()
    else:
        with np.load(point / "raw/response_data.npz") as saved:
            values = dict(saved)
        values["xy_ctrl_phase_deg"] = -100.
        np.savez(point / "raw/response_data.npz", **values)
    file.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="校相|相位|倍率|原始|格式"):
        AnalysisWorkflow(path).execute(["B01"])


def manual_phase_run(path):
    synthetic_run(path, raw_version=2)
    for file in [path / "experiment_config.yaml", *path.glob("controls/*/scales/*/experiment_config.yaml")]:
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        config["parameters"].update(Z_PHASE_SCAN_ENABLED=False, Z_BURST_PHASE_INIT=181.5)
        if "control_scale" in config:
            config["rf_phase_calibration"] = {"mode": "manual", "scan_skipped": True,
                "points_completed": 0, "selected_phase_deg": 181.5}
            (file.parent / "raw/z_phase_scan.npz").unlink()
            with np.load(file.parent / "raw/response_data.npz") as saved:
                values = dict(saved)
            values["z_burst_phase_deg"] = (181.5 + np.where(values["amplitudes_V"] < 0, 180, 0)) % 360
            np.savez(file.parent / "raw/response_data.npz", **values)
        file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_legacy_missing_scan_switch_in_copied_children_keeps_history_unchanged(tmp_path):
    path = synthetic_run(tmp_path / "old_children", raw_version=2)
    originals = {}
    for file in path.glob("controls/*/scales/*/experiment_config.yaml"):
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        config["parameters"].pop("Z_PHASE_SCAN_ENABLED")
        file.write_text(yaml.safe_dump(config), encoding="utf-8")
        originals[file] = file.read_bytes()
    # 模拟续测：新父运行有显式 True，已复制的旧子运行缺键，仍是同一种扫描协议。
    analysis = AnalysisWorkflow(path)
    analysis.execute(["B01"])
    assert analysis.states["B01"]["status"] == "completed"
    assert all(file.read_bytes() == content for file, content in originals.items())


def test_manual_phase_analysis_keeps_all_metrics_without_scan_file(tmp_path, monkeypatch):
    from . import plotting
    monkeypatch.setattr(plotting, "plot_results", lambda *args, **kwargs: None)
    result = analyze(manual_phase_run(tmp_path / "manual"))
    assert result["rf_phase_mode"] == "manual" and result["rf_manual_phase_deg"] == 181.5
    assert "共用手动 RF 相位" in result["sensitivity_assumption"]
    assert all(row["rf_phase_mode"] == "manual" and row["rf_phase_deg"] == 181.5 for row in result["points"])
    assert [row["slope_V_per_nT"] for row in result["points"]] == pytest.approx([1.8, 2.0, 2.2])


@pytest.mark.parametrize("fault", ["missing_provenance", "parent_phase", "parent_mode", "response_phase"])
def test_manual_phase_analysis_rejects_inconsistent_records(tmp_path, fault):
    path = manual_phase_run(tmp_path / "manual")
    point = next(path.glob("controls/*/scales/*"))
    file = point / "experiment_config.yaml"
    config = yaml.safe_load(file.read_text(encoding="utf-8"))
    if fault == "missing_provenance":
        config["rf_phase_calibration"].pop("scan_skipped")
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
    with pytest.raises(ValueError, match="相位|校相|快照"):
        AnalysisWorkflow(path).execute(["B01"])


def test_dispersive_fit_uses_direct_aw_absolute_slope_and_covariance():
    field = np.linspace(-8, 8, 81)
    for amplitude in (-10., 10.):
        response = dispersive(field, amplitude, 2.5, .4, .3)
        reference = fit_response_curve(field, response)
        result, source = fit_dispersive_slope(field, response)
        assert result["slope_V_per_nT"] == pytest.approx(reference["slope_V_per_nT"])
        assert result["slope_V_per_nT"] == pytest.approx(abs(amplitude) / 2.5**2)
        assert result["signed_slope_V_per_nT"] * amplitude > 0
        assert result["dispersive_hwhm_nT"] == pytest.approx(2.5)
        assert result["dispersive_hwhm_Hz"] == pytest.approx(2.5 * LARMOR_PER_NT)
        a, gamma = source["parameters"][:2]
        gradient = np.array([np.sign(a) / gamma**2, -2 * abs(a) / gamma**3, 0., 0.])
        assert result["slope_se_V_per_nT"] == pytest.approx(np.sqrt(max(0., gradient @ source["covariance"] @ gradient)))
        assert result["n_fit"] == len(field), "色散拟合必须使用整条响应，不能保留局部多项式窗口"
        np.testing.assert_allclose(source["fitted"], response, atol=1e-8)
    for x, y in [(field[:3], field[:3]), (field + 12, field), (field, field * np.nan), (field, np.ones_like(field))]:
        with pytest.raises(ValueError):
            fit_dispersive_slope(x, y)


def test_complete_analysis_uses_each_scale_and_preserves_raw(tmp_path, monkeypatch):
    from . import plotting
    path = synthetic_run(tmp_path / "run", control_name="常数控制")
    raw = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*.npz")}
    observed = {}
    real_save = plotting.save_figure
    def capture(figure, file, **kwargs):
        if Path(file).name == "full_analysis.png":
            observed["figure"] = figure
        return real_save(figure, file, **kwargs)
    monkeypatch.setattr(plotting, "save_figure", capture)
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Glyph .* missing")
        result = analyze(path)
    rows = result["points"]
    assert [row["slope_V_per_nT"] for row in rows] == pytest.approx([1.8, 2., 2.2])
    assert [row["frequency_shift_Hz"] for row in rows] == pytest.approx([-10., 0., 10.], abs=1e-5)
    assert [row["normalized_slope"] for row in rows] == pytest.approx([.9, 1., 1.1])
    assert [row["relative_sensitivity"] for row in rows] == pytest.approx([.9, 1., 1.1])
    assert rows[1]["normalized_slope_se"] == 0
    assert result["common_band_valid"]
    assert result["plot_labels"] == {"常数控制": "常数控制"}
    assert result["full_analysis_panels"] == ["slope_V_per_nT", "dispersive_hwhm_Hz", "center_Hz", "sensitivity_fT_per_sqrtHz"]
    axes = observed["figure"].axes
    np.testing.assert_allclose(axes[0].lines[0].get_ydata(), [1.8, 2., 2.2])
    np.testing.assert_allclose(axes[1].lines[0].get_ydata(), np.array([.96, 1.2, 1.44]) * LARMOR_PER_NT)
    np.testing.assert_allclose(axes[2].lines[0].get_ydata(), [2990., 3000., 3010.])
    np.testing.assert_allclose(axes[3].lines[0].get_ydata(), [row["sensitivity_fT_per_sqrtHz"] for row in rows])
    assert axes[0].get_legend().get_texts()[0].get_text() == "常数控制"
    assert axes[1].get_ylabel() == "Dispersive HWHM (Hz)"
    assert axes[3].get_title() == "Median over 3-150 Hz"
    for file, digest in raw.items():
        assert hashlib.sha256(file.read_bytes()).hexdigest() == digest
    for name in ("metrics.csv", "full_analysis.pdf", "frequency_curves.png", "sensitivity_spectra.png"):
        assert (path / "results" / name).is_file()
    state = AnalysisWorkflow(path)
    assert all(record["status"] == "completed" for record in state.states.values())
    legacy = AnalysisWorkflow(path, {"SLOPE_FIT_POINTS": 11})
    assert "slope_fit_points" not in legacy.options
    assert legacy.states["B02"]["status"] == "completed"
    unfiltered = AnalysisWorkflow(path, {"NOISE_OUTLIER_POLICY": "none"})
    assert unfiltered.states["B02"]["status"] == "completed"
    assert unfiltered.states["B03"]["status"] == "stale"
    changed = AnalysisWorkflow(path, {"SENSITIVITY_BAND_MAX_HZ": 120})
    assert changed.states["B01"]["status"] == "completed"
    assert changed.states["B02"]["status"] == "completed"
    assert changed.states["B03"]["status"] == "stale"
    assert changed.states["B04"]["status"] == "stale"
    previous = json.loads((path / "analysis_state.json").read_text(encoding="utf-8"))
    previous["fingerprint"]["analysis_version"] = "rf-control-scale-v1"
    (path / "analysis_state.json").write_text(json.dumps(previous), encoding="utf-8")
    assert AnalysisWorkflow(path).states["B01"]["status"] == "stale"


def test_median_is_not_globally_masked_by_linewidth_or_one_noisy_bin(tmp_path, monkeypatch):
    from . import plotting
    path = synthetic_run(tmp_path / "run")
    bandwidth_path = next(path.glob("controls/*/scales/*/raw/bandwidth_data.npz"))
    with np.load(bandwidth_path) as saved:
        data = dict(saved)
    data["demod3_r_V"] = lorentzian_response(data["frequency_Hz"], 1., 149.4, 2990., .01)
    np.savez(bandwidth_path, **data)
    ctx = AnalysisWorkflow(path)
    ctx.execute(["B01", "B02", "B03"])
    with np.load(path / "results/psd_data.npz") as saved:
        spectra = dict(saved)
    frequency = spectra["p0000_frequency_Hz"]
    spectra["p0000_psd_V2_per_Hz"][np.argmin(abs(frequency - 20))] *= 1e12
    np.savez(path / "results/psd_data.npz", **spectra)
    monkeypatch.setattr(plotting, "plot_results", lambda *args, **kwargs: None)
    result = ctx.execute(["B04"])
    rows = result["points"]
    assert result["comparison_band_Hz"] == [3., 150.]
    assert result["common_band_valid"]
    assert rows[0]["band_exceeds_lorentzian_hwhm"]
    assert all(row["sensitivity_fT_per_sqrtHz"] > 0 for row in rows)
    mask = (frequency >= 3) & (frequency <= 150)
    asd = np.sqrt(spectra["p0000_psd_V2_per_Hz"]) / (rows[0]["slope_V_per_nT"] / 1e6)
    assert rows[0]["sensitivity_fT_per_sqrtHz"] == pytest.approx(np.median(asd[mask]))
    assert rows[0]["sensitivity_fT_per_sqrtHz"] < np.mean(asd[mask]) / 100


def test_numeric_gradient_fallback_is_rejected(monkeypatch):
    from . import analysis_steps
    monkeypatch.setattr(analysis_steps, "fit_response_curve", lambda *args, **kwargs: {
        "success": False, "method": "numeric_gradient", "failure_reason": "模拟拟合失败"})
    with pytest.raises(ValueError, match="不使用数值梯度"):
        fit_dispersive_slope(np.linspace(-2, 2, 21), np.linspace(-1, 1, 21))


def test_noise_record_selection_is_conservative_and_reversible():
    scores = np.r_[np.ones(8), 100.]
    keep, quality = select_noise_records(scores)
    assert keep.tolist() == [True] * 8 + [False]
    assert quality["candidate_indices"] == [8]
    assert select_noise_records(scores, "none")[0].all()
    assert select_noise_records(np.ones(9) * 100)[0].all(), "整组一致偏差不能剔成好结果"
    assert select_noise_records(np.array([1., 1., 1., 100.]))[0].all()
    for bad in ([], [1., np.nan], [-1., 1.], [[1.]]):
        with pytest.raises(ValueError):
            select_noise_records(bad)


def test_bad_record_is_excluded_from_mean_but_raw_and_original_psd_remain(tmp_path, monkeypatch):
    from . import plotting
    path = synthetic_run(tmp_path / "run", noise_records=9)
    file = next(path.glob("controls/*/scales/*/raw/noise_D3_0003.npz"))
    with np.load(file) as saved:
        values = dict(saved)
    values["noise_x_V"] *= 100
    np.savez(file, **values)
    before = file.read_bytes()
    monkeypatch.setattr(plotting, "plot_results", lambda *args, **kwargs: None)
    ctx = AnalysisWorkflow(path)
    result = ctx.execute(["B01", "B02", "B03", "B04"])
    assert result["noise_rejection"]["rejected_records"] == 1
    assert result["points"][0]["noise_records_rejected"] == 1
    assert result["points"][0]["excluded_noise_records"] == "noise_D3_0003.npz"
    assert file.read_bytes() == before
    with np.load(path / "results/psd_data.npz") as psd:
        keep = psd["p0000_included_record_mask"]
        assert not keep[3] and keep.sum() == 8
        np.testing.assert_allclose(psd["p0000_psd_V2_per_Hz"], psd["p0000_psd_records_V2_per_Hz"][keep].mean(axis=0))
        np.testing.assert_allclose(psd["p0000_unfiltered_psd_V2_per_Hz"], psd["p0000_psd_records_V2_per_Hz"].mean(axis=0))
    assert result["points"][0]["sensitivity_before_rejection_fT_per_sqrtHz"] > 10 * result["points"][0]["sensitivity_fT_per_sqrtHz"]
    restored = AnalysisWorkflow(path, {"NOISE_OUTLIER_POLICY": "none"}).execute(["B03", "B04"])
    assert restored["noise_rejection"]["rejected_records"] == 0
    assert restored["points"][0]["sensitivity_fT_per_sqrtHz"] == pytest.approx(result["points"][0]["sensitivity_before_rejection_fT_per_sqrtHz"])
    assert (path / "results/noise_record_quality.csv").is_file()


def test_noise_disabled_and_invalid_point_diagnostics(tmp_path):
    path = synthetic_run(tmp_path / "run", include_noise=False)
    response = next(path.glob("controls/*/scales/*/raw/response_data.npz"))
    with np.load(response) as saved:
        data = dict(saved)
    data["demod3_x_V"] = np.zeros(21)
    np.savez(response, **data)
    result = analyze(path)
    assert not result["noise_enabled"]
    assert not result["points"][0]["valid"]
    assert result["points"][0]["rejection_reason"]
    assert result["points"][0]["lorentzian_fit_valid"]
    assert result["points"][0]["center_Hz"] == pytest.approx(2990.)
    assert all(row.get("sensitivity_fT_per_sqrtHz") is None for row in result["points"])


def test_partial_and_escaping_manifest_rejected_before_output(tmp_path):
    path = synthetic_run(tmp_path / "run")
    manifest = path / "raw/point_manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["points"][0]["path"] = "../escape"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    ctx = AnalysisWorkflow(path)
    with pytest.raises(ValueError, match="点目录"):
        ctx.execute(["B01"])
    assert not (path / "results/analysis.json").exists()


def test_lockin_rejects_complete_frequency_window_and_skips_disabled_noise():
    from ...lockin_compatibility import lockin_preflight
    from ...lockin_acquisition import experiment_acquisition_requests
    params = RFControlScaleParams(bw_freq_stop=6e6, include_noise=False)
    values = params.to_external()
    mapping = {"lockin_r": {"model": "MFLI", "capabilities": {"options": ["MF-F5M", "MF-MD", "MF-DIG"]}}}
    assert any("BW_FREQ_STOP" in reason for reason in lockin_preflight(run_format.EXPERIMENT_ID, values, mapping=mapping))
    assert not any("噪声" in request.stage for request in experiment_acquisition_requests(run_format.EXPERIMENT_ID, values))


def test_real_offline_subprocess_entrypoint(tmp_path):
    path = synthetic_run(tmp_path / "run", include_noise=False)
    env = {**os.environ, "LAB_TYPED_RUN_DIR": str(path), "LAB_TYPED_PARAMETERS": "{}"}
    # 使用真实薄入口，验证不依赖外部理论文件或在线仪器。
    script = Path(__file__).resolve().parents[3] / "experiments/RF_Control_Scale_Sensitivity_plot.py"
    process = subprocess.run([sys.executable, str(script), str(path)], env=env, text=True, capture_output=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    assert (path / "results/analysis.json").is_file()
