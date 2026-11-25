"""离线合成回归：实测幅值、频带资格、Welch、历史指纹及真实子进程产物。"""
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from ...experiments.typed import TypedWorkflowAdapter
from . import analysis, analysis_steps as steps, run_format, stages
from .models import RfPhaseSensitivityParams

REGRESSION_CASES = {
    "rf-phase-sensitivity": {"analysis": {
        "sources": [
            "lab_workflows/experiment_modules/rf_phase_sensitivity/analysis.py",
            "lab_workflows/experiment_modules/rf_phase_sensitivity/analysis_steps.py",
            "lab_workflows/experiment_modules/rf_phase_sensitivity/run_format.py",
        ],
        "tests": [
            "test_direct_tone_fit_is_independent_of_record_start",
            "test_real_analysis_subprocess_publishes_all_products",
            "test_partial_run_cannot_bridge_frequency_gaps",
            "test_low_snr_and_electrical_warnings_still_plot_and_refer_with_advisory_flags",
            "test_hard_integrity_failures_keep_detector_asd_only",
            "test_advisory_gates_still_refer_noise_with_warnings",
            "test_repeat_uncertainty_gate_has_hard_cap",
            "test_empty_partial_run_writes_no_fake_observations",
            "test_rejects_ineligible_history_before_any_write",
            "test_raw_and_result_fingerprints_invalidate_previous_metrics",
        ],
    }},
}

ROOT = Path(__file__).resolve().parents[3]
RATE = 2048.25
K0 = 0.037
K0_SE = 0.0008
NOISE_SIGMA_V = 20e-6
LOCKIN = dict(phase_deg=177., order=4, time_constant_s=0.001, stream_rate_sa_s=RATE, oscillator_hz=5000.)


def _write_config(run, config):
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")


def _scope(run, label, f_hz):
    t = np.arange(300) / 125000
    reference = np.cos(2 * np.pi * 5000 * t)
    coil = 0.025 * np.cos(2 * np.pi * 5000 * t + 0.4)
    run_format.write_frame(run, label, "scope", dict(rate_sa_s=125000., modulation_analysis_hz=f_hz),
                           time_s=t, reference_v=reference, coil_v=coil)


def _true_h(f_hz):
    return K0 * 0.96 / math.sqrt(1.0 + (f_hz / 60.0) ** 2)


