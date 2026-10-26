"""只读诊断单次噪声异常与整组灵敏度偏差，不改主分析结果。"""
from datetime import datetime
import argparse
from pathlib import Path
import csv
import math
import numpy as np
from ...experiment_runtime import check_cancelled
from lab_workflows.workflow_state import write_state
from .analysis import AnalysisWorkflow
from .run_format import calibration_scope
from .analysis_steps import load_inputs, fit_dispersive_slope, load_noise_spectra, inspect_noise_spectra
from .plotting import waveform_font_family

CLASS_LABELS = {
    "outlier_driven": "少数异常记录拉高平均 PSD",
    "group_wide": "整组灵敏度较差，非个别异常",
    "mixed": "两种问题并存",
    "no_clear_problem": "未发现这两类明显问题",
    "reference_unavailable": "缺少有效斜率或标称参考，未判整组",
}


def classify_groups(groups, reference_scale=1.0, whole_group_ratio=2.0):
    """保留记录的下四分位数仍明显超过同控制标称中位数，才标记整组偏差。"""
    if not math.isfinite(reference_scale) or not math.isfinite(whole_group_ratio) or whole_group_ratio <= 1:
        raise ValueError("参考倍率须有限，整组判定倍数必须大于 1")
    references = {row["control"]: row.get("kept_median_record_fT_per_sqrtHz")
                  for row in groups if np.isclose(row["scale"], reference_scale, rtol=0, atol=1e-9)}
    for row in groups:
        reference = references.get(row["control"])
        q25 = row.get("kept_q25_record_fT_per_sqrtHz")
        assessed = reference is not None and reference > 0 and q25 is not None
        poor = assessed and q25 > whole_group_ratio * reference
        outliers = row["n_rejected"] > 0
        category = ("mixed" if poor and outliers else "outlier_driven" if outliers else
                    "group_wide" if poor else "no_clear_problem" if assessed else "reference_unavailable")
        row.update(classification=category, classification_label=CLASS_LABELS[category],
            whole_group_assessed=assessed, group_wide_poor=poor, reference_scale=reference_scale,
            reference_median_fT_per_sqrtHz=reference,
            relative_kept_median=row["kept_median_record_fT_per_sqrtHz"] / reference if assessed else None)
    return groups


def _write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _plot_records(output, groups, band, whole_group_ratio):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from ...plotting import plot_style_context, save_figure
    columns = min(3, len(groups))
    rows = (len(groups) + columns - 1) // columns
    with plot_style_context("paper"):
        figure, axes = plt.subplots(rows, columns, figsize=(3.8 * columns, 2.6 * rows),
                                    squeeze=False, layout="constrained")
        for axis, group in zip(axes.flat, groups):
            calibrated = group["record_sensitivity_fT_per_sqrtHz"] is not None
            scores = np.asarray(group["record_sensitivity_fT_per_sqrtHz"] if calibrated
                                else group["record_asd_V_per_sqrtHz"])
            keep = np.asarray(group["accepted_mask"], dtype=bool)
            numbers = np.arange(len(scores)) + 1
            threshold = group["threshold_fT_per_sqrtHz"] if calibrated else group["threshold_asd_V_per_sqrtHz"]
            axis.scatter(numbers[keep], scores[keep], s=8, color="C0")
            axis.scatter(numbers[~keep], scores[~keep], s=22, color="C3", marker="x")
            axis.axhline(threshold, color="C3", linestyle="--", linewidth=.7)
            if calibrated and group["whole_group_assessed"]:
                axis.axhline(whole_group_ratio * group["reference_median_fT_per_sqrtHz"],
                             color="C2", linestyle=":", linewidth=.8)
            if np.all(scores > 0):
                axis.set_yscale("log")
            axis.set(xlabel="Record number (1-based)",
                     ylabel="Record median NEF (fT/√Hz)" if calibrated else "Record median ASD (V/√Hz)")
            axis.set_title(f"{group['control']} · Scale {group['scale']:g}\n"
                           f"{group['classification']}; rejected {group['n_rejected']}/{group['n_records']}",
                           fontfamily=waveform_font_family(), fontsize=8)
            axis.grid(alpha=.2)
        for axis in list(axes.flat)[len(groups):]:
            axis.set_visible(False)
        figure.suptitle(f"Noise record diagnostics: {band[0]:g}-{band[1]:g} Hz", fontsize=12)
        figure.legend(handles=[
            Line2D([], [], marker="o", linestyle="", color="C0", label="Kept"),
            Line2D([], [], marker="x", linestyle="", color="C3", label="High outlier"),
            Line2D([], [], linestyle="--", color="C3", label="Upper outlier threshold"),
            Line2D([], [], linestyle=":", color="C2", label=f"{whole_group_ratio:g}x nominal reference"),
        ], loc="outside lower center", ncol=4, fontsize=8)
        for suffix in ("png", "pdf"):
            save_figure(figure, output / f"noise_record_diagnostics.{suffix}", dpi=200)
        plt.close(figure)


