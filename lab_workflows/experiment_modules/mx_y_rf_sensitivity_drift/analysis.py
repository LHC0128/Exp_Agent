"""Mx Y RF 灵敏度长飘趋势分析。"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
import numpy as np
import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_GRAY,
    COLOR_GREEN,
    COLOR_OPTIMAL,
    COLOR_ORANGE,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)


FIELDNAMES = (
    "cycle_index",
    "scheduled_offset_s",
    "actual_elapsed_s",
    "actual_start_utc",
    "start_lag_s",
    "child_run_dir",
    "status",
    "valid",
    "sensitivity_flat_median_ft_per_sqrt_hz",
    "zero_point_sensitivity_flat_median_ft_per_sqrt_hz",
    "linewidth_hwhm_hz",
    "linewidth_kind",
    "primary_slope_v_per_vpp",
    "zero_point_slope_v_per_vpp",
    "response_fit_r_squared",
    "excluded_point_count",
    "flat_band_low_hz",
    "flat_band_high_hz",
    "warnings",
    "error",
)

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _manifest(run_dir: Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "raw" / "drift_manifest.yaml"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    return list(payload.get("records", []))


def _metric(result: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    value: Any = result
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _row(record: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    result = record.get("analysis") or {}
    child = Path(record["child_run_dir"]) if record.get("child_run_dir") else None
    if child and not child.is_absolute():
        child = (run_dir / child).resolve()
    if child and not result:
        path = child / "results" / "analysis.json"
        if path.exists():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = {}
    flat_band = result.get("flat_band_hz") or [None, None]
    fit_r2 = _metric(result, ("response_fit", "r_squared"))
    row = {
        "cycle_index": record.get("cycle_index"),
        "scheduled_offset_s": record.get("scheduled_offset_s"),
        "actual_elapsed_s": (
            float(record.get("scheduled_offset_s") or 0.0)
            + float(record.get("start_lag_s") or 0.0)
        ),
        "actual_start_utc": record.get("actual_start_utc"),
        "start_lag_s": record.get("start_lag_s"),
        "child_run_dir": str(child) if child else None,
        "status": record.get("status"),
        "valid": bool(
            result
            and result.get("success")
            and _metric(result, ("flat_detection", "success"), False)
            and _metric(result, ("linewidth", "hwhm_hz")) is not None
        ),
        "sensitivity_flat_median_ft_per_sqrt_hz": result.get(
            "flat_median_ft_per_sqrt_hz"
        ),
        "zero_point_sensitivity_flat_median_ft_per_sqrt_hz": _metric(
            result,
            ("zero_point_method", "flat_median_ft_per_sqrt_hz"),
        ),
        "linewidth_hwhm_hz": _metric(result, ("linewidth", "hwhm_hz")),
        "linewidth_kind": _metric(result, ("linewidth", "linewidth_kind")),
        "primary_slope_v_per_vpp": result.get("primary_slope_v_per_vpp"),
        "zero_point_slope_v_per_vpp": _metric(
            result,
            ("zero_point_method", "slope_v_per_vpp"),
        ),
        "response_fit_r_squared": fit_r2,
        "excluded_point_count": _metric(result, ("response_fit", "excluded_point_count")),
        "flat_band_low_hz": flat_band[0] if len(flat_band) > 0 else None,
        "flat_band_high_hz": flat_band[1] if len(flat_band) > 1 else None,
        "warnings": " | ".join(result.get("warnings", [])) if result else "",
        "error": record.get("error"),
    }
    return {key: _builtin(row.get(key)) for key in FIELDNAMES}


def _write_table(results_dir: Path, rows: list[dict[str, Any]]) -> None:
    csv_path = results_dir / "drift_summary.csv"
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(csv_path)
    json_path = results_dir / "drift_summary.json"
    json_tmp = json_path.with_suffix(".json.tmp")
    json_tmp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    json_tmp.replace(json_path)


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


def refresh_trend(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    rows = [_row(record, run_dir) for record in _manifest(run_dir)]
    _write_table(results_dir, rows)
    plot = _plot(results_dir, rows)
    clock_plot = _plot_clock_time(results_dir, rows)
    payload = {
        "success": True,
        "run_dir": str(run_dir),
        "cycle_count": len(rows),
        "rows": rows,
        "files": [
            "drift_summary.csv",
            "drift_summary.json",
            plot,
            clock_plot,
        ],
        "plot_profile": "paper",
    }
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(payload), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def analyze(run_dir: Path) -> dict[str, Any]:
    return refresh_trend(run_dir)


def main() -> int:
    result = analyze(runtime_run_dir())
    print(f"Mx Y RF 灵敏度长飘分析完成: {result['run_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
