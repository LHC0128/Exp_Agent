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
            "test_failed_qualification_keeps_detector_asd_only",
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
    return K0 * 0.93 * (f_hz / 5) ** -0.37


def _synthetic_run(run, *, status="completed", frequencies=None, missing=(), rejected=(), malformed=(),
                   low_snr=(), repeat_ratio=1.03, k0=K0, noise_bad=None):
    p = RfPhaseSensitivityParams(pm_frequencies_hz=frequencies or [5., 10., 20., 40., 80.],
                                 noise_duration_s=16., welch_segment_s=1.)
    (run / "raw").mkdir(parents=True)
    config = run_format.initial_metadata(p) | dict(completion_status=status, schema_version=p.schema_version,
                                                  parameters=p.to_external())
    config["calibration"].update(accepted=True, k0_v_per_rad=k0, k0_se_v_per_rad=K0_SE,
                                 burst_command_deg=124.5, carrier_center_deg=88., slope_x_v_per_rad=0.0003,
                                 lockin_phase_deg=LOCKIN["phase_deg"])
    rng = np.random.default_rng(40309)
    for index, f_hz in enumerate(p.sweep_order_hz):
        repeat = index == len(p.sweep_order_hz) - 1
        if f_hz in missing and not repeat:
            continue
        scope, xy = f"scope_{index}", f"tone_{index}"
        _scope(run, scope, f_hz)
        pm = math.radians(0.93 + 0.02 * index)
        electrical = f_hz not in rejected
        config["transfer"].append(dict(frequency_hz=f_hz, scope_label=scope, xy_label=xy if electrical else None,
                                       repeat=repeat, phase_pk_rad=pm, phase_scatter_rad=0.0007,
                                       am_fraction_pk=0.0004, center_error_deg=0.15,
                                       accepted=electrical, reason="" if electrical else "PM 电学资格不足"))
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
        run_format.write_frame(run, xy, "xy", dict(rate_sa_s=RATE, time_source="module_timestamp",
                                                   lockin=LOCKIN, frequency_hz=f_hz, scope_label=scope),
                               time_s=t + 3.27, x_v=x, y_v=y)
    scope_labels = ["noise_before", "noise_after"]
    for scope in scope_labels:
        if not (noise_bad == "monitor" and scope == "noise_after"):
            _scope(run, scope, p.pm_anchor_hz)
    config["noise"].update(accepted=noise_bad != "electrical", xy_label="noise", scope_labels=scope_labels,
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
               for name in (steps.TRANSFER_PNG, steps.NOISE_PNG))
    assert all(path.read_bytes() == value for path, value in inputs.items())
    summary = _json(run / "results" / steps.SUMMARY)
    assert summary["result_status"] == "complete" and summary["quality_passed"]
    assert summary["k0_closure"]["frequency_hz"] == 5
    assert summary["k0_closure"]["normalized_gain"] == pytest.approx(0.93, abs=0.001)
    assert summary["anchor_repeat"]["frequency_hz"] == 20
    assert summary["anchor_repeat"]["ratio"] == pytest.approx(1.03, rel=0.001)
    rows = _csv(run, steps.TRANSFER_CSV)
    for row in rows:
        assert float(row["phase_scatter_rad"]) == pytest.approx(0.0007)
        expected = _true_h(float(row["frequency_hz"])) * (1.03 if row["repeat"] == "True" else 1)
        assert float(row["h_v_per_rad"]) == pytest.approx(expected, rel=0.001)
        assert float(row["normalized_gain"]) == pytest.approx(expected / K0, rel=0.001)
        assert float(row["normalized_gain_se"]) > float(row["h_fit_se_v_per_rad"]) / K0
        assert float(row["x_pk_v"]) == pytest.approx(5e-5, rel=0.001)
    w = summary["noise"]["welch"]
    assert w["rate_sa_s"] == pytest.approx(RATE)
    assert w["nperseg"] == 2049 and w["noverlap"] == w["nperseg"] // 2 and w["segments"] == 30
    assert w["one_sided"] is True and w["window"] == "hann" and w["detrend"] == "linear"
    assert w["resolution_hz"] <= 1.0
    noise = _csv(run, steps.NOISE_CSV)
    for row in noise:
        f = float(row["frequency_hz"])
        assert f >= 1
        if 5 <= f <= 80:
            assert float(row["h_v_per_rad"]) == pytest.approx(_true_h(f), rel=0.001)
            assert float(row["phase_asd_rad_per_sqrt_hz"]) == pytest.approx(
                float(row["detector_y_asd_v_per_sqrt_hz"]) / _true_h(f), rel=0.001)
        else:
            assert row["phase_asd_rad_per_sqrt_hz"] == ""
    band = summary["phase_referral"]["bands"][0]
    asds = [float(row["detector_y_asd_v_per_sqrt_hz"]) for row in noise if row["transfer_band"]]
    assert float(np.median(asds)) == pytest.approx(NOISE_SIGMA_V * math.sqrt(2 / RATE), rel=0.10)
    assert band["median_urad_per_sqrt_hz"] == pytest.approx(band["median_rad_per_sqrt_hz"] * 1e6)
    assert band["midpoint_bin_hz"] == pytest.approx(42.5, abs=w["resolution_hz"] / 2)


