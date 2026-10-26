"""分步分析与完整分析一致，缓存、覆盖值和归档不污染采集快照。"""
import numpy as np
import pytest
import yaml
from .analysis import AnalysisWorkflow, analyze
from .test_analysis import synthetic_run


REGRESSION_CASES = {
    "rf-sensitivity-aw": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/rf_sensitivity_direct_aw/analysis.py"
            ],
            "tests": [
                "test_full_and_staged_analysis_match_and_options_are_isolated",
                "test_input_or_product_drift_rejects_stale_prerequisites",
                "test_invalid_analysis_overrides_are_rejected"
            ]
        }
    }
}


def test_full_and_staged_analysis_match_and_options_are_isolated(tmp_path):
    full, staged = tmp_path / "full", tmp_path / "staged"
    synthetic_run(full)
    original, _ = synthetic_run(staged)
    expected = analyze(full)
    ctx = AnalysisWorkflow(staged)
    ctx.execute(["B01", "B02"])
    ctx = AnalysisWorkflow(staged)
    result = ctx.execute(["B03", "B04"])
    assert result["response_fit"] == expected["response_fit"]
    with np.load(full / "results/sensitivity.npz") as first, np.load(staged / "results/sensitivity.npz") as second:
        assert np.allclose(first["sensitivity_fT_per_sqrtHz"], second["sensitivity_fT_per_sqrtHz"])
    fit = (staged / "results/response_fit.npz").read_bytes()
    changed = AnalysisWorkflow(staged, {"NOISE_NPERSEG": 600})
    assert changed.states["B02"]["status"] == "completed"
    assert changed.states["B03"]["status"] == "stale"
    changed.execute(["B03", "B04"])
    assert (staged / "results/response_fit.npz").read_bytes() == fit
    assert list((staged / "results").glob("before_*/psd_avg.npz"))
    config = yaml.safe_load((staged / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["parameters"] == original["parameters"]
    assert AnalysisWorkflow(staged).options["noise_nperseg"] == 600


def test_input_or_product_drift_rejects_stale_prerequisites(tmp_path):
    synthetic_run(tmp_path)
    ctx = AnalysisWorkflow(tmp_path)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])
    ctx.execute(["B01", "B02"])
    (tmp_path / "results/response_fit.npz").unlink()
    changed = AnalysisWorkflow(tmp_path)
    assert changed.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        changed.execute(["B03"])
    (tmp_path / "raw/noise_D3_0000.npz").write_bytes(b"changed")
    changed = AnalysisWorkflow(tmp_path)
    assert changed.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="刷新"):
        changed.execute(["B01"], expected_revision=ctx.revision)


@pytest.mark.parametrize("value", [True, 1, 2.5, float("nan"), float("inf")])
def test_invalid_analysis_overrides_are_rejected(tmp_path, value):
    synthetic_run(tmp_path)
    with pytest.raises(ValueError, match="Welch"):
        AnalysisWorkflow(tmp_path, {"NOISE_NPERSEG": value})
