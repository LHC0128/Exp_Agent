"""Noise_Spectrum_XY_Ctrl 0921_1758 运行的峰脊独立诊断试算；不覆盖正式结果，不修改采集 K/B。"""

from lab_workflows.common import data_root
from pathlib import Path
import sys
import json

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "params").is_dir())
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import find_peaks
from scipy.stats import theilslopes

from lab_workflows.plotting import new_figure, save_figure, set_plot_style, format_axis
from lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.global_analysis import (
    fit_global_noise_separation,
    project_global_noise_separation,
)

OUT = data_root() / "Noise_Spectrum_XY_Ctrl/0921_1758_noise/results/peak_diagnosis"
with np.load(OUT.parent / "psd_matrix.npz") as data:
    matrix, frequency, voltage = (data[key] for key in ("psd_matrix", "freq_axis", "amplitudes"))

# 仅用于候选峰定位：对数列中位数去背景，轻度平滑，并消除逐行公共偏移。
# 原始 PSD 保持不变，后续二维拟合直接使用原始数值。
log_psd = np.log(matrix)
score = gaussian_filter(log_psd - np.median(log_psd, axis=0), sigma=(1.5, 8))
score -= np.median(score, axis=1)[:, None]
trials = []
for threshold in (0.1, 0.15, 0.2, 0.25):
    rows, peaks_hz = [], []
    # 此诊断 ROI 对应用户标记的低频移动脊线，不能当作通用自动识别范围。
    for row in range(10, 190):
        peaks, props = find_peaks(score[row], prominence=threshold)
        valid = (frequency[peaks] > 300) & (frequency[peaks] < 15000)
        if np.any(valid):
            peak = peaks[valid][np.argmax(props["prominences"][valid])]
            rows.append(row)
            peaks_hz.append(frequency[peak])
    rows, peaks_hz = np.asarray(rows), np.asarray(peaks_hz)
    slope, intercept, *_ = theilslopes(peaks_hz, voltage[rows])
    residual = peaks_hz - slope * voltage[rows] - intercept
    center = np.median(residual)
    inlier = np.abs(residual - center) < max(200, 3 * 1.4826 * np.median(np.abs(residual-center)))
    slope, intercept = np.polyfit(voltage[rows[inlier]], peaks_hz[inlier], 1)
    trials.append(dict(prominence=threshold, slope_hz_per_v=float(slope), intercept_hz=float(intercept),
                       candidates=len(rows), inliers=int(inlier.sum()),
                       residual_std_hz=float(np.std(peaks_hz[inlier]-slope*voltage[rows[inlier]]-intercept))))
    if threshold == 0.2:
        selected_rows, selected_peaks, selected_inliers = rows, peaks_hz, inlier
        k, b = slope, intercept

# 诊断校准只在有峰支撑的电压区间使用，不在首尾外推。
row_mask = (voltage >= voltage[selected_rows[selected_inliers]].min()) & (voltage <= voltage[selected_rows[selected_inliers]].max())
control = k * voltage[row_mask] + b
# 试算频率每隔 4 点取一列，保持原 PSD 幅值；无需对整个频带重复昂贵优化。
core_indices = np.flatnonzero((frequency >= 2000) & (frequency <= control[-1]-500))[::4]
core = fit_global_noise_separation(matrix[row_mask][:, core_indices], frequency[core_indices], control,
                                 gamma_guess_hz=300, background_margin_hz=1500, cv_folds=5)