@pytest.mark.parametrize("missing, rejected, malformed, low_snr", [
    ((30,), (), (), ()), ((), (30,), (), ()), ((), (), (30,), ()), ((), (), (), (30,)),
])
def test_partial_run_cannot_bridge_frequency_gaps(tmp_path, missing, rejected, malformed, low_snr):
    _synthetic_run(tmp_path, status="cancelled", frequencies=[5., 10., 20., 30., 40., 60., 80.],
                   missing=missing, rejected=rejected, malformed=malformed, low_snr=low_snr)
    summary = analysis.analyze(tmp_path)
    assert summary["status"] == "completed" and summary["result_status"] == "partial"
    assert summary["acquisition_status"] == "cancelled" and summary["quality_passed"]
    assert summary["transfer_bands_hz"] == [[5., 20.], [40., 80.]]
    row = next((row for row in _csv(tmp_path, steps.TRANSFER_CSV) if float(row["frequency_hz"]) == 30), None)
    if missing:
        assert row is None
    else:
        assert row["accepted"] == "False" and row["h_v_per_rad"] == ""
        assert ("电学" if rejected else "时间轴" if malformed else "信噪比") in row["reason"]
    for row in _csv(tmp_path, steps.NOISE_CSV):
        f = float(row["frequency_hz"])
        assert bool(row["detector_y_asd_v_per_sqrt_hz"])
        assert bool(row["phase_asd_rad_per_sqrt_hz"]) is (5 <= f <= 20 or 40 <= f <= 80)


@pytest.mark.parametrize("changes, diagnostic, fragment", [
    (dict(repeat_ratio=1.40), "anchor_repeat", "对称相对差"),
    (dict(k0=2 * K0), "k0_closure", "最低频点"),
    (dict(noise_bad="electrical"), "noise", "残余 AM"),
    (dict(noise_bad="lockin"), "noise", "锁相状态"),
    (dict(noise_bad="stream"), "noise", "实际率"),
    (dict(noise_bad="monitor"), "noise", "监测帧不可读"),
    (dict(noise_bad="short"), "noise", "Welch 分辨率"),
])
def test_failed_qualification_keeps_detector_asd_only(tmp_path, changes, diagnostic, fragment):
    _synthetic_run(tmp_path, status="failed", **changes)
    summary = analysis.analyze(tmp_path)
    assert summary["acquisition_status"] == "failed" and summary["result_status"] == "partial"
    assert summary["status"] == "completed" and not summary["quality_passed"]
    assert not summary["phase_referral"]["available"]
    assert fragment in str(summary[diagnostic].get("reason") or summary[diagnostic]["reasons"])
    noise = _csv(tmp_path, steps.NOISE_CSV)
    assert noise and all(row["detector_y_asd_v_per_sqrt_hz"] and not row["phase_asd_rad_per_sqrt_hz"] for row in noise)


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