def _synthetic_run(run, *, status="completed", frequencies=None, missing=(), rejected=(), malformed=(),
                   low_snr=(), repeat_ratio=1.03, k0=K0, noise_bad=None, legacy_scope=False):
    p = RfPhaseSensitivityParams(pm_frequencies_hz=frequencies or [5., 10., 20., 40., 80.],
                                 noise_duration_s=16., welch_segment_s=1.)
    (run / "raw").mkdir(parents=True)
    config = run_format.initial_metadata(p) | dict(completion_status=status, schema_version=p.schema_version,
                                                  parameters=p.to_external())
    if legacy_scope:
        anchor = p.pm_anchor_hz
        sweep_order = [anchor, *(f for f in p.pm_frequencies_hz if f != anchor), anchor]
        config["predicted"]["pm_sweep_order_hz"] = sweep_order
    else:
        sweep_order = list(p.sweep_order_hz)
    # 写入 A03 整周粗扫帧与 A04 局部标定帧，保证 B01 四幅结果图都能渲染实测轨迹。
    coarse_grid = np.arange(0., 360., 30.)
    config["phase_response"] = dict(burst_command_deg=124.5, rotation_deg=0.0, lockin_phase_deg=LOCKIN["phase_deg"])
    t_static = np.arange(16) / RATE
    for idx, deg in enumerate(coarse_grid):
        label = f"coarse_{idx:02d}"
        phi = np.deg2rad(deg - 124.5)
        xv = np.full(16, 0.0005 * np.cos(phi))
        yv = np.full(16, k0 * np.sin(phi))
        path = run_format.write_frame(run, label, "xy", dict(rate_sa_s=RATE, time_source="module_timestamp",
                                                             lockin=LOCKIN, burst_command_deg=float(deg)),
                                      time_s=t_static, x_v=xv, y_v=yv)
        config["data_files"].append(path)
    local_points = []
    for idx, dphi in enumerate(np.linspace(-6., 6., 9)):
        deg = 124.5 + float(dphi)
        label = f"local_{idx:02d}_xy"
        xm, ym = 0.0003 * np.deg2rad(dphi), k0 * np.deg2rad(dphi)
        path = run_format.write_frame(run, label, "xy", dict(rate_sa_s=RATE, time_source="module_timestamp",
                                                             lockin=LOCKIN, burst_command_deg=deg,
                                                             carrier_command_deg=deg),
                                      time_s=t_static, x_v=np.full(16, xm), y_v=np.full(16, ym))
        config["data_files"].append(path)
        local_points.append(dict(burst_command_deg=deg, carrier_command_deg=deg, requested_offset_deg=float(dphi),
                                 xy_label=label, elapsed_s=float(idx), x_mean_v=float(xm), y_mean_v=float(ym)))
    config["calibration"].update(accepted=True, k0_v_per_rad=k0, k0_se_v_per_rad=K0_SE,
                                 burst_command_deg=124.5, carrier_center_deg=124.5 if not legacy_scope else 88.,
                                 slope_x_v_per_rad=0.0003, lockin_phase_deg=LOCKIN["phase_deg"], points=local_points)
    rng = np.random.default_rng(40309)
    for index, f_hz in enumerate(sweep_order):
        repeat = legacy_scope and (index == len(sweep_order) - 1)
        if f_hz in missing and not repeat:
            continue
        scope = f"scope_{index}" if legacy_scope else None
        xy = f"tone_{index}"
        if legacy_scope:
            _scope(run, scope, f_hz)
        pm = math.radians(0.93 + 0.02 * index) if legacy_scope else math.radians(2.0)
        electrical = f_hz not in rejected
        row = dict(frequency_hz=f_hz, scope_label=scope, xy_label=xy if electrical else None,
                   repeat=repeat, phase_pk_rad=pm,
                   electrical_accepted=electrical, accepted=electrical, reason="" if electrical else "PM 电学资格不足")
        if legacy_scope:
            row.update(phase_scatter_rad=0.0007, am_fraction_pk=0.0004, center_error_deg=0.15)
        config["transfer"].append(row)
        if not electrical:
            continue
        t = np.arange(round(RATE * p.pm_tone_duration_s)) / RATE
        theta = 2 * np.pi * f_hz * t + rng.uniform(-np.pi, np.pi)
        amplitude = _true_h(f_hz) * pm * (repeat_ratio if repeat else 1)
        y = 0.04 + 0.001 * t + (0 if f_hz in low_snr else amplitude) * np.cos(theta)
        y += 15e-6 * np.sin(2 * theta) + rng.normal(scale=2e-6, size=t.size)
        x = 0.02 - 0.002 * t + 5e-5 * np.sin(theta) + 9e-6 * np.sin(2 * theta)
        if f_hz in malformed:
            # 原始读写器容许的单步浮动超出离线均匀采样门。
            t[1::2] += 0.002 / RATE
        xy_meta = dict(rate_sa_s=RATE, time_source="module_timestamp", lockin=LOCKIN, frequency_hz=f_hz)
        if legacy_scope:
            xy_meta["scope_label"] = scope
        run_format.write_frame(run, xy, "xy", xy_meta, time_s=t + 3.27, x_v=x, y_v=y)
    scope_labels = ["noise_before", "noise_after"] if legacy_scope else []
    for scope in scope_labels:
        if not (noise_bad == "monitor" and scope == "noise_after"):
            _scope(run, scope, p.pm_anchor_hz)
    config["noise"].update(accepted=noise_bad != "electrical", electrical_accepted=noise_bad != "electrical",
                           xy_label="noise", scope_labels=scope_labels,
                           reason="残余 AM 超限" if noise_bad == "electrical" else "")
    count = round(RATE * p.noise_duration_s) if noise_bad != "short" else 512
    t = np.arange(count) / RATE
    if noise_bad == "lockin":
        state = LOCKIN | dict(order=3)
    elif noise_bad == "stream":
        state = LOCKIN | dict(stream_rate_sa_s=1.01 * RATE)
    else:
        state = LOCKIN
    run_format.write_frame(run, "noise", "xy", dict(rate_sa_s=RATE, time_source="module_timestamp",
                           lockin=state, source_mode="unmodulated_carrier"), time_s=t,
                           x_v=0.04 + t * 0.003, y_v=0.02 + t * 0.005 + rng.normal(scale=NOISE_SIGMA_V, size=t.size))
    _write_config(run, config)
    return config


