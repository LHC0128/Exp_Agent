"""只读已有 DirectAW 结果，生成多次运行的双面板对比图。"""

import json
import math
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml

from lab_workflows.experiment_runtime import check_cancelled, report_runtime_progress
from lab_workflows.plotting import new_figure, plot_style_context, save_figure

from .run_format import COMPARISON_FILES, comparison_block_reason, normalize_comparison_runs


def _curve(data, axis_name: str, value_name: str) -> tuple[np.ndarray, np.ndarray]:
    axis = np.asarray(data[axis_name], dtype=float)
    values = np.asarray(data[value_name], dtype=float)
    if axis.ndim != 1 or len(axis) < 2 or values.shape != axis.shape or not np.all(np.isfinite(axis)) or not np.all(np.isfinite(values)) or np.any(np.diff(axis) <= 0):
        raise ValueError(f"{axis_name}/{value_name} 数组不完整或无效")
    return axis, values


def _load_result(item: dict) -> dict:
    directory = Path(item["run_dir"]).resolve()
    config_path = directory / "experiment_config.yaml"
    if directory not in config_path.resolve().parents:
        raise ValueError("运行配置超出允许范围")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    reason = comparison_block_reason(directory, config)
    if reason:
        raise ValueError(reason)
    results = directory / "results"
    analysis = yaml.safe_load((results / "analysis.yaml").read_text(encoding="utf-8"))
    fit_success = analysis["response_fit"]["success"]
    corrected = analysis["frequency_response_correction"]["applied"]
    if not isinstance(fit_success, bool) or not isinstance(corrected, bool) or corrected and not fit_success:
        raise ValueError("拟合或频响校正状态无效，请重新分析")
    with np.load(results / "response_fit.npz", allow_pickle=False) as data:
        saved_success = data["success"]
        if saved_success.shape != () or saved_success.dtype.kind != "b" or bool(saved_success.item()) != fit_success:
            raise ValueError("响应拟合数组与分析摘要不一致")
        field, response = _curve(data, "amplitudes_nT", "demod3_x_V")
        fit_field, fit_response = _curve(data, "fit_x_nT", "fit_y_V") if fit_success else (None, None)
    with np.load(results / "sensitivity.npz", allow_pickle=False) as data:
        name = "sensitivity_corrected_fT_per_sqrtHz" if corrected else "sensitivity_raw_fT_per_sqrtHz"
        frequency, sensitivity = _curve(data, "freq_Hz", name)
        if np.any(frequency < 0) or np.any(sensitivity < 0):
            raise ValueError("频率或灵敏度不能为负数")
        positive = (frequency > 0) & (sensitivity > 0)
        if not positive.any():
            raise ValueError("灵敏度谱没有可用于对数坐标的正值")
    return {**item, "run_dir": str(directory), "field": field, "response": response,
            "fit_field": fit_field, "fit_response": fit_response, "frequency": frequency,
            "sensitivity": sensitivity, "positive": positive, "fit_success": fit_success, "corrected": corrected}


def compare_runs(runs: list[dict], output_dir: Path) -> dict:
    entries = normalize_comparison_runs(runs)
    loaded = []
    for index, item in enumerate(entries):
        check_cancelled()
        report_runtime_progress("analysis", f"读取对比结果：{item['run_id']}", 60 * index / len(entries))
        try:
            loaded.append(_load_result(item))
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise ValueError(f"{item['run_id']}：{exc}") from exc
    output = Path(output_dir).resolve()
    if any(output == Path(item["run_dir"]) or Path(item["run_dir"]) in output.parents for item in loaded):
        raise ValueError("对比输出不能写入源运行目录")
    warnings = []
    for item in loaded:
        if not item["fit_success"]:
            warnings.append(f"{item['run_id']}：色散拟合失败，使用数值斜率；灵敏度谱未作洛伦兹校正。")
        elif not item["corrected"]:
            warnings.append(f"{item['run_id']}：灵敏度谱未作洛伦兹校正。")
    result = {"runs": [{key: item[key] for key in ("run_id", "legend", "run_dir", "fit_success", "corrected")} for item in loaded],
              "warnings": warnings}
    check_cancelled()
    output.mkdir(parents=True, exist_ok=False)
    figure = None
    try:
        with plot_style_context("paper"):
            legend_rows = math.ceil(len(loaded) / 2)
            figure, axes = new_figure(nrows=2, width_mm=177.8, height_mm=130 + 4 * legend_rows, layout="constrained")
            colors = plt.get_cmap("tab10" if len(loaded) <= 10 else "tab20" if len(loaded) <= 20 else "turbo")
            for index, item in enumerate(loaded):
                check_cancelled()
                color = colors(index if len(loaded) <= 20 else index / (len(loaded) - 1))
                axes[0].plot(item["field"], item["response"], "o", color=color, label=item["legend"])
                if item["fit_success"]:
                    axes[0].plot(item["fit_field"], item["fit_response"], "--", color=color)
                mask = item["positive"]
                axes[1].loglog(item["frequency"][mask], item["sensitivity"][mask], color=color, label=item["legend"])
            axes[0].set(xlabel="RF field (nT)", ylabel="Demod3 X (V)")
            axes[1].set(xlabel="Frequency (Hz)", ylabel="Sensitivity (fT/√Hz)")
            handles, labels = axes[0].get_legend_handles_labels()
            figure.legend(handles, labels, loc="outside lower center", ncols=2)
            report_runtime_progress("analysis", "保存 RF 灵敏度对比 PNG/PDF", 80)
            check_cancelled()
            save_figure(figure, output / COMPARISON_FILES[0])
        check_cancelled()
        manifest = output / "comparison.json.tmp"
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        manifest.replace(output / "comparison.json")
    except BaseException:
        for name in (*COMPARISON_FILES, "comparison.json", "comparison.json.tmp"):
            (output / name).unlink(missing_ok=True)
        output.rmdir()
        raise
    finally:
        if figure is not None:
            plt.close(figure)
    report_runtime_progress("analysis", "对比分析完成", 100)
    return result


def main() -> None:
    compare_runs(json.loads(os.environ["LAB_TYPED_PARAMETERS"])["runs"], Path(os.environ["LAB_RUN_DIR"]))


if __name__ == "__main__":
    main()
