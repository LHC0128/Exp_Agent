from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.image as mpimg
import pytest

from lab_workflows.plotting import (
    A0_POSTER_STANDARD,
    DEFAULT_PROFILE,
    PAPER_STANDARD,
    figure_size,
    new_figure,
    plot_style_context,
    save_figure,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TYPED_ANALYSIS_FILES = tuple(
    sorted(
        (_REPOSITORY_ROOT / "lab_workflows" / "experiment_modules").glob(
            "*/analysis.py"
        )
    )
)


def test_default_profile_is_paper() -> None:
    assert DEFAULT_PROFILE == "paper"
    assert figure_size() == PAPER_STANDARD
    assert figure_size(profile="a0_poster") == A0_POSTER_STANDARD


def test_profiles_apply_scientific_readability_settings() -> None:
    with plot_style_context():
        assert mpl.rcParams["font.size"] == pytest.approx(8.0)
        assert mpl.rcParams["lines.linewidth"] == pytest.approx(1.3)
        assert mpl.rcParams["savefig.dpi"] == pytest.approx(300.0)
    with plot_style_context("a0_poster"):
        assert mpl.rcParams["font.size"] == pytest.approx(18.0)
        assert mpl.rcParams["axes.labelsize"] == pytest.approx(20.0)
        assert mpl.rcParams["lines.linewidth"] == pytest.approx(2.5)


def test_default_png_uses_paper_size_and_300_dpi(tmp_path) -> None:
    with plot_style_context():
        fig, ax = new_figure()
        ax.plot([0.0, 1.0], [0.0, 1.0])
        output = save_figure(fig, tmp_path / "paper.png")
    image = mpimg.imread(output)
    assert image.shape[:2] == (510, 1020)


def test_typed_analyzers_use_shared_paper_plotting_api() -> None:
    delegated_grid_analyzers = 0
    delegated_phase_analyzers = 0
    for path in _TYPED_ANALYSIS_FILES:
        source = path.read_text(encoding="utf-8")
        if "mx_y_rf_grid_analysis import" in source:
            delegated_grid_analyzers += 1
            assert "analyze_mx_y_rf_grid(" in source, path
            continue
        if "phase_plot import" in source:
            delegated_phase_analyzers += 1
            assert "plot_phase_calibration(" in source, path
            continue
        assert "plotting import" in source, path
        assert "new_figure(" in source, path
        assert "save_figure(" in source, path
        assert "matplotlib.pyplot" not in source, path
        assert "plt.subplots" not in source, path
        assert ".savefig(" not in source, path
        assert '"TkAgg"' not in source, path
    assert delegated_grid_analyzers == 2
    assert delegated_phase_analyzers == 1
    grid_source = (
        _REPOSITORY_ROOT
        / "lab_workflows"
        / "experiment_modules"
        / "mx_y_rf_grid_analysis.py"
    ).read_text(encoding="utf-8")
    assert "plotting import" in grid_source
    assert 'set_plot_style("paper")' in grid_source
    assert "new_figure(" in grid_source
    assert "save_figure(" in grid_source
    phase_source = (
        _REPOSITORY_ROOT
        / "lab_workflows"
        / "experiment_modules"
        / "mx_z_optimal_control_rf_sensitivity"
        / "phase_plot.py"
    ).read_text(encoding="utf-8")
    assert "plotting import" in phase_source
    assert 'set_plot_style("paper")' in phase_source
    assert "new_figure(" in phase_source
    assert "save_figure(" in phase_source
