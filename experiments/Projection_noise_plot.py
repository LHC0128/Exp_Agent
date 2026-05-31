# %% [markdown] Cell 0
# # 投影噪声标定 — 数据分析 & 可视化
#
# 加载两阶段 PSD 数据，计算 kappa2、PNL、洛伦兹拟合、噪声预算。
# 无需连接任何仪器。

# %% Cell 1
from pathlib import Path
import sys
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path: sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from scipy import signal as scipy_signal
from scipy.ndimage import binary_dilation

print("导入完成（离线分析模式，无需仪器）")

# %% Cell 2
# ========== 选择数据目录 ==========
USE_LATEST = True
if USE_LATEST:
    base = project_root / "data" / "Projection_noise"
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    DATA_DIR = dirs[0] if dirs else None
else:
    DATA_DIR = project_root / "data" / "Projection_noise" / "0528_0911_thermal_pnl"
raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

# %% Cell 3
# ========== 加载配置 ==========
with open(DATA_DIR / "experiment_config.yaml", encoding="utf-8") as f:
    exp_config = yaml.safe_load(f)
NPERSEG = exp_config["psd_params"]["nperseg"]
PSD_INTEG_FMIN = exp_config["psd_params"]["integ_fmin"]
PSD_INTEG_FMAX = exp_config["psd_params"]["integ_fmax"]
NOTCH_FREQS = exp_config["psd_params"]["notch_freqs"]
NOTCH_WIDTH = exp_config["psd_params"]["notch_width"]
PNL_THERMAL_RATIO = exp_config["correction_factors"]["pnl_thermal_ratio"]
F1_CORRECTION = exp_config["correction_factors"]["f1_correction"]
PEAK_THRESHOLD = 5.0  # 窄带峰检测阈值
PEAK_DILATE = 1
print("配置加载完成")

# %% Cell 4
# =====================================================================
# 数据分析: Welch PSD -> 工频陷波 -> 自适应去峰 -> 频段积分 -> kappa2 和 PNL 计算
# =====================================================================
# 两阶段波形分别加载，使用完全相同的 PSD 分析流程
# [经验] PSD 上 50 Hz 及其谐波的尖峰必须在积分前剔除
# [经验] F=1 和 F=2 的贡献不能在 PSD 上通过频率分离，须用理论耦合系数修正
# [经验] 非工频窄带干扰（如开关电源 ~3.6 kHz）用中位数阈值法自动检测并剔除

from scipy.ndimage import binary_dilation

