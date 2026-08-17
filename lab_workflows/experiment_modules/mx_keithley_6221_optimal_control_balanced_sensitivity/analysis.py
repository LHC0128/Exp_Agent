"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化离线分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from ..mx_keithley_6221_optimal_control_rf_sensitivity.analysis import (
    analyze as analyze_rf,
)
from ..mx_keithley_6221_optimal_control_xyz_balance.analysis import (
    analyze as analyze_balance,
)
from ..mx_y_rf_sensitivity.analysis_core import absolute_dispersive_response

EXPERIMENT_ID = "mx-keithley-6221-optimal-control-balanced-sensitivity"


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, (__import__("numpy").ndarray,)):
        return value.tolist()
    if isinstance(value, __import__("numpy").generic):
        return value.item()
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


def _plot_pipeline_summary(
    run_dir: Path,
    results_dir: Path,
    balance_result: dict[str, Any] | None,
    rf_result: dict[str, Any] | None,
) -> str | None:
    """汇总图：左侧平衡零点随 z 漂移，右侧 RF 幅度扫描与色散拟合。"""
    import numpy as np

    fig, axes = new_figure(
        figsize=(9.0, 3.6),
        nrows=1,
        ncols=2,
        squeeze=False,
        constrained_layout=True,
    )
    ax_left = axes[0][0]
    ax_right = axes[0][1]
    if balance_result is not None:
        linear = balance_result.get("linear_fit", {})
        per_layer = linear.get("per_layer", [])
        z_values = [layer["z_field_ma"] for layer in per_layer]
        x0_values = [
            layer["x_fit"]["s0"] if layer["x_fit"]["success"] else np.nan
            for layer in per_layer
        ]
        y0_values = [
            layer["y_fit"]["s0"] if layer["y_fit"]["success"] else np.nan
            for layer in per_layer
        ]
        ax_left.plot(
            z_values, x0_values, "o-", color=COLOR_OPTIMAL, label="X zero"
        )
        ax_left.plot(
            z_values, y0_values, "s--", color=COLOR_TRAD, label="Y zero"
        )
        workpoint = linear.get("fitted_workpoint", {})
        if workpoint.get("z_field_ma") is not None:
            ax_left.axvline(
                workpoint["z_field_ma"], color="gray", ls=":", alpha=0.6
            )
        format_axis(
            ax_left,
            xlabel="Z field setting (mA)",
            ylabel="Fitted zero (V)",
        )
        ax_left.set_title("Balance workpoint drift")
        style_legend(ax_left, fontsize=7.0)
    else:
        ax_left.set_axis_off()
    if rf_result is not None and (run_dir / "raw" / "amplitude_scan.npz").exists():
        with np.load(run_dir / "raw" / "amplitude_scan.npz") as data:
            amplitude = np.asarray(data["signed_amplitude_vpp"], dtype=float)
            r_mean = np.asarray(data["r_mean_v"], dtype=float)
        ax_right.plot(
            amplitude, r_mean, "o", color=COLOR_OPTIMAL, label="Data"
        )
        parameters = rf_result.get("response_fit", {}).get("parameters")
        if parameters:
            dense = np.linspace(float(amplitude.min()), float(amplitude.max()), 400)
            ax_right.plot(
                dense,
                absolute_dispersive_response(dense, *parameters),
                "--",
                color=COLOR_TRAD,
                label="Dispersive fit",
            )
        format_axis(
            ax_right,
            xlabel="Signed Y RF amplitude (Vpp)",
            ylabel="Demod R (V)",
        )
        slope = rf_result.get("primary_slope_v_per_vpp")
        if slope is not None:
            ax_right.text(
                0.03,
                0.08,
                f"Slope = {slope:.3g} V/Vpp",
                transform=ax_right.transAxes,
                fontsize=8,
            )
        ax_right.set_title("RF amplitude response")
        style_legend(ax_right, fontsize=7.0)
    else:
        ax_right.set_axis_off()
    filename = "pipeline_summary.png"
    save_figure(fig, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """汇总平衡阶段与 RF 灵敏度阶段的分析结果。"""
    run_dir = Path(run_dir).resolve()
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    set_plot_style("paper")
    warnings: list[str] = []
    balance_result: dict[str, Any] | None = None
    rf_result: dict[str, Any] | None = None
    files: list[str] = []

    if (run_dir / "raw" / "xyz_balance_scan.npz").exists():
        try:
            balance_result = analyze_balance(run_dir)
            files.extend(balance_result.get("files", []))
            (results_dir / "balance_analysis.yaml").write_text(
                yaml.safe_dump(
                    _builtin(balance_result), allow_unicode=True, sort_keys=False
                ),
                encoding="utf-8",
            )
            (results_dir / "balance_analysis.json").write_text(
                json.dumps(_builtin(balance_result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            files.extend(["balance_analysis.yaml", "balance_analysis.json"])
        except Exception as exc:
            warnings.append(f"平衡分析失败: {exc}")
    else:
        warnings.append("缺少 raw/xyz_balance_scan.npz，跳过平衡分析")

    if (results_dir / "phase_calibration.yaml").exists():
        try:
            # RF 子分析用严格的 from_external 构造参数模型，先把本实验
            # 专属的 BALANCE_* 键从 config 中临时过滤，分析后恢复原文。
            config_path = run_dir / "experiment_config.yaml"
            original_text = config_path.read_text(encoding="utf-8")
            config_payload = _load_yaml(config_path)
            parameters = dict(config_payload.get("parameters", {}))
            filtered = {
                key: value
                for key, value in parameters.items()
                if not str(key).startswith("BALANCE_")
            }
            config_payload["parameters"] = filtered
            config_path.write_text(
                yaml.safe_dump(config_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            try:
                rf_result = analyze_rf(run_dir)
            finally:
                config_path.write_text(original_text, encoding="utf-8")
            files.extend(rf_result.get("files", []))
            (results_dir / "rf_analysis.yaml").write_text(
                yaml.safe_dump(
                    _builtin(rf_result), allow_unicode=True, sort_keys=False
                ),
                encoding="utf-8",
            )
            (results_dir / "rf_analysis.json").write_text(
                json.dumps(_builtin(rf_result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            files.extend(["rf_analysis.yaml", "rf_analysis.json"])
        except Exception as exc:
            warnings.append(f"RF 灵敏度分析失败: {exc}")
    else:
        warnings.append("缺少 results/phase_calibration.yaml，跳过 RF 灵敏度分析")

    balance_summary: dict[str, Any] | None = None
    if balance_result is not None:
        linear = balance_result.get("linear_fit", {})
        balance_summary = {
            "measured_grid_minimum": balance_result.get("measured_grid_minimum"),
            "fitted_workpoint": linear.get("fitted_workpoint"),
            "coupling": linear.get("coupling"),
            "z_layer_fit": linear.get("z_layer_fit"),
            "next_scan_suggestion": linear.get("next_scan_suggestion"),
            "warnings": balance_result.get("warnings", []),
        }
        warnings.extend(balance_result.get("warnings", []))

    rf_summary: dict[str, Any] | None = None
    if rf_result is not None:
        rf_summary = {
            "primary_slope_v_per_vpp": rf_result.get("primary_slope_v_per_vpp"),
            "flat_median_ft_per_sqrt_hz": rf_result.get(
                "flat_median_ft_per_sqrt_hz"
            ),
            "response_fit": rf_result.get("response_fit"),
            "flat_detection": rf_result.get("flat_detection"),
            "warnings": rf_result.get("warnings", []),
        }
        warnings.extend(rf_result.get("warnings", []))

    summary = {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "success": balance_result is not None or rf_result is not None,
        "balance": balance_summary,
        "rf_sensitivity": rf_summary,
        "warnings": warnings,
        "files": sorted(set(files)),
    }
    plot_file = _plot_pipeline_summary(
        run_dir, results_dir, balance_result, rf_result
    )
    if plot_file is not None:
        summary["files"].append(plot_file)
    (results_dir / "analysis.yaml").write_text(
        yaml.safe_dump(_builtin(summary), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (results_dir / "analysis.json").write_text(
        json.dumps(_builtin(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    run_dir = runtime_run_dir()
    if run_dir is None:
        raise RuntimeError("未设置分析运行目录")
    result = analyze(run_dir)
    print(
        "平衡-灵敏度一体化分析完成: "
        f"success={result['success']}, files={len(result['files'])}"
    )
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
