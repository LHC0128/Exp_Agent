"""T2 数值、旧格式、质量失败和真实离线入口回归。"""

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from . import analysis


REGRESSION_CASES = {
    "t2-calibration": {"analysis": {
        "sources": ["lab_workflows/experiment_modules/t2_calibration/analysis.py",
                    "lab_workflows/experiment_modules/t2_calibration/__init__.py"],
        "tests": ["test_single_science_and_real_explicit_cli", "test_power_scan_science_excludes_undecayed_point",
                  "test_fit_rejects_no_decay_and_invalid_time", "test_lazy_definition_preserves_public_exports"],
    }},
}


def prepare_run(tmp_path):
    directory = tmp_path / "legacy_t2"
    (directory / "raw").mkdir(parents=True)
    (directory / "experiment_config.yaml").write_text(
        "experiment_id: t2-calibration\nschema_version: 1\n", encoding="utf-8")
    return directory, np.arange(4000, dtype=float) / 400000


def fingerprint(directory):
    return {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in [directory / "experiment_config.yaml", *(directory / "raw").rglob("*")] if path.is_file()}


def test_single_science_and_real_explicit_cli(tmp_path):
    directory, time = prepare_run(tmp_path)
    waveform = 0.2 * np.exp(-time / 0.002) * np.cos(2 * np.pi * 90000 * time + 0.1) + 0.01
    np.savez(directory / "raw/fid_waveforms.npz", time=time, avg_waveform=waveform,
             waveforms=[waveform, waveform], rf_gate_freq=90000.0, trigger_slope="FALLing", burst_duration=0.01)
    before = fingerprint(directory)
    result = analysis.analyze(directory)
    assert result["fit_success"]
    assert result["T2_ms"] == pytest.approx(2.0, rel=1e-3)
    assert result["f_L_fit_Hz"] == pytest.approx(90000, rel=1e-5)
    for artifact in result["artifacts"]:
        assert Path(artifact).stat().st_size > 0
        Path(artifact).unlink()
    environment = os.environ.copy()
    environment.update(LAB_RUN_DIR=str(directory), MPLBACKEND="Agg")
    environment.pop("LAB_CANCEL_FILE", None)
    outside = tmp_path / "outside"
    outside.mkdir()
    command = [sys.executable, "-X", "utf8", "-m", "lab_workflows.experiment_modules.t2_calibration.analysis"]
    completed = subprocess.run(command, cwd=outside, env=environment, capture_output=True, text=True,
                               encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    saved = yaml.safe_load((directory / "results/T2_results.yaml").read_text(encoding="utf-8"))
    assert saved["fit_success"] and saved["T2_ms"] == pytest.approx(2.0, rel=1e-3)
    assert (directory / "results/T2_fid_analysis.png").stat().st_size > 0
    assert fingerprint(directory) == before
    environment.pop("LAB_RUN_DIR")
    rejected = subprocess.run(command, cwd=outside, env=environment, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
    assert rejected.returncode != 0 and "LAB_RUN_DIR" in rejected.stderr
    assert not list(outside.rglob("results"))


def test_power_scan_science_excludes_undecayed_point(tmp_path):
    directory, time = prepare_run(tmp_path)
    powers = np.arange(1, 6, dtype=float) * 0.05
    expected_ms = 1 / (0.5 + 2 * powers)
    waveforms = np.array([0.2 * np.exp(-time / (t2_ms / 1000)) * np.cos(2 * np.pi * 90000 * time)
                          for t2_ms in expected_ms])
    waveforms[-1] = 0.2 * np.cos(2 * np.pi * 90000 * time)
    np.savez(directory / "raw/power_scan_waveforms.npz", t_axis=time, avg_stack=waveforms, probe_power=powers)
    np.savez(directory / "raw/fid_p00.npz", rf_gate_freq=90000.0)
    # 抢救帧与正常分析入口隔离，不得把量程重试或未完成帧当成平均 FID。
    recovery = directory / "raw/recovery/p00"
    recovery.mkdir(parents=True)
    np.savez(recovery / "frame_r000_a0.npz", waveform_v=np.ones(10), time_s=np.arange(10))
    before = fingerprint(directory)
    result = analysis.analyze(directory)
    np.testing.assert_allclose(result["T2_ms"][:-1], expected_ms[:-1], rtol=1e-3)
    assert not result["fit_success"][-1] and np.isnan(result["T2_ms"][-1])
    assert result["fit_reason"][-1] == "no_measurable_decay"
    assert result["T2_0_ms"] == pytest.approx(2.0, rel=1e-3)
    assert result["alpha"] == pytest.approx(2.0, rel=1e-3)
    assert fingerprint(directory) == before
    assert all(Path(path).stat().st_size > 0 for path in result["artifacts"])


def test_fit_rejects_no_decay_and_invalid_time():
    time = np.arange(4000, dtype=float) / 400000
    result = analysis.fit_t2_diagnostics(time, np.cos(2 * np.pi * 90000 * time))
    assert not result.success and result.reason == "no_measurable_decay" and np.isnan(result.t2_ms)
    with pytest.raises(ValueError, match="严格递增"):
        analysis.fit_t2_diagnostics(np.zeros_like(time), np.ones_like(time))


def test_lazy_definition_preserves_public_exports():
    from . import definition
    from .. import t2_calibration as package
    from .models import T2CalibrationParams
    assert package.ADAPTER is definition.ADAPTER
    assert package.DEFINITION is definition.DEFINITION
    assert package.T2CalibrationParams is T2CalibrationParams
    with pytest.raises(AttributeError):
        getattr(package, "unknown_export")
