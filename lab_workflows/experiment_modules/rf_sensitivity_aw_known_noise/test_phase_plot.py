"""相位响应读取、排序与版本名称图例的离线回归。"""
import warnings
import numpy as np
import pytest
from . import phase_plot


def phase_run(directory):
    (directory / "raw").mkdir()
    data = dict(scanned_phase_deg=np.array([180., 0., 270., 90.]),
        x_mean_v=np.array([1., 2., 3., 4.]), y_mean_v=np.array([5., 6., 7., 8.]),
        r_mean_v=np.array([9., 10., 11., 12.]), demod_idx=1)
    np.savez(directory / "raw/z_phase_scan.npz", **data)
    config = {"parameters": {"Z_PHASE_SCAN_START_DEG": 0., "Z_PHASE_SCAN_STOP_DEG": 270.,
        "Z_PHASE_SCAN_STEP_DEG": 90., "DEMOD3_IDX": 1}}
    return config, data


def test_phase_scan_sorts_paired_acquisition_without_changing_responses(tmp_path):
    config, _ = phase_run(tmp_path)
    scan = phase_plot.load_phase_scan(tmp_path, config)
    np.testing.assert_array_equal(scan["scanned_phase_deg"], [0, 90, 180, 270])
    np.testing.assert_array_equal(scan["x_mean_v"], [2, 4, 1, 3])
    np.testing.assert_array_equal(scan["y_mean_v"], [6, 8, 5, 7])
    np.testing.assert_array_equal(scan["r_mean_v"], [10, 12, 9, 11])


@pytest.mark.parametrize("change", ["missing", "missing_component", "partial", "non_finite", "wrong_demod"])
def test_invalid_phase_scan_rejected(tmp_path, change):
    config, data = phase_run(tmp_path)
    path = tmp_path / "raw/z_phase_scan.npz"
    if change == "missing":
        path.unlink()
    else:
        if change == "missing_component":
            data.pop("y_mean_v")
        elif change == "partial":
            data["x_mean_v"] = data["x_mean_v"][:-1]
        elif change == "non_finite":
            data["r_mean_v"][0] = np.nan
        else:
            data["demod_idx"] = 2
        np.savez(path, **data)
    with pytest.raises(ValueError, match="相位扫描"):
        phase_plot.load_phase_scan(tmp_path, config)


def test_phase_comparison_uses_version_names_and_all_recorded_components(tmp_path, monkeypatch):
    config, _ = phase_run(tmp_path)
    scan = phase_plot.load_phase_scan(tmp_path, config)
    saved = []
    original = phase_plot.save_figure
    def capture(figure, path):
        saved.append(figure)
        return original(figure, path)
    monkeypatch.setattr(phase_plot, "save_figure", capture)
    versions = ("常数控制", "默认最优控制")
    scans = dict.fromkeys(versions, scan)
    labels = ["Control 1", "Control 2"]
    with warnings.catch_warnings(record=True) as notices:
        phase_plot.plot_phase_responses(tmp_path, scans)
    assert list(scans) == list(versions)
    assert not any("Glyph" in str(notice.message) for notice in notices)
    assert len(saved[0].axes) == 3
    assert [text.get_text() for text in saved[0].legends[0].get_texts()] == labels
    extent = saved[0].legends[0].get_window_extent()
    assert saved[0].bbox.contains(extent.x0, extent.y0)
    assert saved[0].bbox.contains(extent.x1, extent.y1)
    for axis, component in zip(saved[0].axes, ("x", "y", "r")):
        assert axis.get_legend_handles_labels()[1] == labels
        for line in axis.lines:
            np.testing.assert_array_equal(line.get_xdata(), scan["scanned_phase_deg"])
            np.testing.assert_array_equal(line.get_ydata(), scan[component + "_mean_v"])
    for suffix in ("png", "pdf"):
        assert (tmp_path / ("z_phase_response_comparison." + suffix)).stat().st_size > 100


def test_shared_summary_uses_english_plot_labels_but_keeps_control_names(tmp_path, monkeypatch):
    from ..rf_sensitivity_direct_aw import control_analysis

    inputs = []
    for version in ("中文控制", "v2"):
        child = tmp_path / version
        (child / "results").mkdir(parents=True)
        np.savez(child / "results/response_fit.npz", amplitudes_nT=[-1, 1], demod3_x_V=[-2, 2])
        np.savez(child / "results/sensitivity.npz", freq_Hz=[0, 1, 2],
            sensitivity_fT_per_sqrtHz=[1, 2, 3], psd_avg_V2_per_Hz=[1, 1, 1])
        inputs.append((version, child, {}))
    (tmp_path / "results").mkdir()
    (tmp_path / "results/noise_rejection_summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(control_analysis, "control_inputs", lambda *args, **kwargs: iter(inputs))
    monkeypatch.setattr(control_analysis.shared, "sensitivity_and_plots", lambda *args, **kwargs: {})
    monkeypatch.setattr(control_analysis, "load_phase_scan", lambda *args: None)
    figures = []
    result = control_analysis.sensitivity_and_plots(tmp_path, {}, 2,
        save_plot=lambda figure, path: figures.append(figure))
    assert list(result["controls"]) == ["中文控制", "v2"]
    assert [value["plot_label"] for value in result["controls"].values()] == ["Control 1", "v2"]
    for axis in figures[0].axes:
        assert axis.get_legend_handles_labels()[1] == ["Control 1", "v2"]
