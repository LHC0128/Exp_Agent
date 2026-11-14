"""离线算法与分阶段分析：小数组与假仪器生成的真实运行目录。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from . import analysis, processing, workflow
from .test_workflow import TAU, copy_config, install, Rig, small_params

REGRESSION_CASES = {
    "coil-drive-characterization": {"analysis": {
        "sources": ["lab_workflows/experiment_modules/coil_drive_characterization/processing.py",
                    "lab_workflows/experiment_modules/coil_drive_characterization/analysis_steps.py",
                    "lab_workflows/experiment_modules/coil_drive_characterization/analysis.py",
                    "experiments/Coil_Drive_Characterization_plot.py"],
        "tests": ["test_area_tolerance_override_only_reruns_b02", "test_step_metrics_first_order"],
    }},
}


def test_fit_sine_recovers_amplitude_and_phase():
    t = np.linspace(0, 1e-2, 5000)
    fit = processing.fit_sine(t, 0.3 * np.cos(2 * math.pi * 1e3 * t - 0.4) + 0.01, 1e3)
    assert fit.amplitude == pytest.approx(0.3)
    assert fit.phase_rad == pytest.approx(-0.4)
    assert fit.quality == pytest.approx(1.0)


def test_step_metrics_first_order():
    t = np.linspace(-50e-6, 150e-6, 20001)
    width = 50e-6
    current = np.where(t < 0, 0, np.where(t < width, 1 - np.exp(-t / TAU),
                                          (1 - math.exp(-width / TAU)) * np.exp(-(t - width) / TAU)))
    metrics = processing.step_metrics(t, 0.1 * current, width)
    assert metrics.rise_10_90_s == pytest.approx(TAU * math.log(9), rel=0.01)
    assert metrics.fall_90_10_s == pytest.approx(TAU * math.log(9), rel=0.01)
    assert metrics.area_a_s == pytest.approx(0.1 * width, rel=0.005)  # 线性 RL 含拖尾面积守恒


def test_step_metrics_flags_truncated_tail():
    """记录窗在脉冲尾部之前结束：抛 TruncatedPulse，由分析跳过该帧而不是中止整段。"""
    t = np.linspace(-2.5e-6, 2.5e-6, 5001)
    width = 1e-6
    current = np.where(t < 0, 0, np.where(t < width, 1 - np.exp(-t / TAU),
                                          (1 - math.exp(-width / TAU)) * np.exp(-(t - width) / TAU)))
    with pytest.raises(processing.TruncatedPulse, match="衰减尾部"):
        processing.step_metrics(t, 0.1 * current, width)


def test_truncated_pulse_frame_is_skipped_not_fatal(tmp_path, monkeypatch):
    """短脉宽窗口被截断时该帧记入 pulse_skipped，其余帧照常出结果。"""
    copy_config(tmp_path)
    install(monkeypatch, Rig())
    params = small_params(pulse_widths_us=[1.0, 20.0, 100.0], pulse_min_window_us=1.0)
    directory = workflow.run(params, project_root=tmp_path)
    summary = analysis.analyze(directory)
    assert summary["status"] == "completed"
    assert [item["width_s"] for item in summary["pulse_skipped"]] == [1e-6]
    assert summary["shortest_usable_pulse_width_s"] is not None


def test_area_tolerance_override_only_reruns_b02(tmp_path, monkeypatch):
    copy_config(tmp_path)
    install(monkeypatch, Rig())
    directory = workflow.run(small_params(), project_root=tmp_path)
    first = analysis.analyze(directory)
    assert first["shortest_usable_pulse_width_s"] == pytest.approx(20e-6)
    ctx = analysis.AnalysisWorkflow(directory, {"AREA_TOLERANCE": 0.1})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    looser = ctx.execute(["B02"])
    # 5 µs = 2.5τ，峰值约 0.918：容差 0.1 时也可用
    assert looser["shortest_usable_pulse_width_s"] == pytest.approx(5e-6)
    assert any(path.name.startswith("before_") for path in (directory / "results").iterdir())
    with pytest.raises(ValueError, match="AREA_TOLERANCE"):
        analysis.AnalysisWorkflow(directory, {"AREA_TOLERANCE": 0.9})