# ---- 辅助函数 ----
def compute_welch_psd(waveform, fs, nperseg):
    nperseg_eff = min(nperseg, len(waveform) // 2)
    freqs, psd = scipy_signal.welch(
        waveform, fs=fs, nperseg=nperseg_eff,
        scaling='density', detrend='constant',
    )
    return freqs, psd

def create_notch_mask(freqs, notch_freqs, notch_width):
    mask = np.ones_like(freqs, dtype=bool)
    for f0 in notch_freqs:
        mask[(freqs >= f0 - notch_width) & (freqs <= f0 + notch_width)] = False
    return mask

def remove_narrow_peaks(freqs, psd, fmin, fmax, threshold=5.0, dilate=1):
    """自适应检测窄带尖峰，返回 mask（True=保留, False=去除）和尖峰 bin 数"""
    band = (freqs >= fmin) & (freqs <= fmax)
    psd_in = psd[band]
    if len(psd_in) == 0:
        return np.ones_like(freqs, dtype=bool), 0
    baseline = np.median(psd_in)
    if baseline <= 0:
        return np.ones_like(freqs, dtype=bool), 0
    bad = np.zeros(len(freqs), dtype=bool)
    bad[band] = psd_in > threshold * baseline
    if dilate > 0 and np.any(bad):
        bad = binary_dilation(bad, iterations=dilate)
    n_bad = int(np.sum(bad[band]))
    return ~bad, n_bad

def integrate_psd(freqs, psd, fmin, fmax, notch_mask=None):
    band_mask = (freqs >= fmin) & (freqs <= fmax)
    if notch_mask is not None:
        band_mask = band_mask & notch_mask
    if np.sum(band_mask) < 2:
        return np.nan
    return np.trapezoid(psd[band_mask], freqs[band_mask])

def analyze_phase(x_wf, y_wf, rate, nperseg, notch_freqs, notch_width,
                  integ_fmin, integ_fmax, peak_threshold=5.0, peak_dilate=1,
                  label=""):
    """Welch PSD → 工频陷波 → 自适应去峰 → 频段积分"""
    n = x_wf.shape[0]
    psd_x_list, psd_y_list = [], []
    freqs_saved = None
    notch_mask = None
    for rep in range(n):
        f, px = compute_welch_psd(x_wf[rep], rate, nperseg)
        _, py = compute_welch_psd(y_wf[rep], rate, nperseg)
        if freqs_saved is None:
            freqs_saved = f
            notch_mask = create_notch_mask(freqs_saved, notch_freqs, notch_width)
        psd_x_list.append(px)
        psd_y_list.append(py)
    psd_x_mean = np.mean(psd_x_list, axis=0)
    psd_x_std = np.std(psd_x_list, axis=0)
    psd_y_mean = np.mean(psd_y_list, axis=0)
    psd_y_std = np.std(psd_y_list, axis=0)
    peak_mask_x, n_peaks_x = remove_narrow_peaks(
        freqs_saved, psd_x_mean, integ_fmin, integ_fmax, peak_threshold, peak_dilate)
    peak_mask_y, n_peaks_y = remove_narrow_peaks(
        freqs_saved, psd_y_mean, integ_fmin, integ_fmax, peak_threshold, peak_dilate)
    combined_x = notch_mask & peak_mask_x
    combined_y = notch_mask & peak_mask_y
    var_x_list, var_y_list = [], []
    for rep in range(n):
        var_x_list.append(integrate_psd(freqs_saved, psd_x_list[rep],
                                        integ_fmin, integ_fmax, combined_x))
        var_y_list.append(integrate_psd(freqs_saved, psd_y_list[rep],
                                        integ_fmin, integ_fmax, combined_y))
    result = {
        "freqs": freqs_saved, "notch_mask": notch_mask,
        "peak_mask_x": peak_mask_x, "peak_mask_y": peak_mask_y,
        "n_peaks_x": n_peaks_x, "n_peaks_y": n_peaks_y,
        "var_x_mean": np.nanmean(var_x_list), "var_x_std": np.nanstd(var_x_list),
        "var_y_mean": np.nanmean(var_y_list), "var_y_std": np.nanstd(var_y_list),
        "psd_x_mean": psd_x_mean, "psd_x_std": psd_x_std,
        "psd_y_mean": psd_y_mean, "psd_y_std": psd_y_std,
    }
    print(f"  {label}: Var(X)={result['var_x_mean']:.4e} +- {result['var_x_std']:.4e}, "
          f"Var(Y)={result['var_y_mean']:.4e} +- {result['var_y_std']:.4e}")
    if n_peaks_x > 0 or n_peaks_y > 0:
        print(f"         自适应去峰: X 移除 {n_peaks_x} bins, Y 移除 {n_peaks_y} bins")
    return result

# ---- Phase 1: 纯光噪声 ----
print("=" * 55)
print("Phase 1: 纯光噪声 PSD 分析")
p1_data = np.load(raw_dir / "waveforms_phase1_light.npz")
rate = float(p1_data["actual_rate"])
light = analyze_phase(
    p1_data["x_waveforms"], p1_data["y_waveforms"],
    rate, NPERSEG, NOTCH_FREQS, NOTCH_WIDTH,
    PSD_INTEG_FMIN, PSD_INTEG_FMAX,
    peak_threshold=PEAK_THRESHOLD, peak_dilate=PEAK_DILATE,
    label="Light noise",
)

# ---- Phase 2: 热态噪声 ----
print(f"\nPhase 2: 热态噪声 PSD 分析")
p2_data = np.load(raw_dir / "waveforms_thermal.npz")
thermal = analyze_phase(
    p2_data["x_waveforms"], p2_data["y_waveforms"],
    float(p2_data["actual_rate"]), NPERSEG, NOTCH_FREQS, NOTCH_WIDTH,
    PSD_INTEG_FMIN, PSD_INTEG_FMAX,
    peak_threshold=PEAK_THRESHOLD, peak_dilate=PEAK_DILATE,
    label="Thermal noise",
)

# ---- 变量别名 ----
var_x_light_mean = light["var_x_mean"]; var_x_light_std = light["var_x_std"]
var_y_light_mean = light["var_y_mean"]; var_y_light_std = light["var_y_std"]
var_x2_mean = thermal["var_x_mean"]; var_x2_std = thermal["var_x_std"]
var_y2_mean = thermal["var_y_mean"]; var_y2_std = thermal["var_y_std"]
psd_x_light_mean = light["psd_x_mean"]; psd_x_light_std = light["psd_x_std"]
psd_y_light_mean = light["psd_y_mean"]; psd_y_light_std = light["psd_y_std"]
psd_x2_mean = thermal["psd_x_mean"]; psd_x2_std = thermal["psd_x_std"]
psd_y2_mean = thermal["psd_y_mean"]; psd_y2_std = thermal["psd_y_std"]
freqs_light = light["freqs"]; freqs_thermal = thermal["freqs"]
notch_mask_light = light["notch_mask"]; notch_mask_thermal = thermal["notch_mask"]
peak_mask_x_light = light["peak_mask_x"]; peak_mask_y_light = light["peak_mask_y"]
peak_mask_x_thermal = thermal["peak_mask_x"]; peak_mask_y_thermal = thermal["peak_mask_y"]
n_peaks_x_light = light["n_peaks_x"]; n_peaks_y_light = light["n_peaks_y"]
n_peaks_x_thermal = thermal["n_peaks_x"]; n_peaks_y_thermal = thermal["n_peaks_y"]
x2 = p2_data["x_waveforms"]; y2 = p2_data["y_waveforms"]
n_repeats = x2.shape[0]
actual_rate = rate

# ---- kappa2 ----
delta_var_x = var_x2_mean - var_x_light_mean
delta_var_y = var_y2_mean - var_y_light_mean
kappa2_raw_x = delta_var_x / var_x_light_mean if var_x_light_mean > 0 else np.nan
kappa2_x = kappa2_raw_x * PNL_THERMAL_RATIO * F1_CORRECTION if var_x_light_mean > 0 else np.nan
kappa2_raw_y = delta_var_y / var_y_light_mean if var_y_light_mean > 0 else np.nan
kappa2_y = kappa2_raw_y * PNL_THERMAL_RATIO * F1_CORRECTION if var_y_light_mean > 0 else np.nan
print(f"\n{'='*55}")
print(f"kappa2 (X): raw={kappa2_raw_x:.4f}, corrected={kappa2_x:.4f}")
print(f"  ΔVar = {delta_var_x:.4e}, Var_light = {var_x_light_mean:.4e}")
print(f"kappa2 (Y): raw={kappa2_raw_y:.4f}, corrected={kappa2_y:.4f}")
pnl_factor = 1.0 + kappa2_x * (1.0 / (PNL_THERMAL_RATIO * F1_CORRECTION)) \
    if (PNL_THERMAL_RATIO > 0 and F1_CORRECTION > 0) else np.nan
var_pnl_x = var_x_light_mean * pnl_factor if not np.isnan(pnl_factor) else np.nan
print(f"PNL factor = {pnl_factor:.4f}, Var(X^PNL) = {var_pnl_x:.4e} V^2")
print(f"{'='*55}")

# =====================================================================
# SPN 频域贡献分析: ΔS(f) → S_PNL(f) → 洛伦兹拟合 → 噪声预算
# =====================================================================

# ---- clean mask ----
clean_x = notch_mask_thermal & peak_mask_x_thermal & peak_mask_x_light
clean_y = notch_mask_thermal & peak_mask_y_thermal & peak_mask_y_light

# ---- ΔS(f) = S_thermal - S_light ----
ds_x = psd_x2_mean - psd_x_light_mean
ds_y = psd_y2_mean - psd_y_light_mean

# ---- 原始 S_PNL(f) = (4/5) × ΔS(f) (V²/Hz) ----
s_pnl_x = PNL_THERMAL_RATIO * ds_x
s_pnl_y = PNL_THERMAL_RATIO * ds_y

# ---- 幅度谱密度 (V/√Hz) ----
s_pnl_amp_x = np.sqrt(np.maximum(s_pnl_x, 0))
s_pnl_amp_y = np.sqrt(np.maximum(s_pnl_y, 0))

# ---- S_light baseline ----
band_full = (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= PSD_INTEG_FMAX)
s_light_baseline_x = np.median(psd_x_light_mean[band_full & clean_x])
s_light_baseline_y = np.median(psd_y_light_mean[band_full & clean_y])

# =====================================================================
# 洛伦兹拟合: 固定 fc=328 Hz (T₂≈0.5ms), offset=0，线性回归 A
# =====================================================================
# 物理上 ΔS(f) 在 f→∞ 时应趋于 0（原子噪声贡献衰减至零），故 offset≡0
# 自由三参数拟合在低 SNR 下不稳定 → 利用已知物理约束，化为过原点线性回归

def lorentzian_model(f, A, fc):
    return A / (1.0 + (f / fc)**2)

FC_FIXED = 328.0  # Hz, T₂ = 1/(2π·fc) ≈ 0.5 ms
FIT_FMAX = 2000
fit_mask_x = clean_x & (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= FIT_FMAX)
f_fit = freqs_thermal[fit_mask_x]
ds_fit = ds_x[fit_mask_x]
X_fit = 1.0 / (1.0 + (f_fit / FC_FIXED)**2)

if len(f_fit) > 10:
    # 过原点线性回归: y = A * X  (offset ≡ 0)
    A_fit = np.sum(X_fit * ds_fit) / np.sum(X_fit**2)
    if A_fit < 0:
        A_fit = 0.0
    fc_fit = FC_FIXED
    T2_psd = 1.0 / (2.0 * np.pi * fc_fit)
    ds_pred = A_fit * X_fit
    ss_res = np.sum((ds_fit - ds_pred)**2)
    ss_tot = np.sum(ds_fit**2)
    r2_fit = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    lorentz_fit_ok = True

    # 光滑曲线（绘图用）
    f_fit_smooth = np.logspace(np.log10(PSD_INTEG_FMIN), np.log10(FIT_FMAX), 200)
    ds_fit_smooth = lorentzian_model(f_fit_smooth, A_fit, fc_fit)

    # 拟合模型给出的光滑 S_PNL(f)（用于噪声预算图）
    s_pnl_fit = PNL_THERMAL_RATIO * lorentzian_model(freqs_thermal, A_fit, fc_fit)
    s_pnl_fit = np.maximum(s_pnl_fit, 0)

    print(f"\n洛伦兹拟合 ΔS_X(f) [fc={FC_FIXED} Hz fixed, offset=0]:")
    print(f"  A  = {A_fit:.4e} V²/Hz,  fc = {fc_fit:.1f} Hz (fixed)")
    print(f"  T₂_PSD = {T2_psd*1000:.2f} ms")
    print(f"  R² = {r2_fit:.4f} (过原点回归)")
    A_theory = kappa2_x * var_x_light_mean * (5.0 / 4.0) * 4.0 * T2_psd
    print(f"  A_theory(from κ̃²) = {A_theory:.4e},  A_fit/A_theory = {A_fit/A_theory:.3f}")
else:
    lorentz_fit_ok = True
    T2_psd = 0.001; fc_fit = FC_FIXED
    A_fit = kappa2_x * var_x_light_mean * (5.0 / 4.0) * 4.0 * T2_psd
    r2_fit = np.nan
    f_fit_smooth = np.logspace(np.log10(PSD_INTEG_FMIN), np.log10(FIT_FMAX), 200)
    ds_fit_smooth = lorentzian_model(f_fit_smooth, A_fit, fc_fit)
    s_pnl_fit = PNL_THERMAL_RATIO * lorentzian_model(freqs_thermal, A_fit, fc_fit)
    s_pnl_fit = np.maximum(s_pnl_fit, 0)
    print(f"\n洛伦兹拟合: 使用理论值 (A={A_fit:.4e}, fc={fc_fit:.0f})")

# ---- 关键频点 SPN 贡献 ----
print(f"\n关键频点噪声预算 (X 通道):")
print(f"  {'f (Hz)':<8} {'S_light':<14} {'S_PNL_raw':<14} {'S_PNL_fit':<14} {'SPN占比':<10} {'SPN amp':<14}")
print(f"  {'-'*8} {'-'*14} {'-'*14} {'-'*14} {'-'*10} {'-'*14}")
for f_target in [10, 50, 100, 159, 200, 500, 1000, 2000, 3000, 5000]:
    idx = np.argmin(np.abs(freqs_thermal - f_target))
    spnl_raw = max(s_pnl_x[idx], 0)
    spnl_fit_v = s_pnl_fit[idx]
    stot = psd_x2_mean[idx]
    frac = spnl_fit_v / stot * 100 if stot > 0 else 0
    amp_nv = np.sqrt(spnl_fit_v) * 1e9
    print(f"  {f_target:<8} {psd_x_light_mean[idx]:<14.4e} {spnl_raw:<14.4e} {spnl_fit_v:<14.4e} {frac:<10.1f} {amp_nv:<14.2f}")

# ========== 保存 ==========
np.savez(
    raw_dir / "psd_matrix.npz",
    freqs_thermal=freqs_thermal, freqs_light=freqs_light,
    psd_x_light_mean=psd_x_light_mean, psd_x_light_std=psd_x_light_std,
    psd_y_light_mean=psd_y_light_mean, psd_y_light_std=psd_y_light_std,
    psd_x_thermal_mean=psd_x2_mean, psd_x_thermal_std=psd_x2_std,
    psd_y_thermal_mean=psd_y2_mean, psd_y_thermal_std=psd_y2_std,
    notch_mask_light=notch_mask_light, notch_mask_thermal=notch_mask_thermal,
    peak_mask_x_thermal=peak_mask_x_thermal, peak_mask_x_light=peak_mask_x_light,
    peak_mask_y_thermal=peak_mask_y_thermal, peak_mask_y_light=peak_mask_y_light,
    var_x_light_mean=var_x_light_mean, var_x_light_std=var_x_light_std,
    var_x_thermal_mean=var_x2_mean, var_x_thermal_std=var_x2_std,
    ds_x=ds_x, ds_y=ds_y,
    s_pnl_x=s_pnl_x, s_pnl_y=s_pnl_y,
    s_pnl_fit_x=s_pnl_fit,
    s_pnl_amp_x=s_pnl_amp_x, s_pnl_amp_y=s_pnl_amp_y,
    s_light_baseline_x=s_light_baseline_x, s_light_baseline_y=s_light_baseline_y,
    clean_x=clean_x, clean_y=clean_y,
    lorentz_fit_ok=lorentz_fit_ok,
    A_fit=A_fit, fc_fit=fc_fit, T2_psd=T2_psd, r2_fit=r2_fit,
)
print(f"\nPSD + SPN 频谱数据已保存: {raw_dir / 'psd_matrix.npz'}")

# %% Cell 5
# =====================================================================
# 对称性检查 & 结果可视化
# =====================================================================


clean_mask_x_light = notch_mask_light & peak_mask_x_light
clean_mask_x_thermal = notch_mask_thermal & peak_mask_x_thermal

# ---- 1. 均值检查 ----
mean_x_thermal = np.nanmean([np.mean(x2[r]) for r in range(n_repeats)])
mean_y_thermal = np.nanmean([np.mean(y2[r]) for r in range(n_repeats)])
threshold_mean = np.sqrt(var_x2_mean) / np.sqrt(x2.shape[1])

# ---- 2. X/Y 方差对称性 ----
symmetry_ratio = var_x2_mean / var_y2_mean if var_y2_mean > 0 else np.nan
symmetry_ok = 0.8 < symmetry_ratio < 1.2 if not np.isnan(symmetry_ratio) else False

# ---- 3. PSD 平坦性 ----
flat_band_light = (freqs_light >= PSD_INTEG_FMIN) & (freqs_light <= PSD_INTEG_FMAX) & clean_mask_x_light
psd_in_band = psd_x_light_mean[flat_band_light]
if len(psd_in_band) > 0:
    psd_fluctuation_db = 10 * np.log10(np.max(psd_in_band) / np.min(psd_in_band)) \
        if np.min(psd_in_band) > 0 else np.inf
    flatness_ok = psd_fluctuation_db < 6
else:
    psd_fluctuation_db = np.nan; flatness_ok = False

# ---- 4. 窄带尖峰检查 ----
n_peaks_total = n_peaks_x_thermal + n_peaks_x_light + n_peaks_y_thermal + n_peaks_y_light
peaks_ok = n_peaks_total == 0

print("=" * 55)
print("对称性与数据质量检查")
print("=" * 55)
print(f"1. 均值检查 (热态): mean(X)={mean_x_thermal:.6e}, mean(Y)={mean_y_thermal:.6e}")
print(f"   -> {'✓' if abs(mean_x_thermal)<10*threshold_mean else '⚠ 检查 DC 偏置'}")
print(f"2. X/Y 方差对称性: Var(X)/Var(Y)={symmetry_ratio:.3f}")
print(f"   -> {'✓' if symmetry_ok else '⚠ 检查解调相位'}")
print(f"3. PSD 平坦性: {psd_fluctuation_db:.1f} dB 起伏")
print(f"   -> {'✓ (<6 dB)' if flatness_ok else '⚠ 检查 LPF 设置'}")
print(f"4. 窄带尖峰: X_th={n_peaks_x_thermal}, X_li={n_peaks_x_light}, Y_th={n_peaks_y_thermal}, Y_li={n_peaks_y_light}")
print(f"   -> {'✓' if peaks_ok else '⚠ 已自动剔除'}")
print("=" * 55)

# ---- 辅助 ----
def find_mask_blocks(mask, freqs, fmin, fmax):
    band = (freqs >= fmin) & (freqs <= fmax)
    bad = ~mask & band
    if not np.any(bad): return []
    changes = np.diff(np.concatenate([[False], bad, [False]]).astype(int))
    starts = np.where(changes == 1)[0]; ends = np.where(changes == -1)[0] - 1
    return [(freqs[s], freqs[e]) for s, e in zip(starts, ends)]

# ---- 字体 ----
_rc_backup = {k: plt.rcParams[k] for k in ['font.size', 'axes.titlesize', 'axes.labelsize',
                                             'xtick.labelsize', 'ytick.labelsize', 'legend.fontsize']}
plt.rcParams.update({'font.size': 8, 'axes.titlesize': 9, 'axes.labelsize': 8,
                     'xtick.labelsize': 7, 'ytick.labelsize': 7, 'legend.fontsize': 7})

# =====================================================================
# Figure 1: PSD 对比 + 方差 + 汇总 (2×2)
# =====================================================================
fig, axes = plt.subplots(2, 2, figsize=(9, 7))

# --- X 通道 PSD (clean mask) ---
ax1 = axes[0, 0]
for f0 in NOTCH_FREQS:
    ax1.axvspan(f0 - NOTCH_WIDTH, f0 + NOTCH_WIDTH, color='red', alpha=0.08)
plot_xl = (freqs_light >= PSD_INTEG_FMIN) & (freqs_light <= PSD_INTEG_FMAX) & clean_x
plot_xt = (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= PSD_INTEG_FMAX) & clean_x
for f_s, f_e in find_mask_blocks(peak_mask_x_light, freqs_light, PSD_INTEG_FMIN, PSD_INTEG_FMAX):
    ax1.axvspan(f_s, f_e, facecolor='orange', alpha=0.15)
ax1.plot(freqs_light[plot_xl], psd_x_light_mean[plot_xl], 'C0', alpha=0.7, lw=0.6, label='Light')
ax1.fill_between(freqs_light[plot_xl],
                 np.maximum(psd_x_light_mean[plot_xl] - psd_x_light_std[plot_xl], 1e-20),
                 psd_x_light_mean[plot_xl] + psd_x_light_std[plot_xl], color='C0', alpha=0.15)
ax1.plot(freqs_thermal[plot_xt], psd_x2_mean[plot_xt], 'C3', alpha=0.7, lw=0.6, label='Thermal')
ax1.fill_between(freqs_thermal[plot_xt],
                 np.maximum(psd_x2_mean[plot_xt] - psd_x2_std[plot_xt], 1e-20),
                 psd_x2_mean[plot_xt] + psd_x2_std[plot_xt], color='C3', alpha=0.15)
ax1.set_yscale('log'); ax1.set_xlabel('Frequency (Hz)'); ax1.set_ylabel('PSD (V²/Hz)')
ax1.set_title(f'X-Channel PSD: Light vs Thermal\nκ̃² = {kappa2_x:.4f}')
ax1.legend(fontsize=6); ax1.grid(True, alpha=0.3)
# y 轴范围用 1-99 百分位截断，避免噪声峰压缩有效数据区域
_psd_x_all = np.concatenate([psd_x_light_mean[plot_xl], psd_x2_mean[plot_xt]])
if len(_psd_x_all) > 0:
    ax1.set_ylim(np.percentile(_psd_x_all, 0.5) * 0.5,
                 np.percentile(_psd_x_all, 99.5) * 2.0)

# --- Y 通道 PSD (clean mask) ---
ax2 = axes[0, 1]
for f0 in NOTCH_FREQS:
    ax2.axvspan(f0 - NOTCH_WIDTH, f0 + NOTCH_WIDTH, color='red', alpha=0.08)
plot_yl = (freqs_light >= PSD_INTEG_FMIN) & (freqs_light <= PSD_INTEG_FMAX) & clean_y
plot_yt = (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= PSD_INTEG_FMAX) & clean_y
for f_s, f_e in find_mask_blocks(peak_mask_y_light, freqs_light, PSD_INTEG_FMIN, PSD_INTEG_FMAX):
    ax2.axvspan(f_s, f_e, facecolor='orange', alpha=0.15)
ax2.plot(freqs_light[plot_yl], psd_y_light_mean[plot_yl], 'C0', alpha=0.7, lw=0.6, label='Light')
ax2.fill_between(freqs_light[plot_yl],
                 np.maximum(psd_y_light_mean[plot_yl] - psd_y_light_std[plot_yl], 1e-20),
                 psd_y_light_mean[plot_yl] + psd_y_light_std[plot_yl], color='C0', alpha=0.15)
ax2.plot(freqs_thermal[plot_yt], psd_y2_mean[plot_yt], 'C3', alpha=0.7, lw=0.6, label='Thermal')
ax2.fill_between(freqs_thermal[plot_yt],
                 np.maximum(psd_y2_mean[plot_yt] - psd_y2_std[plot_yt], 1e-20),
                 psd_y2_mean[plot_yt] + psd_y2_std[plot_yt], color='C3', alpha=0.15)
ax2.set_yscale('log'); ax2.set_xlabel('Frequency (Hz)'); ax2.set_ylabel('PSD (V²/Hz)')
ax2.set_title(f'Y-Channel PSD: Light vs Thermal\nκ̃²_y = {kappa2_y:.4f}')
ax2.legend(fontsize=6); ax2.grid(True, alpha=0.3)
# y 轴范围用 1-99 百分位截断，避免噪声峰压缩有效数据区域
_psd_y_all = np.concatenate([psd_y_light_mean[plot_yl], psd_y2_mean[plot_yt]])
if len(_psd_y_all) > 0:
    ax2.set_ylim(np.percentile(_psd_y_all, 0.5) * 0.5,
                 np.percentile(_psd_y_all, 99.5) * 2.0)

# --- 方差对比 ---
ax3 = axes[1, 0]
phases = ['Light (X)', 'Thermal (X)', 'Light (Y)', 'Thermal (Y)']
var_values = [var_x_light_mean, var_x2_mean, var_y_light_mean, var_y2_mean]
var_errors = [var_x_light_std, var_x2_std, var_y_light_std, var_y2_std]
bars = ax3.bar(np.arange(4), var_values, yerr=var_errors, color=['C0','C3','C0','C3'], capsize=5, alpha=0.8)
ax3.set_xticks(np.arange(4)); ax3.set_xticklabels(phases, rotation=15, ha='right', fontsize=7)
ax3.set_ylabel('Integrated Variance (V²)')
ax3.set_title(f'PSD-Integrated Variance\nInteg: [{PSD_INTEG_FMIN}, {PSD_INTEG_FMAX}] Hz')
ax3.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, var_values):
    ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f'{val:.2e}',
             ha='center', va='bottom', fontsize=6)

