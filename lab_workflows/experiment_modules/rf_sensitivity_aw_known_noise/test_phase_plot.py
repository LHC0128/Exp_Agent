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
    with warnings.catch_warnings(record=True) as notices:
        phase_plot.plot_phase_responses(tmp_path, dict.fromkeys(versions, scan))
    assert not any("Glyph" in str(notice.message) for notice in notices)
    assert len(saved[0].axes) == 3
    assert [text.get_text() for text in saved[0].legends[0].get_texts()] == list(versions)
    extent = saved[0].legends[0].get_window_extent()
    assert saved[0].bbox.contains(extent.x0, extent.y0)
    assert saved[0].bbox.contains(extent.x1, extent.y1)
    for axis, component in zip(saved[0].axes, ("x", "y", "r")):
        assert axis.get_legend_handles_labels()[1] == list(versions)
        for line in axis.lines:
            np.testing.assert_array_equal(line.get_xdata(), scan["scanned_phase_deg"])
            np.testing.assert_array_equal(line.get_ydata(), scan[component + "_mean_v"])
    for suffix in ("png", "pdf"):
        assert (tmp_path / ("z_phase_response_comparison." + suffix)).stat().st_size > 100