projection_indices = np.flatnonzero((frequency >= 500) & (frequency <= 30000))[::4]
projection = project_global_noise_separation(matrix[row_mask][:, projection_indices], frequency[projection_indices], control, core, cv_folds=5)
band = (projection.frequency_hz >= 24000) & (projection.frequency_hz <= 26000)
report = dict(source_run=OUT.parents[1].name, status="diagnostic_only_not_production_calibration",
              ridge_search_hz=[300,15000], smoothing_sigma_rows_bins=[1.5,8], threshold_trials=trials,
              control_range_hz=control[[0,-1]].tolist(), voltage_range_v=voltage[row_mask][[0,-1]].tolist(),
              global_core_frequency_range_hz=frequency[core_indices][[0,-1]].tolist(), frequency_stride=4,
              background_margin_hz=1500, gamma_hz=core.gamma_hz, offset_hz=core.control_offset_hz,
              optimizer_success=core.optimizer_success, core_median_fractional_residual=float(np.median(core.median_absolute_fractional_residual)),
              core_provisional_valid_points=int(core.quantitative_valid_mask.sum()), core_points=len(core.frequency_hz),
              band25_median_fractional_residual=float(np.median(projection.median_absolute_fractional_residual[band])),
              band25_quantitative_valid_points=int(projection.quantitative_valid_mask[band].sum()),
              limitations=["用户绿色线指定低频脊线 ROI；尚未验证通用自动峰选择", "未执行全局背景窗口敏感性与重拟合交叉验证；有效掩码仅是初筛",
                           "25 kHz 超出本次共振扫描覆盖，不可定量提取该处可控噪声", "未独立标定光学增益，仅输出 C_S_beta 乘积"])
(OUT / "diagnosis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
np.savez(OUT / "diagnostic_arrays.npz", voltage_v=voltage, ridge_score=score, selected_rows=selected_rows,
         selected_peaks_hz=selected_peaks, selected_inliers=selected_inliers, control_hz=control,
         frequency_hz=projection.frequency_hz, N_S1=projection.n_s1, C_S_beta=projection.c_s_beta,
         model=projection.model_matrix, residual=projection.residual_matrix, background_profile=core.background_profile,
         provisional_valid_mask=projection.quantitative_valid_mask)

set_plot_style("paper")
figure, axes = new_figure(nrows=2, ncols=2, figsize=(9,7))
extent=[frequency[0]/1000, frequency[-1]/1000, voltage[0], voltage[-1]]
raw=axes[0,0].imshow(np.log10(matrix), origin="lower", aspect="auto", extent=extent, cmap="inferno", vmin=-13.2, vmax=-12.7)
axes[0,0].plot((k*voltage+b)/1000, voltage, "--", color="cyan", lw=1.2, label="Diagnostic ridge")
axes[0,0].set_xlim(0,30)
axes[0,0].legend()
format_axis(axes[0,0],xlabel="PSD frequency (kHz)",ylabel="Sine peak voltage (V)")
axes[0,0].set_title("(a) Original PSD; measured voltage axis")
figure.colorbar(raw,ax=axes[0,0],label="log10 PSD (V²/Hz)")
normalized=axes[0,1].imshow(score, origin="lower", aspect="auto", extent=extent,cmap="coolwarm",vmin=-.3,vmax=.3)
axes[0,1].scatter(selected_peaks[selected_inliers]/1000,voltage[selected_rows[selected_inliers]],s=6,c="black",label="Selected ridge peaks")
axes[0,1].set_xlim(0,15)
format_axis(axes[0,1],xlabel="PSD frequency (kHz)",ylabel="Sine peak voltage (V)")
axes[0,1].set_title("(b) Background-normalized locator only")
figure.colorbar(normalized,ax=axes[0,1],label="Smoothed log contrast")
for ax, target, label in zip(axes[1],(4000,25000),("(c) Moving resonance","(d) Fixed band; outside resonance coverage")):
    j=int(np.argmin(abs(projection.frequency_hz-target)))
    ax.plot(control/1000,matrix[row_mask,projection_indices[j]],".",ms=2,label="Original PSD")
    ax.plot(control/1000,projection.model_matrix[:,j],label="Mx model trial")
    ax.plot(control/1000,projection.background_profile*projection.n_s1[j],"--",label="Background component")
    format_axis(ax,xlabel="Diagnostic control frequency (kHz)",ylabel="PSD (V²/Hz)")
    ax.set_title(label+f"\nPSD frequency = {projection.frequency_hz[j]/1000:.2f} kHz")
    ax.legend(fontsize=7)
save_figure(figure,OUT / "ridge_diagnosis.png")
print(json.dumps(report,ensure_ascii=False,indent=2))