# --- 汇总 ---
ax4 = axes[1, 1]; ax4.axis('off')
summary_text = (
    f"Results Summary\n{'─'*30}\n"
    f"κ̃² (X) = {kappa2_x:.4f}\nκ̃² (Y) = {kappa2_y:.4f}\n\n"
    f"PNL factor = {pnl_factor:.4f}\nVar(X^PNL) = {var_pnl_x:.4e} V²\n\n"
    f"Lorentz fit (fc={fc_fit:.0f} Hz):\n"
    f"  A={A_fit:.4e}, R²={r2_fit:.3f}\n"
    f"  T₂_PSD={T2_psd*1000:.1f} ms\n\n"
    f"Peak removal: {n_peaks_x_thermal}x + {n_peaks_x_light}l bins\n\n"
    f"Var_light(X) = {var_x_light_mean:.4e} V²\nVar_thermal(X) = {var_x2_mean:.4e} V²\n"
    f"ΔVar(X) = {delta_var_x:.4e} V²\n"
)
ax4.text(0.05, 0.95, summary_text, transform=ax4.transAxes,
         fontsize=7, fontfamily='monospace', verticalalignment='top',
         bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
ax4.set_title('κ̃² & PNL Summary')

plt.tight_layout()
fig.savefig(results_dir / "projection_noise_calibration.png", dpi=150, bbox_inches="tight")
plt.show()

# =====================================================================
# Figure 2: ΔS(f) 洛伦兹拟合 (0.5-2000 Hz)
# =====================================================================
fig2, axes2 = plt.subplots(1, 2, figsize=(10, 4))

fit_band = (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= FIT_FMAX) & clean_x

ax_l = axes2[0]
ax_l.plot(freqs_thermal[fit_band], ds_x[fit_band] * 1e9, 'C0.', markersize=2, alpha=0.6,
          label=r'$\Delta S_X(f)$ data')
ax_l.axhline(0, color='gray', ls='--', alpha=0.3)
for f0 in NOTCH_FREQS:
    if f0 <= FIT_FMAX:
        ax_l.axvspan(f0 - NOTCH_WIDTH, f0 + NOTCH_WIDTH, color='red', alpha=0.08)
ax_l.plot(f_fit_smooth, ds_fit_smooth * 1e9, 'C3-', linewidth=1.5,
          label=f'Lorentz fit (fc={fc_fit:.0f} Hz, A={A_fit:.2e})')
ax_l.axvline(fc_fit, color='C3', ls=':', alpha=0.5, label=f'fc={fc_fit:.0f} Hz')
ax_l.set_xlabel('Frequency (Hz)'); ax_l.set_ylabel(r'$\Delta S_X(f)$ (nV²/Hz)')
ax_l.set_title(r'$\Delta S_X(f)$ = S$_{\rm thermal}$ - S$_{\rm light}$ (clean)')
ax_l.legend(fontsize=6); ax_l.grid(True, alpha=0.3); ax_l.set_xlim(0, FIT_FMAX)

ax_r = axes2[1]
ax_r.loglog(freqs_thermal[fit_band], np.maximum(ds_x[fit_band], 1e-40), 'C0.', markersize=2,
            alpha=0.6, label=r'$\Delta S_X(f)$ data')
ax_r.loglog(f_fit_smooth, np.maximum(ds_fit_smooth, 1e-40), 'C3-', linewidth=1.5,
            label=f'Lorentz fit (R²={r2_fit:.3f})')
ax_r.axvline(fc_fit, color='C3', ls=':', alpha=0.5)
ax_r.set_xlabel('Frequency (Hz)'); ax_r.set_ylabel(r'$\Delta S_X(f)$ (V²/Hz)')
ax_r.set_title(fr'$\Delta S_X(f)$ log-log (Lorentz fit, fc={fc_fit:.0f} Hz)')
ax_r.legend(fontsize=6); ax_r.grid(True, alpha=0.3)

plt.tight_layout()
fig2.savefig(results_dir / "ds_lorentz_fit.png", dpi=150, bbox_inches="tight")
plt.show()

# =====================================================================
# Figure 3: 噪声预算 — 用洛伦兹拟合值画 SPN 曲线
# =====================================================================
fig3, axes3 = plt.subplots(2, 1, figsize=(9, 6), sharex=True)

nb_mask = (freqs_thermal >= PSD_INTEG_FMIN) & (freqs_thermal <= PSD_INTEG_FMAX) & clean_x
f_nb = freqs_thermal[nb_mask]
s_light_nb = psd_x_light_mean[nb_mask]
s_thermal_nb = psd_x2_mean[nb_mask]
s_pnl_nb = s_pnl_fit[nb_mask]

ax_top = axes3[0]
ax_top.fill_between(f_nb, s_light_nb, s_thermal_nb, color='C3', alpha=0.2,
                     label='Atomic (SPN) contribution')
ax_top.loglog(f_nb, s_light_nb, 'C0', lw=0.8, label=r'$S_{\rm light}$ (shot+elec)')
ax_top.loglog(f_nb, s_thermal_nb, 'C3', lw=0.6, alpha=0.7, label=r'$S_{\rm thermal}$ (total)')
ax_top.loglog(f_nb, s_pnl_nb, 'C2', lw=1.0, alpha=0.8,
              label=r'$S_{\rm PNL}$ (F=2 SPN, Lorentz fit)')
for f0 in NOTCH_FREQS:
    ax_top.axvspan(f0 - NOTCH_WIDTH, f0 + NOTCH_WIDTH, color='red', alpha=0.06)
ax_top.set_ylabel('PSD (V²/Hz)')
ax_top.set_title('Noise Budget: PSD Decomposition (X Channel, Lorentz fit)')
ax_top.legend(fontsize=6, loc='upper right'); ax_top.grid(True, alpha=0.3)

ax_bot = axes3[1]
amp_light = np.sqrt(s_light_nb)
amp_total = np.sqrt(s_thermal_nb)
amp_pnl = np.sqrt(s_pnl_nb)
spn_fraction = amp_pnl / amp_total * 100

ax_bot.loglog(f_nb, amp_light * 1e9, 'C0', lw=0.8, label=r'$\sqrt{S_{\rm light}}$')
ax_bot.loglog(f_nb, amp_total * 1e9, 'C3', lw=0.6, alpha=0.7, label=r'$\sqrt{S_{\rm thermal}}$')
ax_bot.loglog(f_nb, amp_pnl * 1e9, 'C2', lw=1.0, alpha=0.8,
              label=r'$\sqrt{S_{\rm PNL}}$ (SPN fit)')
ax_bot.set_ylabel('Amplitude Spectral Density\n(nV/√Hz)')
ax_bot.set_xlabel('Frequency (Hz)')
ax_bot.legend(fontsize=6, loc='upper right'); ax_bot.grid(True, alpha=0.3)

ax_frac = ax_bot.twinx()
ax_frac.semilogx(f_nb, spn_fraction, 'gray', lw=0.4, alpha=0.5)
ax_frac.set_ylabel('SPN / Total (%)', color='gray', fontsize=7)
ax_frac.tick_params(axis='y', labelcolor='gray', labelsize=6)
ax_frac.set_ylim(0, max(50, np.percentile(spn_fraction, 99) * 1.2))

for f_mark in [50, 100, fc_fit, 1000]:
    ax_bot.axvline(f_mark, color='gray', ls=':', alpha=0.3)

plt.tight_layout()
fig3.savefig(results_dir / "noise_budget.png", dpi=150, bbox_inches="tight")
plt.show()

plt.rcParams.update(_rc_backup)

# ========== 保存分析结果 ==========
analysis_path = results_dir / "analysis.yaml"
analysis = {
    "variances": {
        "light_noise": {"var_x_mean": float(var_x_light_mean), "var_x_std": float(var_x_light_std),
                        "var_y_mean": float(var_y_light_mean), "var_y_std": float(var_y_light_std)},
        "thermal": {"var_x_mean": float(var_x2_mean), "var_x_std": float(var_x2_std),
                    "var_y_mean": float(var_y2_mean), "var_y_std": float(var_y2_std)},
    },
    "kappa2": {
        "kappa2_x": float(kappa2_x), "kappa2_y": float(kappa2_y),
        "raw_ratio_x": float(kappa2_raw_x), "raw_ratio_y": float(kappa2_raw_y),
        "delta_var_x": float(delta_var_x), "delta_var_y": float(delta_var_y),
        "correction_factors": {"pnl_thermal_ratio": PNL_THERMAL_RATIO, "f1_correction": F1_CORRECTION},
    },
    "pnl": {"pnl_factor": float(pnl_factor), "var_x_pnl": float(var_pnl_x)},
    "spn_spectrum": {"s_light_baseline_x": float(s_light_baseline_x),
                     "s_light_baseline_y": float(s_light_baseline_y)},
    "lorentz_fit": {
        "fit_ok": bool(lorentz_fit_ok), "method": "linear_regression_fixed_fc",
        "fc_fixed_Hz": FC_FIXED,
        "A": float(A_fit), "fc_Hz": float(fc_fit),
        "T2_psd_ms": float(T2_psd * 1000) if not np.isnan(T2_psd) else None,
        "r2": float(r2_fit), "fit_fmax_Hz": FIT_FMAX,
    },
    "peak_removal": {
        "threshold": PEAK_THRESHOLD, "dilate": PEAK_DILATE,
        "n_bins_removed": {"x_thermal": int(n_peaks_x_thermal), "x_light": int(n_peaks_x_light),
                           "y_thermal": int(n_peaks_y_thermal), "y_light": int(n_peaks_y_light)},
    },
    "symmetry_checks": {
        "mean_x_thermal": float(mean_x_thermal), "mean_y_thermal": float(mean_y_thermal),
        "var_ratio_xy": float(symmetry_ratio), "symmetry_ok": bool(symmetry_ok),
        "psd_flatness_db": float(psd_fluctuation_db), "flatness_ok": bool(flatness_ok),
        "n_narrow_peaks": int(n_peaks_total), "peaks_ok": bool(peaks_ok),
    },
    "psd_params": {
        "nperseg": NPERSEG, "integ_fmin": PSD_INTEG_FMIN, "integ_fmax": PSD_INTEG_FMAX,
        "notch_freqs": NOTCH_FREQS, "notch_width": NOTCH_WIDTH,
        "peak_threshold": PEAK_THRESHOLD, "peak_dilate": PEAK_DILATE,
        "sampling_rate_thermal": float(actual_rate),
    },
}
with open(analysis_path, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"分析结果已保存: {analysis_path}")