def _csv(run, name):
    with (run / "results" / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_direct_tone_fit_is_independent_of_record_start():
    f_hz = 23.65
    t = np.arange(3871) / RATE
    theta = 2 * np.pi * f_hz * t
    rng = np.random.default_rng(11)
    y = 0.015 + 0.03 * t + 0.002 * np.sin(theta + 2.3) + 0.0003 * np.cos(2 * theta + 1.1)
    y += rng.normal(scale=8e-5, size=t.size)
    x = 0.08 - 0.2 * t + 0.00013 * np.cos(theta + 1.8)
    for start in (0., -106.11, 759.25):
        fit = steps.fit_tone(t + start, x, y, RATE * 1.0002, f_hz)
        assert fit["rate_sa_s"] == pytest.approx(RATE, rel=1e-10)
        assert fit["y_pk_v"] == pytest.approx(0.002, rel=0.003)
        assert fit["y_pk_se_v"] == pytest.approx(8e-5 * math.sqrt(2 / t.size), rel=0.08)
        assert fit["y_snr"] == pytest.approx(fit["y_pk_v"] / fit["y_pk_se_v"])
        assert fit["x_pk_v"] == pytest.approx(0.00013, abs=1e-9)
        assert fit["y_2f_pk_v"] == pytest.approx(0.0003, rel=0.02)

    nyquist = RATE / 2
    near = 0.71 * nyquist
    fit = steps.fit_tone(t, x, np.cos(2 * np.pi * near * t), RATE, near)
    assert fit["y_pk_v"] == pytest.approx(1.) and "y_2f_pk_v" not in fit
    with pytest.raises(ValueError, match="Nyquist"):
        steps.fit_tone(t, x, y, RATE, nyquist)
    with pytest.raises(ValueError, match="采样率"):
        steps.fit_tone(t, x, y, RATE * 0.99, f_hz)
    gap = t.copy()
    gap[200:] += 1 / RATE
    with pytest.raises(ValueError, match="缺样"):
        steps.fit_tone(gap, x, y, RATE, f_hz)
    with pytest.raises(ValueError, match="长度"):
        steps.fit_tone(t, x[:-1], y, RATE, f_hz)
    with pytest.raises(ValueError, match="9.8 个周期"):
        steps.fit_tone(t[:600], x[:600], y[:600], RATE, f_hz)
    fit = steps.fit_tone(t, x, 0.1 + 0.2 * t, RATE, f_hz)
    assert fit["y_snr"] < steps.MIN_TONE_SNR

    # 强 50/100/150 Hz 工频干扰不应虚高 40 Hz 的 Y 拟合误差棒，且接近 50 Hz 时应自动跳过碰撞工频避免病态。
    y_mains = 0.005 * np.sin(2 * np.pi * 40. * t) + 0.055 * np.cos(2 * np.pi * 50. * t + 0.4) + 0.004 * np.sin(2 * np.pi * 150. * t) + rng.normal(scale=5e-5, size=t.size)
    fit_40 = steps.fit_tone(t, x, y_mains, RATE, 40., correlation_s=0.004)
    assert fit_40["y_pk_v"] == pytest.approx(0.005, rel=0.003)
    assert fit_40["y_pk_se_v"] < 1e-4 and fit_40["y_snr"] > 50
    fit_50 = steps.fit_tone(t, x, 0.055 * np.cos(2 * np.pi * 50. * t), RATE, 50.)
    assert fit_50["y_pk_v"] == pytest.approx(0.055, rel=1e-4)


def test_real_analysis_subprocess_publishes_all_products(tmp_path):
    run = tmp_path / "hist"
    _synthetic_run(run)
    inputs = {path: path.read_bytes() for path in (run / "experiment_config.yaml", *(run / "raw").glob("*.npz"))}
    env = os.environ | dict(LAB_RUN_DIR=str(run), LAB_TYPED_PARAMETERS='{"step_ids":["B01"],"revision":0}',
                            LAB_PROGRESS_PROTOCOL="jsonl", PYTHONIOENCODING="utf-8")
    env.pop("LAB_CANCEL_FILE", None)
    child = subprocess.run([sys.executable, "-B", "-m", analysis.__name__], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert child.returncode == 0, child.stderr + child.stdout
    assert "__LAB_PROGRESS__" in child.stdout
    assert analysis.AnalysisWorkflow(run).snapshot()["steps"]["B01"]["status"] == "completed"
    assert {path.name for path in (run / "results").iterdir()} == set(stages.ANALYSIS[0].artifacts) == set(steps.ARTIFACTS)
    assert all((run / "results" / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
               for name in (steps.RESPONSE_PNG, steps.LOCAL_PNG, steps.TRANSFER_PNG, steps.NOISE_PNG))
    assert all(path.read_bytes() == value for path, value in inputs.items())
    summary = _json(run / "results" / steps.SUMMARY)
    assert summary["result_status"] == "complete" and summary["quality_passed"]
    assert summary["k0_closure"]["frequency_hz"] == 5
    assert summary["k0_closure"]["normalized_gain"] == pytest.approx(_true_h(5.) / K0, abs=0.001)
    lor = summary["transfer_lorentzian_fit"]
    assert lor["available"] and lor["h0_v_per_rad"] == pytest.approx(K0 * 0.96, rel=0.01)
    assert lor["fc_hz"] == pytest.approx(60.0, rel=0.02)
    assert summary["anchor_repeat"]["configured"] is False
    rows = _csv(run, steps.TRANSFER_CSV)
    for row in rows:
        expected = _true_h(float(row["frequency_hz"]))
        assert float(row["h_v_per_rad"]) == pytest.approx(expected, rel=0.001)
        assert float(row["normalized_gain"]) == pytest.approx(expected / K0, rel=0.001)
        assert float(row["normalized_h0_gain"]) == pytest.approx(expected / lor["h0_v_per_rad"], rel=0.001)
        assert float(row["normalized_gain_se"]) > float(row["h_fit_se_v_per_rad"]) / K0
        assert float(row["x_pk_v"]) == pytest.approx(5e-5, rel=0.001)
    w = summary["noise"]["welch"]
    assert w["rate_sa_s"] == pytest.approx(RATE)
    assert w["nperseg"] == 2049 and w["noverlap"] == w["nperseg"] // 2 and w["segments"] == 30
    assert w["one_sided"] is True and w["window"] == "hann" and w["detrend"] == "linear"
    assert w["resolution_hz"] <= 1.0
    band_f = np.log([5., 10., 20., 40., 80.])
    band_h = np.log([_true_h(f_val) for f_val in (5., 10., 20., 40., 80.)])
    noise = _csv(run, steps.NOISE_CSV)
    for row in noise:
        f = float(row["frequency_hz"])
        assert f >= 1
        assert float(row["h_lorentzian_v_per_rad"]) == pytest.approx(
            lor["h0_v_per_rad"] / math.sqrt(1.0 + (f / lor["fc_hz"]) ** 2), rel=1e-4)
        assert float(row["phase_asd_lorentzian_urad_per_sqrt_hz"]) == pytest.approx(
            float(row["detector_y_asd_v_per_sqrt_hz"]) / float(row["h_lorentzian_v_per_rad"]) * 1e6, rel=1e-4)
        if 5 <= f <= 80:
            expected_interp = math.exp(float(np.interp(math.log(f), band_f, band_h)))
            assert float(row["h_v_per_rad"]) == pytest.approx(expected_interp, rel=0.001)
            assert float(row["phase_asd_rad_per_sqrt_hz"]) == pytest.approx(
                float(row["detector_y_asd_v_per_sqrt_hz"]) / expected_interp, rel=0.001)
            assert float(row["phase_asd_urad_per_sqrt_hz"]) == pytest.approx(
                float(row["phase_asd_rad_per_sqrt_hz"]) * 1e6, rel=0.001)
            assert float(row["phase_asd_deg_per_sqrt_hz"]) == pytest.approx(
                math.degrees(float(row["phase_asd_rad_per_sqrt_hz"])), rel=0.001)
        else:
            assert row["phase_asd_rad_per_sqrt_hz"] == "" and row["phase_asd_deg_per_sqrt_hz"] == "" and row["phase_asd_urad_per_sqrt_hz"] == ""
    assert summary["phase_referral"]["lorentzian_full_band"]["available"]
    band = summary["phase_referral"]["bands"][0]
    asds = [float(row["detector_y_asd_v_per_sqrt_hz"]) for row in noise if row["transfer_band"]]
    assert float(np.median(asds)) == pytest.approx(NOISE_SIGMA_V * math.sqrt(2 / RATE), rel=0.10)
    assert band["median_urad_per_sqrt_hz"] == pytest.approx(band["median_rad_per_sqrt_hz"] * 1e6)
    assert band["median_deg_per_sqrt_hz"] == pytest.approx(math.degrees(band["median_rad_per_sqrt_hz"]))
    assert band["midpoint_deg_per_sqrt_hz"] == pytest.approx(math.degrees(band["midpoint_rad_per_sqrt_hz"]))
    assert band["midpoint_bin_hz"] == pytest.approx(42.5, abs=w["resolution_hz"] / 2)


def test_legacy_scope_archive_remains_compatible(tmp_path):
    run = tmp_path / "legacy_hist"
    _synthetic_run(run, legacy_scope=True)
    summary = analysis.analyze(run)
    assert summary["result_status"] == "complete" and summary["quality_passed"]
    assert summary["anchor_repeat"]["configured"] is True
    assert summary["anchor_repeat"]["frequency_hz"] == 20
    assert summary["anchor_repeat"]["ratio"] == pytest.approx(1.03, rel=0.001)
    for row in _csv(run, steps.TRANSFER_CSV):
        assert float(row["phase_scatter_rad"]) == pytest.approx(0.0007)
        expected = _true_h(float(row["frequency_hz"])) * (1.03 if row["repeat"] == "True" else 1)
        assert float(row["h_v_per_rad"]) == pytest.approx(expected, rel=0.001)


@pytest.mark.parametrize("missing, rejected, malformed", [
    ((30,), (), ()), ((), (30,), ()), ((), (), (30,)),
])
def test_partial_run_cannot_bridge_frequency_gaps(tmp_path, missing, rejected, malformed):
    _synthetic_run(tmp_path, status="cancelled", frequencies=[5., 10., 20., 30., 40., 60., 80.],
                   missing=missing, rejected=rejected, malformed=malformed)
    summary = analysis.analyze(tmp_path)
    assert summary["status"] == "completed" and summary["result_status"] == "partial"
    assert summary["acquisition_status"] == "cancelled" and summary["phase_referral"]["available"]
    assert summary["transfer_bands_hz"] == [[5., 20.], [40., 80.]]
    row = next((row for row in _csv(tmp_path, steps.TRANSFER_CSV) if float(row["frequency_hz"]) == 30), None)
    if missing:
        assert row is None
    else:
        assert row["accepted"] == "False" and row["h_v_per_rad"] == ""
        assert ("缺少 XY 帧" if rejected else "时间轴") in row["reason"]
    for row in _csv(tmp_path, steps.NOISE_CSV):
        f = float(row["frequency_hz"])
        assert bool(row["detector_y_asd_v_per_sqrt_hz"])
        assert bool(row["phase_asd_rad_per_sqrt_hz"]) is (5 <= f <= 20 or 40 <= f <= 80)


def test_low_snr_and_electrical_warnings_still_plot_and_refer_with_advisory_flags(tmp_path):
    config = _synthetic_run(tmp_path, frequencies=[5., 10., 20., 40., 80.], low_snr=(20.,))
    # 同步模拟电学告警但保留 XY 帧的场景（如 10 Hz AM 超限仍采 XY）。
    config["transfer"][1]["electrical_accepted"] = False
    config["transfer"][1]["accepted"] = False
    config["transfer"][1]["reason"] = "残余 AM 超过 0.1% 峰值"
    _write_config(tmp_path, config)

    summary = analysis.analyze(tmp_path)
    assert summary["result_status"] == "complete"
    assert summary["phase_referral"]["available"] is True
    assert summary["quality_passed"] is False
    assert summary["accepted_primary_points"] == 5 and summary["clean_primary_points"] == 3
    assert summary["transfer_bands_hz"] == [[5., 80.]]
    assert any("Y 音调信噪比" in w for w in summary["advisory_warnings"])
    assert any("残余 AM" in w for w in summary["advisory_warnings"])

    rows = _csv(tmp_path, steps.TRANSFER_CSV)
    row_10 = next(r for r in rows if float(r["frequency_hz"]) == 10. and r["repeat"] == "False")
    row_20 = next(r for r in rows if float(r["frequency_hz"]) == 20. and r["repeat"] == "False")
    assert row_10["accepted"] == "True" and row_10["quality_passed"] == "False"
    assert "残余 AM" in row_10["warnings"] and float(row_10["h_v_per_rad"]) > 0
    assert row_20["accepted"] == "True" and row_20["quality_passed"] == "False"
    assert "信噪比" in row_20["warnings"] and float(row_20["h_v_per_rad"]) > 0
    noise = _csv(tmp_path, steps.NOISE_CSV)
    assert any(float(r["frequency_hz"]) == pytest.approx(20., abs=1.0) and r["phase_asd_rad_per_sqrt_hz"] for r in noise)


@pytest.mark.parametrize("changes, diagnostic, fragment", [
    (dict(noise_bad="lockin"), "noise", "锁相状态"),
    (dict(noise_bad="stream"), "noise", "实际率"),
    (dict(noise_bad="short"), "noise", "Welch 分段数"),
])
def test_hard_integrity_failures_keep_detector_asd_only(tmp_path, changes, diagnostic, fragment):
    _synthetic_run(tmp_path, status="failed", **changes)
    summary = analysis.analyze(tmp_path)
    assert summary["acquisition_status"] == "failed" and summary["result_status"] == "partial"
    assert summary["status"] == "completed" and not summary["quality_passed"]
    assert not summary["phase_referral"]["available"]
    assert fragment in str(summary[diagnostic].get("reason") or summary[diagnostic]["reasons"])
    noise = _csv(tmp_path, steps.NOISE_CSV)
    assert noise and all(row["detector_y_asd_v_per_sqrt_hz"] and not row["phase_asd_rad_per_sqrt_hz"] for row in noise)


@pytest.mark.parametrize("changes, fragment", [
    (dict(legacy_scope=True, repeat_ratio=1.40), "对称相对差"),
    (dict(k0=2 * K0), "最低频点"),
    (dict(noise_bad="electrical"), "残余 AM"),
    (dict(legacy_scope=True, noise_bad="monitor"), "监测帧不可读"),
])
def test_advisory_gates_still_refer_noise_with_warnings(tmp_path, changes, fragment):
    _synthetic_run(tmp_path, status="completed", **changes)
    summary = analysis.analyze(tmp_path)
    assert summary["phase_referral"]["available"] is True
    assert summary["quality_passed"] is False
    assert any(fragment in w for w in summary["advisory_warnings"])
    noise = _csv(tmp_path, steps.NOISE_CSV)
    assert any(row["phase_asd_rad_per_sqrt_hz"] for row in noise)


def test_repeat_uncertainty_gate_has_hard_cap():
    first = dict(frequency_hz=20., repeat=False, accepted=True, normalized_gain=1.,
                 h_v_per_rad=1., h_fit_se_v_per_rad=0.20)
    repeat = first | dict(repeat=True, h_v_per_rad=1.40, h_fit_se_v_per_rad=0.28)
    closure, gate = steps._gates([first, repeat], {20.: first}, lowest=20., anchor=20.)
    assert closure["passed"] and not gate["passed"]
    assert gate["difference_fit_se"] * 2 > 0.30
    assert gate["fractional_limit"] == 0.30
    assert gate["symmetric_fractional_difference"] == pytest.approx(1 / 3)


def test_empty_partial_run_writes_no_fake_observations(tmp_path):
    p = RfPhaseSensitivityParams()
    _write_config(tmp_path, run_format.initial_metadata(p) | dict(completion_status="cancelled",
                  schema_version=p.schema_version, parameters=p.to_external()))
    summary = analysis.analyze(tmp_path)
    assert summary["result_status"] == "no_usable_data" and not summary["noise"]["detector_spectrum_available"]
    assert not _csv(tmp_path, steps.TRANSFER_CSV) and not _csv(tmp_path, steps.NOISE_CSV)
    assert all((tmp_path / "results" / name).stat().st_size > 0 for name in steps.ARTIFACTS)


@pytest.mark.parametrize("invalid", [dict(completion_status="running"), dict(experiment_id="other"), dict(raw_format_version=99)])
def test_rejects_ineligible_history_before_any_write(tmp_path, invalid):
    config = _synthetic_run(tmp_path)
    _write_config(tmp_path, config | invalid)
    with pytest.raises(ValueError):
        analysis.analyze(tmp_path)
    assert not (tmp_path / "results").exists() and not (tmp_path / "analysis_state.json").exists()


def test_raw_and_result_fingerprints_invalidate_previous_metrics(tmp_path):
    config = _synthetic_run(tmp_path)
    analysis.analyze(tmp_path)
    state = analysis.AnalysisWorkflow(tmp_path)
    initial_revision = state.revision
    assert state.summary["quality_passed"]
    TypedWorkflowAdapter._write_analysis_status(tmp_path, status="completed", error="仅作状态回写测试")
    assert run_format.read_config(tmp_path / "experiment_config.yaml")["analysis_status"] == "completed"
    after_status = analysis.AnalysisWorkflow(tmp_path)
    assert after_status.revision == initial_revision and after_status.states["B01"]["status"] == "completed"
    assert after_status.fingerprint == state.fingerprint

    _write_config(tmp_path, config | dict(failure_reason="输入诊断发生变化"))
    changed_config = analysis.AnalysisWorkflow(tmp_path)
    assert changed_config.states["B01"]["status"] == "stale"
    assert changed_config.fingerprint["config_sha256"] != state.fingerprint["config_sha256"]
    _write_config(tmp_path, config)

    path = tmp_path / "raw" / (config["transfer"][1]["xy_label"] + ".npz")
    raw, times = path.read_bytes(), path.stat()
    path.write_bytes(raw[:30] + bytes([raw[30] ^ 1]) + raw[31:])
    os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    with pytest.raises(ValueError, match="修订"):
        state.execute(["B01"], expected_revision=initial_revision)
    assert state.states["B01"]["status"] == "stale" and "phase_referral" not in state.summary
    path.write_bytes(raw)
    _write_config(tmp_path, config | dict(completion_status="cancelled", failure_reason="合成故障"))
    assert state.execute(["B01"])["acquisition_status"] == "cancelled"
    assert {p.name for p in (state.results / state.summary["backup_directory"]).iterdir()} == set(steps.ARTIFACTS)
    (state.results / steps.NOISE_CSV).unlink()
    restored = analysis.AnalysisWorkflow(tmp_path)
    assert restored.states["B01"]["status"] == "stale" and "quality_passed" not in restored.summary


def test_lorentzian_fit_excludes_mains_harmonic_collisions(tmp_path):
    """即使历史或扫频数据含 50/150 Hz 整倍频干扰飞点，√Lorentzian 拟合仍应自动跳过碰撞点恢复真实 H0/fc。"""
    config = _synthetic_run(tmp_path, frequencies=[13., 43., 50., 103., 150., 193.])
    # 人为给 50 Hz 和 150 Hz 注入 +45% 工频污染，验证拟合不被拉偏。
    for row in config["transfer"]:
        if row["frequency_hz"] in (50.0, 150.0):
            meta, arr = run_format.read_frame(tmp_path, row["xy_label"], "xy")
            (tmp_path / "raw" / f"{row['xy_label']}.npz").unlink()
            run_format.write_frame(tmp_path, row["xy_label"], "xy", meta,
                                   time_s=arr["time_s"], x_v=arr["x_v"], y_v=arr["y_v"] * 1.45)
    # 同时验证 carrier_center_deg 相对 burst_command_deg 偏移 > 1° 时仍可正常做 A04 漂移扣除与成图。
    config["calibration"]["carrier_center_deg"] = config["calibration"]["burst_command_deg"] + 2.5
    _write_config(tmp_path, config)

    summary = analysis.analyze(tmp_path)
    lor = summary["transfer_lorentzian_fit"]
    assert lor["available"] is True
    assert lor["h0_v_per_rad"] == pytest.approx(K0 * 0.96, rel=0.02)
    assert lor["fc_hz"] == pytest.approx(60.0, rel=0.03)
    assert lor["chi2_red"] < 10.0
