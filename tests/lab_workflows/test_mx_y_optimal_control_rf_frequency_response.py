from pathlib import Path
import numpy as np
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.models import MxYOptimalControlRFFrequencyResponseParams
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.workflow import build_frequency_axis, build_phase_axis
from lab_workflows.experiment_modules.mx_y_optimal_control_rf_frequency_response.analysis import analyze

def test_defaults_axes_and_validation():
    params = MxYOptimalControlRFFrequencyResponseParams()
    assert params.validate() == []
    assert build_frequency_axis(params).size == 100
    assert build_frequency_axis(params)[0] == 100
    assert build_frequency_axis(params)[-1] == 10000
    assert np.allclose(build_phase_axis(params), np.arange(0, 360, 10))

def test_observation_analysis_only_writes_plots(tmp_path: Path):
    raw = tmp_path / "raw"; raw.mkdir()
    frequency = np.linspace(100, 10000, 3); phase = np.arange(0, 360, 10)
    shape = (frequency.size, phase.size)
    kwargs = {"r_mean_v": np.ones(shape), "r_std_v": np.full(shape, 1e-4)}
    np.savez(raw / "phase_frequency_scan.npz", frequency_hz=frequency, phase_deg=phase, accepted_attempt_index=np.zeros(shape, int), accepted_file=np.full(shape, "x.npz", object), **kwargs)
    result = analyze(tmp_path)
    assert result["success"]
    assert (tmp_path / "results" / "r_phase_frequency.png").is_file()
    assert (tmp_path / "results" / "analysis.yaml").is_file()
    assert result["acquisition_signals"] == ["Demod0 R"]
    assert "fit" not in result
    assert "bandwidth" not in result