def diagnose(run_dir, output_dir=None, *, reference_scale=1.0, whole_group_ratio=2.0,
             band_min_hz=None, band_max_hz=None, nperseg=None):
    """从 raw 重算，默认输出独立时间戳目录；不会执行采集或改写主结果。"""
    run = Path(run_dir).resolve()
    overrides = {}
    for key, value in (("SENSITIVITY_BAND_MIN_HZ", band_min_hz),
                       ("SENSITIVITY_BAND_MAX_HZ", band_max_hz), ("NOISE_NPERSEG", nperseg)):
        if value is not None:
            overrides[key] = value
    ctx = AnalysisWorkflow(run, overrides)
    if not ctx.config["parameters"]["INCLUDE_NOISE"]:
        raise ValueError("该运行未采集噪声，无法做记录诊断")
    # 在创建输出前校验分类选项和完整采集资格。
    classify_groups([], reference_scale, whole_group_ratio)
    points = load_inputs(run, ctx.config)
    output = Path(output_dir).resolve() if output_dir else run / "results/noise_record_diagnostics" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if output == run or output in run.parents or (output.is_relative_to(run) and output.relative_to(run).parts[0] != "results"):
        raise ValueError("输出必须位于 results 或运行目录之外，不能写入原始数据")
    if output.exists():
        raise ValueError("诊断输出目录已存在，请使用新目录")
    output.mkdir(parents=True)
    low, high = (ctx.options[key] for key in ("band_min_hz", "band_max_hz"))
    groups, records = [], []
    for record, path, child in points:
        check_cancelled()
        fit_error = ""
        try:
            with np.load(path / "raw/response_data.npz", allow_pickle=False) as data:
                fit, _ = fit_dispersive_slope(data["amplitudes_nT"], data["demod3_x_V"])
            slope = fit["slope_V_per_nT"]
        except ValueError as exc:
            slope, fit_error = None, str(exc)
        frequency, matrix, rates, files = load_noise_spectra(path, child["parameters"], ctx.options["noise_nperseg"])
        keep, scores, original, cleaned, quality = inspect_noise_spectra(frequency, matrix, low, high, "mad_high")
        mask = (frequency >= low) & (frequency <= high)
        factor = 1e6 / slope if slope else None
        calibrated = scores * factor if factor else None
        row = dict(control=record["control"], scale=record["scale"], path=record["path"],
            n_records=len(scores), n_rejected=int((~keep).sum()), n_accepted=int(keep.sum()),
            rejected_records=[name for name, ok in zip(files, keep) if not ok],
            slope_V_per_nT=slope, fit_error=fit_error,
            kept_median_record_fT_per_sqrtHz=float(np.median(calibrated[keep])) if factor else None,
            kept_q25_record_fT_per_sqrtHz=float(np.percentile(calibrated[keep], 25)) if factor else None,
            before_sensitivity_fT_per_sqrtHz=float(np.median(np.sqrt(original[mask])) * factor) if factor else None,
            after_sensitivity_fT_per_sqrtHz=float(np.median(np.sqrt(cleaned[mask])) * factor) if factor else None,
            record_asd_V_per_sqrtHz=scores.tolist(),
            record_sensitivity_fT_per_sqrtHz=calibrated.tolist() if factor else None,
            accepted_mask=keep.tolist(), record_filenames=files, actual_rates_Sa_s=rates,
            threshold_fT_per_sqrtHz=quality["threshold_asd_V_per_sqrtHz"] * factor if factor else None, **quality)
        groups.append(row)
        for i, (name, score, ok) in enumerate(zip(files, scores, keep)):
            records.append(dict(control=record["control"], scale=record["scale"], record_number=i + 1,
                filename=name, accepted=bool(ok), band_median_asd_V_per_sqrtHz=float(score),
                band_median_sensitivity_fT_per_sqrtHz=float(score * factor) if factor else None))
    classify_groups(groups, reference_scale, whole_group_ratio)
    report = dict(run_dir=str(run), output_dir=str(output), comparison_band_Hz=[low, high],
        phase_calibration_scope=calibration_scope(ctx.config),
        reference_scale=reference_scale, whole_group_ratio=whole_group_ratio,
        total_groups=len(groups), total_records=len(records),
        rejected_records=sum(group["n_rejected"] for group in groups), groups=groups,
        note="仅诊断建议，不修改 raw、采集配置、analysis_state 或主分析结果。整组较差只比较同控制标称参考；可能来自斜率下降，不能据此认定电压噪声一定增加。")
    write_state(output / "diagnostics.json", report)
    _write_csv(output / "group_summary.csv", groups, [
        "control", "scale", "classification", "classification_label", "n_records", "n_rejected",
        "kept_median_record_fT_per_sqrtHz", "kept_q25_record_fT_per_sqrtHz",
        "reference_median_fT_per_sqrtHz", "relative_kept_median",
        "before_sensitivity_fT_per_sqrtHz", "after_sensitivity_fT_per_sqrtHz", "rejected_records"])
    _write_csv(output / "record_scores.csv", records, list(records[0]))
    def number(value):
        return "—" if value is None else f"{value:.3f}"
    lines = ["# 噪声记录诊断", "", report["note"], "",
        f"- 公共频带：{low:g}–{high:g} Hz；参考 Scale：{reference_scale:g}；校相策略：{report['phase_calibration_scope']}。",
        "- 单次高值规则：至少 5 条，且同时超过组内中位数 3 倍和中位数 + 6 × 1.4826 × MAD。",
        f"- 整组较差规则：保留记录的下四分位数仍超过同控制参考中位数的 {whole_group_ratio:g} 倍。",
        "", "| 控制 | Scale | 分类 | 建议剔除/总条数 | 原指标 | 剔除后指标 |",
        "|---|---:|---|---:|---:|---:|"]
    for group in groups:
        lines.append(f"| {group['control']} | {group['scale']:g} | {group['classification_label']} | "
                     f"{group['n_rejected']}/{group['n_records']} | {number(group['before_sensitivity_fT_per_sqrtHz'])} | "
                     f"{number(group['after_sensitivity_fT_per_sqrtHz'])} |")
    lines.extend(["", "灵敏度单位均为 fT/√Hz；指标是频带内斜率折算 ASD 的中位数。整组较差不自动删除。",
                  "记录编号从 1 开始；文件名后缀仍按采集时的 0 起始索引。"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _plot_records(output, groups, (low, high), whole_group_ratio)
    return report


def main():
    parser = argparse.ArgumentParser(description="只读诊断单次高值异常和整组灵敏度偏差，不改主结果")
    parser.add_argument("run_dir", type=Path, help="包含 experiment_config.yaml 的已完成运行目录")
    parser.add_argument("--output", type=Path, help="新的独立输出目录，默认位于本次运行 results 下")
    parser.add_argument("--reference-scale", type=float, default=1.0, help="同控制的参考倍率")
    parser.add_argument("--whole-group-ratio", type=float, default=2.0, help="保留记录下四分位数相对参考中位数的判定倍数")
    parser.add_argument("--band-min-hz", type=float, help="覆盖本次诊断频带下限，不改采集参数")
    parser.add_argument("--band-max-hz", type=float, help="覆盖本次诊断频带上限，不改采集参数")
    parser.add_argument("--nperseg", type=int, help="覆盖本次诊断 Welch 段长")
    args = parser.parse_args()
    report = diagnose(args.run_dir, args.output, reference_scale=args.reference_scale,
        whole_group_ratio=args.whole_group_ratio, band_min_hz=args.band_min_hz,
        band_max_hz=args.band_max_hz, nperseg=args.nperseg)
    print("诊断报告：" + report["output_dir"])
    print(f"共 {report['total_groups']} 组 / {report['total_records']} 条记录；建议剔除 {report['rejected_records']} 条。")
    for group in report["groups"]:
        print(f"{group['control']} · Scale {group['scale']:g}：{group['classification_label']}，"
              f"建议剔除 {group['n_rejected']}/{group['n_records']} 条")


if __name__ == "__main__":
    main()
