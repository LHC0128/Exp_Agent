"""原灵敏度、幅度等效线宽与拟合质量趋势图及报告。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
import numpy as np
import yaml

from ...plotting import (COLOR_GRAY, COLOR_GREEN, COLOR_OPTIMAL, COLOR_ORANGE,
    format_axis, new_figure, save_figure, set_plot_style, style_legend)
from lab_workflows.workflow_state import write_state
from .analysis_steps import contained, plain

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPORT_FILES = ("drift_trend.png", "drift_trend_clock_time.png", "analysis.yaml", "analysis.json")


def _plot_trend(
    results_dir: Path,
    rows: list[dict[str, Any]],
    x: np.ndarray,
    *,
    xlabel: str,
    filename: str,
    clock_axis: bool = False,
) -> str:
    set_plot_style("paper")
    valid = np.asarray([bool(row["valid"]) for row in rows], dtype=bool)

    def values(key: str) -> np.ndarray:
        return np.asarray(
            [
                float(row[key]) if row[key] is not None else np.nan
                for row in rows
            ]
        )

    sensitivity = values("sensitivity_flat_median_ft_per_sqrt_hz")
    zero_point = values("zero_point_sensitivity_flat_median_ft_per_sqrt_hz")
    linewidth = values("linewidth_hwhm_hz")
    r_squared = values("response_fit_r_squared")
    fig, axes = new_figure(
        (6.4, 8.2),
        3,
        1,
        height_ratios=[1.0, 1.0, 1.0],
    )
    ax = axes[0]
    ax.plot(
        x[valid],
        sensitivity[valid],
        "o-",
        color=COLOR_OPTIMAL,
        label="Sensitivity",
    )
    ax.plot(
        x[valid],
        zero_point[valid],
        "s--",
        color=COLOR_GREEN,
        label="Zero-point sensitivity",
    )
    format_axis(ax, xlabel=xlabel, ylabel="Sensitivity (fT/√Hz)")
    style_legend(ax)
    ax = axes[1]
    ax.plot(
        x[valid],
        linewidth[valid],
        "o-",
        color=COLOR_ORANGE,
        label="Amplitude-equivalent HWHM",
    )
    format_axis(ax, xlabel=xlabel, ylabel="HWHM (Hz)")
    style_legend(ax)
    ax = axes[2]
    ax.plot(
        x[valid],
        r_squared[valid],
        "o-",
        color=COLOR_GRAY,
        label="Response fit R²",
    )
    if np.any(~valid):
        ax.scatter(
            x[~valid],
            np.zeros(np.count_nonzero(~valid)),
            marker="x",
            color=COLOR_ORANGE,
            label="Invalid cycle",
        )
    ax.set_ylim(0.0, 1.05)
    format_axis(ax, xlabel=xlabel, ylabel="Fit quality (R²)")
    style_legend(ax)
    if clock_axis:
        for axis in axes:
            locator = AutoDateLocator(tz=LOCAL_TIMEZONE)
            axis.xaxis.set_major_locator(locator)
            axis.xaxis.set_major_formatter(
                ConciseDateFormatter(locator, tz=LOCAL_TIMEZONE)
            )
        fig.autofmt_xdate(rotation=20, ha="right")
    save_figure(fig, results_dir / filename)
    return filename


def _plot(results_dir: Path, rows: list[dict[str, Any]]) -> str:
    x = np.asarray(
        [float(row["actual_elapsed_s"] or 0.0) / 3600.0 for row in rows]
    )
    return _plot_trend(
        results_dir,
        rows,
        x,
        xlabel="Elapsed time (h)",
        filename="drift_trend.png",
    )


def _parse_measurement_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=LOCAL_TIMEZONE)
    return timestamp.astimezone(LOCAL_TIMEZONE)


def _plot_clock_time(results_dir: Path, rows: list[dict[str, Any]]) -> str:
    timed_rows: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    for row in rows:
        timestamp = _parse_measurement_time(row.get("actual_start_utc"))
        if timestamp is None:
            continue
        timed_rows.append(row)
        timestamps.append(timestamp)
    return _plot_trend(
        results_dir,
        timed_rows,
        np.asarray(timestamps, dtype=object),
        xlabel="Measurement time (Asia/Shanghai)",
        filename="drift_trend_clock_time.png",
        clock_axis=True,
    )


def report(run_dir, rows, diagnostics, *, status="completed"):
    results = run_dir / "results"
    for name in REPORT_FILES:
        contained(results / name, run_dir)
    plot = _plot(results, rows)
    clock_plot = _plot_clock_time(results, rows)
    payload = dict(success=True, experiment_id="mx-y-rf-sensitivity-drift", run_dir=str(run_dir),
        status=status, rows=plain(rows), **diagnostics,
        files=["drift_summary.csv", "drift_summary.json", plot, clock_plot], plot_profile="paper")
    temporary = results / "analysis.yaml.tmp"
    temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(results / "analysis.yaml")
    write_state(results / "analysis.json", payload)
    return payload
