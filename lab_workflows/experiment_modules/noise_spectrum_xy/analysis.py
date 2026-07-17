# %% [markdown] Cell 0
# # X/Y 交流控制噪声谱测量离线分析脚本
#
# 从采集脚本保存的 raw/ 数据离线计算 PSD、拟合并绘图。

# %% Cell 1
from pathlib import Path
import os
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "TkAgg"))
import matplotlib.pyplot as plt
from scipy import signal as scipy_signal
from scipy.optimize import curve_fit

from lab_workflows.experiment_runtime import runtime_run_dir
from lab_workflows.experiment_modules.noise_spectrum_xy.calibration import (
    fit_robust_linear_calibration,
)

plt.rcParams.update({"figure.dpi": 120, "font.size": 11, "axes.labelsize": 12})

EXPERIMENT_TYPE = "Noise_Spectrum_XY_Ctrl"
run_dir = runtime_run_dir()

# %% Cell 17
# ===== 离线数据分析：从保存的配置加载参数 =====
# 重要：后续分析 Cell 将使用本 Cell 从 experiment_config.yaml 加载的参数，
#       而不是笔记本顶部（Cell 5）的默认值。
# 步骤：
#   1. 运行 Cells 1-5（导入库 + 基础配置）
#   2. 运行本 Cell 加载保存的参数
#   3. 依次运行后续分析 Cell
# ⚠️ 不要在本 Cell 之后重新运行 Cell 5（否则会覆盖保存的参数）
# ------------------------------------------------

if run_dir is None or not run_dir.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {run_dir}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，或设置 USE_LATEST=False 手动指定路径"
    )

DATA_RUN = run_dir.name
raw_dir = run_dir / "raw"
results_dir = run_dir / "results"
results_dir.mkdir(parents=True, exist_ok=True)

# ---- 从 experiment_config.yaml 加载保存的实验参数 ----
config_path = run_dir / "experiment_config.yaml"
if not config_path.exists():
    raise FileNotFoundError(f"实验配置文件不存在: {config_path}")

with open(config_path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# ===== 覆盖所有分析相关参数 =====
# 来源：保存的 experiment_config.yaml（采集时的实际参数）
print(f"\n{'='*55}")
print(f"从 experiment_config.yaml 加载实验参数:")
print(f"{'='*55}")

# 扫描参数：新目录直接保存目标 Omega_ctrl 与反算包络，旧目录保留幅度扫描兼容。
scan_cfg = cfg["scan_params"]
DIRECT_AW_TARGET_SCAN = "target_noise_frequency_Hz" in scan_cfg
configured_omega_ctrl = None
configured_envelope_v = None
if DIRECT_AW_TARGET_SCAN:
    configured_omega_ctrl = np.asarray(
        scan_cfg["target_noise_frequency_Hz"], dtype=float
    )
    configured_envelope_v = np.asarray(
        scan_cfg["xy_envelope_voltage_V"], dtype=float
    )
    XY_AMP_START = float(configured_envelope_v[0])
    XY_AMP_STOP = float(configured_envelope_v[-1])
    XY_AMP_POINTS = len(configured_envelope_v)
    print(
        f"  Target Omega_ctrl = {configured_omega_ctrl[0]:.1f} - "
        f"{configured_omega_ctrl[-1]:.1f} Hz"
    )
    print(
        f"  DirectAW envelope = {XY_AMP_START:.6f} - "
        f"{XY_AMP_STOP:.6f} V"
    )
else:
    XY_AMP_START = scan_cfg["XY_AMP_START_V"]
    XY_AMP_STOP = scan_cfg["XY_AMP_STOP_V"]
    XY_AMP_POINTS = scan_cfg["XY_AMP_POINTS"]
    print(f"  XY_AMP_START  = {XY_AMP_START} V")
    print(f"  XY_AMP_STOP   = {XY_AMP_STOP} V")
    print(f"  XY_AMP_POINTS = {XY_AMP_POINTS}")
XY_SETTLE_TIME = scan_cfg["XY_SETTLE_TIME_s"]

# DAQ 采集参数（Welch PSD 计算用）
actual_rate_daq = cfg["hf2_daq"].get("actual_rate_Sa_s", cfg["hf2_daq"]["DAQ_rate_Sa_s"])
HF2_NPERSEG     = cfg["hf2_daq"]["nperseg"]
HF2_DAQ_TC      = cfg["hf2_daq"]["DAQ_TC_s"]
HF2_DAQ_DURATION = cfg["hf2_daq"]["DAQ_duration_s"]
HF2_DAQ_RATE    = cfg["hf2_daq"]["DAQ_rate_Sa_s"]
print(f"  采样率        = {actual_rate_daq} Sa/s")
print(f"  HF2_NPERSEG   = {HF2_NPERSEG}")
print(f"  HF2_DAQ_DURATION = {HF2_DAQ_DURATION} s")
print(f"  HF2_DAQ_TC    = {HF2_DAQ_TC*1e6:.2f} μs")

# ---- 可选：如果之前已保存 PSD 矩阵，可直接加载 ----
LOAD_EXISTING_PSD = True  # True=跳过 Welch 重算，False=从原始波形重新计算
psd_matrix_path = results_dir / "psd_matrix.npz"

if LOAD_EXISTING_PSD and psd_matrix_path.exists():
    _d = np.load(psd_matrix_path, allow_pickle=True)
    psd_matrix = _d["psd_matrix"]
    freq_axis = _d["freq_axis"]
    amplitudes_used = _d["amplitudes"]
    print(f"\n已加载已有 PSD 矩阵: shape={psd_matrix.shape}")
else:
    # ---- 从原始波形重新计算 PSD ----
    print(f"\n从原始波形重新计算 PSD...")
    n_files = len(list(raw_dir.glob("waveform_C*.npy")))
    print(f"找到 {n_files} 个波形文件")

    psd_list = []
    freq_axis = None

    for i in range(n_files):
        wf_path = raw_dir / f"waveform_C{i:04d}.npy"
        if not wf_path.exists():
            print(f"  ⚠️ 缺失: {wf_path.name}")
            continue

        waveform = np.load(wf_path)

        if np.all(np.isnan(waveform)) or len(waveform) < HF2_NPERSEG:
            print(f"  ⚠️ 无效波形: {wf_path.name}, 跳过 PSD 计算")
            psd_list.append(np.full(HF2_NPERSEG // 2 + 1, np.nan))
            if i == 0:
                freq_axis = np.fft.rfftfreq(HF2_NPERSEG, d=1.0/actual_rate_daq)
            continue

        f, psd = scipy_signal.welch(
            waveform, fs=actual_rate_daq,
            nperseg=min(HF2_NPERSEG, len(waveform)),
            noverlap=None,
            scaling="density",
        )
        if i == 0:
            freq_axis = f
        psd_list.append(psd)

    psd_matrix = np.array(psd_list)
    if DIRECT_AW_TARGET_SCAN:
        amplitudes_used = configured_envelope_v[:len(psd_matrix)]
    else:
        amplitudes_used = np.linspace(
            XY_AMP_START, XY_AMP_STOP, len(psd_matrix)
        )

    print(f"PSD 矩阵形状: {psd_matrix.shape}")
    print(f"频率范围: {freq_axis[0]:.0f} - {freq_axis[-1]:.0f} Hz")
    print(f"频率点数: {len(freq_axis)}")

    results_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        results_dir / "psd_matrix.npz",
        psd_matrix=psd_matrix,
        freq_axis=freq_axis,
        amplitudes=amplitudes_used,
        target_omega_ctrl_Hz=(
            configured_omega_ctrl[:len(psd_matrix)]
            if DIRECT_AW_TARGET_SCAN
            else np.array([])
        ),
        actual_rate=actual_rate_daq,
    )
    print(f"\nPSD 矩阵已保存: {results_dir / 'psd_matrix.npz'}")

print(f"\n✅ 参数已从 {DATA_RUN}/experiment_config.yaml 加载完成")
print(f"   后续分析 Cell 将使用以上保存的参数进行运算")
print(f"   运行目录: {run_dir}")

# %% Cell 18
# ===== 幅频标定：逐列寻峰 =====
# 原理:
#   对固定频率 ω_j，沿幅度轴看 PSD → 在 V_peak 处有共振峰，其他 V 处 ≈ N_S1。
#   逐列 find_peaks 找最大 prominence 峰 → (f_j, V_peak_j)。
#   然后用 Theil-Sen 初值 + 迭代 3×MAD 离群拒绝拟合 k·V+b。

# 每次分析都从 PSD 脊线重新标定，已有 calibration.npz 不作为 K/B 输入。
from scipy.signal import find_peaks

PEAK_PROMINENCE_MIN = None  # 最小prominence阈值，None=自动用col.std的0.1倍
CALIBRATION_FIT_RANGE_V = (0.0, 4.0)  # 幅频标定线性拟合使用的控制幅度范围
CALIBRATION_REJECTION_SIGMA = 3.0  # MAD 鲁棒残差拒绝阈值

# ============ Step 0: 确定 Ω_Ctrl 频率范围 ============
# 标定搜索覆盖实测 PSD 频率轴，不使用采集配置中的预设 K/B 或经验斜率。
f_lo = max(0, float(freq_axis[0]))
f_hi = float(freq_axis[-1])
freq_mask = (freq_axis >= f_lo) & (freq_axis <= f_hi)
freq_indices = np.where(freq_mask)[0]
print(f"搜索范围: f ∈ [{f_lo:.0f}, {f_hi:.0f}] Hz, {len(freq_indices)} 频率列")

# ============ Step 1: 逐列寻峰（含边界扩展） ============
def find_peaks_extended(col, ext_ratio=0.1):
    """对称扩展后寻峰，避免漏掉边界附近的真峰"""
    n = len(col)
    n_ext = int(n * ext_ratio)
    col_ext = np.concatenate([col[n_ext-1::-1], col, col[:-n_ext-1:-1]])
    peaks_ext, props = find_peaks(col_ext, prominence=0)
    peaks = peaks_ext - n_ext
    valid = (peaks >= 0) & (peaks < n)
    return peaks[valid], props["prominences"][valid]

peak_amps = np.full(len(freq_axis), np.nan)

for j in freq_indices:
    col = psd_matrix[:, j]
    if np.all(np.isnan(col)):
        continue

    peaks, prominences = find_peaks_extended(col)
    if len(peaks) == 0:
        continue

    # 自动prominence阈值
    thresh = PEAK_PROMINENCE_MIN if PEAK_PROMINENCE_MIN is not None else 0.1 * np.nanstd(col)
    valid_peak = prominences > thresh
    if not np.any(valid_peak):
        continue

    # 只在有效峰中选prominence最大的
    valid_idx = np.where(valid_peak)[0]
    best = valid_idx[np.argmax(prominences[valid_peak])]
    peak_amps[j] = amplitudes_used[peaks[best]]

# 有效点筛选: 峰不能太靠近幅度边界
valid_cal = ~np.isnan(peak_amps)
amp_lo = amplitudes_used[0] + 0.1 * (amplitudes_used[-1] - amplitudes_used[0])
amp_hi = amplitudes_used[-1] - 0.1 * (amplitudes_used[-1] - amplitudes_used[0])
valid_cal &= (peak_amps >= amp_lo) & (peak_amps <= amp_hi)
fit_amp_lo, fit_amp_hi = CALIBRATION_FIT_RANGE_V
valid_cal &= (peak_amps >= fit_amp_lo) & (peak_amps <= fit_amp_hi)

V_candidates = peak_amps[valid_cal]
f_candidates = freq_axis[valid_cal]
n_total = len(freq_indices)
print(
    f"逐列寻峰: {np.sum(valid_cal)}/{n_total} 有效峰 "
    f"({100*np.sum(valid_cal)/max(n_total,1):.1f}%), "
    f"拟合幅度范围: {fit_amp_lo:g}-{fit_amp_hi:g} V"
)

# ============ Step 2: 鲁棒 k·V+b 标定 ============
try:
    calibration = fit_robust_linear_calibration(
        V_candidates,
        f_candidates,
        rejection_sigma=CALIBRATION_REJECTION_SIGMA,
    )
except ValueError as exc:
    raise RuntimeError(
        f"PSD 脊线标定失败：{exc}；分析不会回退到预设 K/B，"
        "请检查 calibration 图和原始 PSD"
    ) from exc

calibration_inlier_mask = calibration.inlier_mask
V_cal = V_candidates[calibration_inlier_mask]
f_cal = f_candidates[calibration_inlier_mask]
V_rejected = V_candidates[~calibration_inlier_mask]
f_rejected = f_candidates[~calibration_inlier_mask]
k = calibration.slope_hz_per_v
b = calibration.intercept_hz
omega_ctrl = k * amplitudes_used + b
calibration_source = "psd_ridge_robust_fit"
print(
    f"  鲁棒离群拒绝完成: 保留 {len(V_cal)}/{len(V_candidates)} 点，"
    f"剔除 {len(V_rejected)} 点，残差 MAD σ={calibration.residual_sigma_hz:.2f} Hz"
)
print(f"\n幅频标定结果:")
print(f"  k = {k:.2f} Hz/V")
print(f"  b = {b:.2f} Hz")
print(f"  Ω_Ctrl = {k:.2f} · V + {b:.2f}")

# 保存
np.savez(results_dir / "calibration.npz",
            k=k, b=b, amplitudes=amplitudes_used, omega_ctrl=omega_ctrl,
            peak_amps=peak_amps, V_cal=V_cal, f_cal=f_cal,
            V_candidates=V_candidates, f_candidates=f_candidates,
            calibration_inlier_mask=calibration_inlier_mask,
            V_rejected=V_rejected, f_rejected=f_rejected,
            residual_sigma_hz=calibration.residual_sigma_hz,
            rejection_sigma=CALIBRATION_REJECTION_SIGMA,
            calibration_source=np.array(calibration_source),
            fit_range_V=np.array(CALIBRATION_FIT_RANGE_V, dtype=float))
print(f"标定结果已保存: {results_dir / 'calibration.npz'}")

# ---- 标定图（1×2：散点拟合 + PSD伪彩图纵轴为Ω_Ctrl） ----
fig_cal, axes_cal = plt.subplots(1, 2, figsize=(14, 5))

# 图 a: 散点 + 拟合
ax = axes_cal[0]
if len(V_rejected):
    ax.scatter(V_rejected, f_rejected / 1000, s=4, alpha=0.25,
               color="tab:red", label="Rejected nonlinear peaks")
ax.scatter(V_cal, f_cal / 1000, s=3, alpha=0.5, color="gray", label="Column peaks")
ax.plot(amplitudes_used, omega_ctrl / 1000, "r-", linewidth=2,
        label=f"Robust fit: $\\Omega_{{\\mathrm{{Ctrl}}}}$ = {k:.0f}·V + {b:.0f}")
ax.set_xlabel("DirectAW Envelope (V)")
ax.set_ylabel("$\\Omega_{\\mathrm{Ctrl}}$ (kHz)")
ax.set_title(f"Column-wise Peak Finding (k={k:.0f}, b={b:.0f})")
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)

# 图 b: 原始 PSD log（纵轴映射为 Ω_Ctrl，x轴聚焦至脊线区域）
ax = axes_cal[1]
psd_log = np.log10(np.maximum(psd_matrix, 1e-20))
omega_min, omega_max = omega_ctrl[0], omega_ctrl[-1]
extent = [freq_axis[0], freq_axis[-1], omega_min, omega_max]
ax.imshow(psd_log, aspect="auto", origin="lower", extent=extent, cmap="inferno")
ax.plot([omega_min, omega_max], [omega_min, omega_max],
        "c--", linewidth=1.5, label=f"$\\Omega_{{\\mathrm{{Ctrl}}}}$ = {k:.0f}·V + {b:.0f}")
ax.set_xlim(0, omega_max)
ax.set_ylim(omega_min, omega_max)
ax.set_xlabel("Frequency (Hz)")
ax.set_ylabel("$\\Omega_{\\mathrm{Ctrl}}$ (Hz)")
ax.set_title("Raw PSD (log10) with Calibrated Ridge")
ax.legend(fontsize=8)

fig_cal.tight_layout()
fig_cal.savefig(results_dir / "calibration.png", dpi=150)
print(f"标定图已保存: {results_dir / 'calibration.png'}")

# %% Cell 19
# 诊断：查看特定频率列沿幅度轴的 PSD 曲线
test_freqs = [1000, 3000, 10000,20000]  # Hz
fig, axes = plt.subplots(2, 2, figsize=(12, 8))
for ax, f_idx in zip(axes.flatten(), [np.argmin(np.abs(freq_axis - f)) for f in test_freqs]):
    ax.plot(amplitudes_used, psd_matrix[:, f_idx], 'o-', ms=3)
    ax.axvline(amplitudes_used[np.argmax(psd_matrix[:, f_idx])],
               color='r', ls='--', label=f'peak @ {amplitudes_used[np.argmax(psd_matrix[:, f_idx])]:.2f}V')
    ax.set_xlabel('Control Amplitude (V)')
    ax.set_ylabel(f'PSD @ {freq_axis[f_idx]:.0f} Hz')
    ax.legend()
    ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig(results_dir / "psd_column_diagnostics.png", dpi=150, bbox_inches="tight")
print(f"PSD 频率列诊断图已保存: {results_dir / 'psd_column_diagnostics.png'}")

# %% Cell 20
# ===== 洛伦兹拟合提取噪声参数 =====
# 在每个频率点 ω 上，对控制强度 Ω_Ctrl 做洛伦兹拟合
#
# S_S1(Ω_Ctrl; ω) = D + A · (γ² + ω²) / ((γ² - ω² + (Ω_Ctrl + Δω)²)² + 4ω²γ²)
#
# 拟合参数: gamma (γ), Amp (A), D (N_S1), dw (Δω)

def lorentzian_vs_omega(omega_ctrl, gamma, Amp, D, dw, omega_fixed):
    """洛伦兹函数: PSD vs 控制强度，在固定频率 omega_fixed 上"""
    w = omega_fixed
    Om = omega_ctrl + dw
    return D + Amp * (gamma**2 + w**2) / (
        (gamma**2 - w**2 + Om**2)**2 + 4 * w**2 * gamma**2
    )

# ---- 逆向估计 Amp 初始值（来自 oldcode） ----
def estimate_amp_guess(psd_max, gamma_guess, omega_fixed):
    """
    从洛伦兹峰值的解析公式反向求解 Amp:
    在共振时 (OmegaCtrl ~ omega), 洛伦兹分母 ≈ (γ² - ω² + ω²)² = γ⁴, 分子 = γ² + ω²
    → 峰高 ≈ Amp × (γ² + ω²) / γ⁴
    → Amp ≈ 峰高 × γ⁴ / (γ² + ω²)
    """
    peak_height = psd_max
    denom = (gamma_guess**2 + omega_fixed**2)
    return peak_height * (gamma_guess**4 + 4 * gamma_guess**2 * omega_fixed**2) / denom

# ---- 配置拟合参数 ----
PEAK_MARGIN = 10000        # 脊线附近 ±10 kHz 内检测共振峰
GAMMA_GUESS = 300          # 初始线宽估计

# 构造 Ω_Ctrl 轴
omega_ctrl_axis = omega_ctrl

n_freq = len(freq_axis)
popt_list = np.full((n_freq, 4), np.nan)  # gamma, Amp, D, dw
perr_list = np.full((n_freq, 4), np.nan)
fit_mask = np.zeros(n_freq, dtype=bool)

print("洛伦兹拟合中...")
for j in range(n_freq):
    psd_slice = psd_matrix[:, j]
    if np.all(np.isnan(psd_slice)):
        continue

    SwFreq = freq_axis[j]

    # ---- 只在脊线附近 [SwFreq-10k, SwFreq+10k] 内检测峰 ----
    peak_range = [SwFreq - PEAK_MARGIN, SwFreq + PEAK_MARGIN]
    peak_idx_lo = np.searchsorted(omega_ctrl_axis, peak_range[0])
    peak_idx_hi = np.searchsorted(omega_ctrl_axis, peak_range[1])
    if peak_idx_hi <= peak_idx_lo:
        continue

    local_slice = psd_slice[peak_idx_lo:peak_idx_hi]
    median_val = np.nanmedian(psd_slice)
    std_val = np.nanstd(psd_slice)
    max_val = np.nanmax(local_slice)

    # ---- 预筛选：max - median > 2σ 时才尝试拟合 ----
    if max_val - median_val <= 2 * std_val:
        continue

    valid = ~np.isnan(psd_slice) & ~np.isnan(omega_ctrl_axis)
    if np.sum(valid) < 10:
        continue

    x_data = omega_ctrl_axis[valid]
    y_data = psd_slice[valid]

    # ---- 初始值估计 ----
    gamma_guess = GAMMA_GUESS
    baseline_guess = median_val
    Amp_guess = estimate_amp_guess(max_val - median_val, gamma_guess, SwFreq)
    dw_guess = 0.0

    try:
        popt, pcov = curve_fit(
            lambda x, g, A, D, dw: lorentzian_vs_omega(x, g, A, D, dw, SwFreq),
            x_data, y_data,
            p0=[gamma_guess, Amp_guess, baseline_guess, dw_guess],
            maxfev=5000,
        )
        # Amp 和 gamma 取绝对值
        popt_list[j, :] = [np.abs(popt[0]), np.abs(popt[1]), popt[2], popt[3]]
        perr_list[j, :] = np.sqrt(np.diag(pcov))
        fit_mask[j] = True
    except (RuntimeError, ValueError) as e:
        pass  # 拟合失败，保留 NaN

fitted_count = np.sum(fit_mask)
print(f"拟合完成: {fitted_count}/{n_freq} 频率点拟合成功 ({100*fitted_count/n_freq:.1f}%)")

# [经验] 失败点用线性插值填充（仅在脊线附近的合理频率范围内）
FIT_OMEGA_MARGIN = 0.5  # 脊线外扩 50%
f_lo_fit = max(0, omega_ctrl[0] * (1 - FIT_OMEGA_MARGIN))
f_hi_fit = omega_ctrl[-1] * (1 + FIT_OMEGA_MARGIN)

if fitted_count > 0 and fitted_count < n_freq:
    from scipy.interpolate import interp1d

    for param_idx in range(4):
        near_ridge = (freq_axis >= f_lo_fit) & (freq_axis <= f_hi_fit)
        mask_near = fit_mask & near_ridge
        if np.sum(mask_near) > 2:
            interp_func = interp1d(
                np.where(mask_near)[0], popt_list[mask_near, param_idx],
                kind="linear", fill_value="extrapolate",
            )
            fill_idx = np.where(~fit_mask & near_ridge)[0]
            for idx in fill_idx:
                popt_list[idx, param_idx] = float(interp_func(idx))

    print("脊线附近失败点已用线性插值填充，远离脊线的保持 NaN")

# 提取噪声谱
S_beta = popt_list[:, 1].copy()  # Amp → 可控噪声
N_S1   = popt_list[:, 2].copy()  # D → 不可控噪声

# 保存拟合结果
np.savez(
    results_dir / "popt_fit.npz",
    popt=popt_list,
    perr=perr_list,
    fit_mask=fit_mask,
    freq_axis=freq_axis,
    param_names=["gamma", "Amp", "D", "dw"],
)

# [经验] 保存 noise_spectra.npz 供最优控制设计 Notebook 加载
np.savez(
    results_dir / "noise_spectra.npz",
    S_beta=S_beta,
    N_S1=N_S1,
    freq_axis=freq_axis,
    amplitudes=amplitudes_used,
    omega_ctrl=omega_ctrl,
)

noise_csv = np.column_stack([freq_axis, S_beta, N_S1])
np.savetxt(
    results_dir / "noise_spectra.csv",
    noise_csv,
    delimiter=",",
    header="frequency_Hz,S_beta_controllable_V2_per_Hz,N_S1_uncontrollable_V2_per_Hz",
    comments="",
)

print(f"\n噪声谱已保存: {results_dir / 'noise_spectra.npz'}")
print(f"噪声谱 CSV 已保存: {results_dir / 'noise_spectra.csv'}")
print(f"  S_beta (可控噪声): {S_beta.shape}, 有效: {np.sum(~np.isnan(S_beta))} 点")
print(f"  N_S1 (不可控噪声): {N_S1.shape}, 有效: {np.sum(~np.isnan(N_S1))} 点")
print(f"  freq_axis: {freq_axis[0]:.0f} - {freq_axis[-1]:.0f} Hz")

# %% Cell 22
# ===== 绘制结果图 =====

# ---- 图 1: PSD 二维伪彩图（纵轴映射为 Ω_Ctrl，聚焦脊线区域） ----
fig1, ax1 = plt.subplots(figsize=(10, 6))
psd_log = np.log10(np.maximum(psd_matrix, 1e-20))
# 百分位截断：裁剪颜色范围，排除极大噪声峰干扰，凸显脊背
vmin_pct, vmax_pct = 5, 95
vmin = np.nanpercentile(psd_log, vmin_pct)
vmax = np.nanpercentile(psd_log, vmax_pct)
print(f"PSD log10 颜色范围: [{vmin:.2f}, {vmax:.2f}] (P{vmin_pct}–P{vmax_pct})")
# 纵轴从幅度映射为 Ω_Ctrl
omega_min_1, omega_max_1 = omega_ctrl[0], omega_ctrl[-1]
extent = [freq_axis[0], freq_axis[-1], omega_min_1, omega_max_1]
im = ax1.imshow(psd_log, aspect="auto", origin="lower",
                extent=extent, cmap="inferno",
                vmin=vmin, vmax=vmax)
ax1.set_xlim(0, omega_max_1)
# 叠加标定脊线（对角线上 Ω_Ctrl = ω）
ax1.plot([omega_min_1, omega_max_1], [omega_min_1, omega_max_1],
         "c--", linewidth=1.5, label=f"$\\Omega_{{\\mathrm{{Ctrl}}}}$ = {k:.0f}·V + {b:.0f}")
ax1.set_xlabel("Frequency (Hz)")
ax1.set_ylabel("$\\Omega_{\\mathrm{Ctrl}}$ (Hz)")
ax1.set_title("PSD $S_{S_1}(\\omega, \\Omega_\\mathrm{Ctrl})$ (zoomed to ridge region)")
cb1 = fig1.colorbar(im, ax=ax1, label="$\\log_{10}$ PSD (V²/Hz)")
fig1.tight_layout()
fig1.savefig(results_dir / "noise_spectrum_2d.png", dpi=150)
print(f"PSD 伪彩图已保存")

# ---- 图 2: 提取的噪声谱 ----
fig2, (ax2a, ax2b) = plt.subplots(1, 2, figsize=(14, 5))

ax2a.loglog(freq_axis, S_beta, "b-", linewidth=1.5)
ax2a.set_xlabel("Frequency (Hz)")
ax2a.set_ylabel("$S_\\beta(\\omega)$ (V²/Hz)")
ax2a.set_title("Controllable Noise Spectrum $S_\\beta(\\omega)$")
ax2a.grid(True, alpha=0.3)

ax2b.loglog(freq_axis, N_S1, "r-", linewidth=1.5)
ax2b.set_xlabel("Frequency (Hz)")
ax2b.set_ylabel("$N_{S_1}(\\omega)$ (V²/Hz)")
ax2b.set_title("Uncontrollable Noise $N_{S_1}(\\omega)$")
ax2b.grid(True, alpha=0.3)

fig2.tight_layout()
fig2.savefig(results_dir / "noise_spectra_extracted.png", dpi=150)
print(f"提取的噪声谱已保存")

plt.show()
print(f"\n所有图表已保存至: {results_dir}")

# %% Cell 24
# ===== 诊断：分析 PSD 图中斜线的斜率（正负斜率均检测）=====
# 对 PSD 矩阵沿各种方向做线积分（Radon 变换思想），
# 同时检测正斜率（ω ∝ Ω_Ctrl）和负斜率（ω + k·Ω_Ctrl = const）特征。

psd_for_analysis = np.log10(np.maximum(psd_matrix, 1e-20))
omega_max_diag = omega_ctrl[-1]

# ---- 方法: 在 (Ω_Ctrl, ω) 平面上沿直线做线积分 ----
# 参数化: ω = s · Ω_Ctrl + offset
# 正斜率 s > 0: 过原点的射线族
# 负斜率 s < 0: ω = -s · Ω_Ctrl + offset，offset 为 ω 轴截距
#
# 对固定截距 offset，扫描斜率 s，找积分最强的 s 值

# 先检测负斜率特征：固定 offset，扫描 s
# 负斜率线的特征：Ω_Ctrl 增大时 ω 减小 → 在更低的 PSD 频率上响应更强

# ---- 1. 沿 ω = -Ω_Ctrl + offset 方向积分（斜率 -1 附近）----
# 对每个 offset (ω 轴截距)，沿 ω = -Ω_Ctrl + offset 收集数据
offset_candidates = np.linspace(freq_axis[0], freq_axis[-1], 300)
score_neg1 = np.zeros_like(offset_candidates)

for idx_off, offset in enumerate(offset_candidates):
    w_target = -omega_ctrl + offset
    mask = (w_target >= freq_axis[0]) & (w_target <= freq_axis[-1])
    if np.sum(mask) < 10:
        continue
    score = 0
    for i in np.where(mask)[0]:
        j = np.argmin(np.abs(freq_axis - w_target[i]))
        score += psd_for_analysis[i, j]
    score_neg1[idx_off] = score / np.sum(mask)

# ---- 2. 通用负斜率检测：ω = s_neg · Ω_Ctrl + offset，s_neg < 0 ----
# 对每个斜率 s_neg 和 offset 的二维扫描
# 简化：扫描 s_neg 从 -0.2 到 -3.0（步长 0.1），对每个 s_neg 找最优 offset

s_neg_candidates = -np.linspace(0.2, 3.0, 30)
best_scores_neg = np.zeros_like(s_neg_candidates)

for idx_s, s_neg in enumerate(s_neg_candidates):
    offset_scores = np.zeros_like(offset_candidates)
    for idx_off, offset in enumerate(offset_candidates):
        w_target = s_neg * omega_ctrl + offset
        mask = (w_target >= freq_axis[0]) & (w_target <= freq_axis[-1])
        if np.sum(mask) < 10:
            offset_scores[idx_off] = -np.inf
            continue
        score = 0
        for i in np.where(mask)[0]:
            j = np.argmin(np.abs(freq_axis - w_target[i]))
            score += psd_for_analysis[i, j]
        offset_scores[idx_off] = score / np.sum(mask)
    best_scores_neg[idx_s] = np.max(offset_scores)

# ---- 3. 正斜率检测（已有的，补充） ----
slope_candidates = np.linspace(0.2, 3.0, 200)
integration_score = np.zeros_like(slope_candidates)
ridge_lower = omega_ctrl * 0.3
ridge_upper = omega_ctrl * 2.5

for idx_s, s in enumerate(slope_candidates):
    w_target = s * omega_ctrl
    mask = (w_target >= ridge_lower) & (w_target <= ridge_upper)
    if np.sum(mask) < 10:
        continue
    score = 0
    for i in np.where(mask)[0]:
        j = np.argmin(np.abs(freq_axis - w_target[i]))
        score += psd_for_analysis[i, j]
    integration_score[idx_s] = score / np.sum(mask)

# ---- 汇总并排序检测到的特征 ----
from scipy.signal import find_peaks

# 正斜率峰值
peaks_pos, props_pos = find_peaks(integration_score, prominence=0.5, height=0)
# 负斜率峰值
peaks_neg, props_neg = find_peaks(best_scores_neg, prominence=0.5, height=0)

# 找出负斜率中峰值对应的具体 offset
detected_features = []

# 添加正斜率特征
for p in peaks_pos:
    s = slope_candidates[p]
    h = integration_score[p]
    detected_features.append({"type": "positive", "slope": s, "height": h, "desc": f"ω = {s:.2f}·Ω_Ctrl"})

# 添加负斜率特征
for p in peaks_neg:
    s_neg = s_neg_candidates[p]
    h = best_scores_neg[p]
    detected_features.append({"type": "negative", "slope": s_neg, "height": h, "desc": f"ω = {s_neg:.2f}·Ω_Ctrl + offset"})

# 按强度排序
detected_features.sort(key=lambda x: x["height"], reverse=True)

# ---- 绘制诊断图 ----
fig_diag, axes_diag = plt.subplots(2, 2, figsize=(14, 10))

# (a) 正斜率积分
ax0 = axes_diag[0, 0]
ax0.plot(slope_candidates, integration_score, 'k-', linewidth=1)
for feat in detected_features[:5]:
    if feat["type"] != "positive": continue
    ax0.axvline(feat["slope"], color='r', linestyle='--', alpha=0.5)
    ax0.text(feat["slope"], feat["height"]*1.1, f's={feat["slope"]:.2f}', fontsize=9, ha='center')
ax0.axvline(1.0, color='c', linestyle='-', alpha=0.7, label='s=1 (Main ridge)')
ax0.set_xlabel("Slope s (ω = s · Ω_Ctrl)")
ax0.set_ylabel("Integrated intensity")
ax0.set_title("(a) Positive slope analysis")
ax0.legend(fontsize=8)
ax0.grid(True, alpha=0.3)

# (b) 负斜率积分
ax1 = axes_diag[0, 1]
ax1.plot(s_neg_candidates, best_scores_neg, 'k-', linewidth=1)
for feat in detected_features[:5]:
    if feat["type"] != "negative": continue
    ax1.axvline(feat["slope"], color='r', linestyle='--', alpha=0.5)
    ax1.text(feat["slope"], feat["height"]*1.1, f's={feat["slope"]:.2f}', fontsize=9, ha='center')
ax1.set_xlabel("Slope s_neg (ω = s_neg·Ω_Ctrl + offset)")
ax1.set_ylabel("Best offset-integrated intensity")
ax1.set_title("(b) Negative slope analysis")
ax1.grid(True, alpha=0.3)

# (c) PSD 原图 + 标注检测到的特征
ax2 = axes_diag[1, 0]
extent_d = [freq_axis[0], freq_axis[-1], omega_ctrl[0], omega_ctrl[-1]]
ax2.imshow(psd_for_analysis, aspect="auto", origin="lower",
           extent=extent_d, cmap="inferno")
ax2.set_xlim(0, omega_max_diag)

# 标注主脊线
ax2.plot([omega_ctrl[0], omega_ctrl[-1]], [omega_ctrl[0], omega_ctrl[-1]],
         "c-", linewidth=2, alpha=0.8, label="s=+1 (Main ridge)")

# 标注检测到的正斜率特征
colors_line = ['r--', 'y--', 'g--', 'm--', 'w--']
for idx_feat, feat in enumerate(detected_features[:8]):
    if feat["type"] == "positive" and abs(feat["slope"] - 1.0) > 0.05:
        s = feat["slope"]
        ax2.plot([omega_ctrl[0], omega_ctrl[-1]],
                 [omega_ctrl[0]*s, omega_ctrl[-1]*s],
                 colors_line[idx_feat % len(colors_line)],
                 linewidth=1.2, alpha=0.6,
                 label=f's=+{s:.2f}')
ax2.set_xlabel("Frequency ω (Hz)")
ax2.set_ylabel("Ω_Ctrl (Hz)")
ax2.set_title("(c) PSD with detected features")
ax2.legend(fontsize=6, loc='upper left')

# (d) 负斜率特征标注（ω = s·Ω_Ctrl + offset，选取最优 offset）
ax3 = axes_diag[1, 1]
ax3.imshow(psd_for_analysis, aspect="auto", origin="lower",
           extent=extent_d, cmap="inferno")
ax3.set_xlim(0, omega_max_diag)
ax3.plot([omega_ctrl[0], omega_ctrl[-1]], [omega_ctrl[0], omega_ctrl[-1]],
         "c-", linewidth=2, alpha=0.8, label="s=+1 (Main ridge)")

# 标注负斜率特征（选最优 offset 画线）
for idx_feat, feat in enumerate(detected_features[:8]):
    if feat["type"] != "negative": continue
    s_neg = feat["slope"]
    # 重新找这个斜率对应的最优 offset
    best_off = 0; best_sc = -np.inf
    for offset in offset_candidates:
        w_target = s_neg * omega_ctrl + offset
        mask = (w_target >= freq_axis[0]) & (w_target <= freq_axis[-1])
        if np.sum(mask) < 10: continue
        sc = 0
        for i in np.where(mask)[0]:
            j = np.argmin(np.abs(freq_axis - w_target[i]))
            sc += psd_for_analysis[i, j]
        sc /= np.sum(mask)
        if sc > best_sc:
            best_sc = sc; best_off = offset

    # 画该负斜率线
    w_line = s_neg * omega_ctrl + best_off
    mask_line = (w_line >= freq_axis[0]) & (w_line <= freq_axis[-1])
    if np.sum(mask_line) > 10:
        ax3.plot(omega_ctrl[mask_line], w_line[mask_line],
                 colors_line[idx_feat % len(colors_line)],
                 linewidth=1.5, alpha=0.7,
                 label=f's={s_neg:.2f}, off={best_off:.0f}Hz')

ax3.set_xlabel("Frequency ω (Hz)")
ax3.set_ylabel("Ω_Ctrl (Hz)")
ax3.set_title("(d) Negative-slope features on PSD")
ax3.legend(fontsize=6, loc='upper left')

fig_diag.tight_layout()
fig_diag.savefig(results_dir / "diagonal_analysis.png", dpi=150)
plt.show()

# ---- 打印结果解读 ----
print("\n" + "=" * 65)
print("斜线检测结果（按强度排序）")
print("=" * 65)
n_printed = 0
for feat in detected_features[:12]:
    if feat["type"] == "positive" and abs(feat["slope"] - 1.0) < 0.05:
        print(f"  ★ 正斜率 s = {feat['slope']:.2f}  ← 主脊线 (洛伦兹共振)")
        continue
    n_printed += 1
    if feat["type"] == "positive":
        print(f"  [{n_printed}] 正斜率 s = {feat['slope']:.2f}", end="")
        s = feat["slope"]
        if abs(s - 2.0) < 0.15: print(" ← 可能: 二次谐波 (2Ω_Ctrl)")
        elif abs(s - 3.0) < 0.2: print(" ← 可能: 三次谐波 (3Ω_Ctrl)")
        elif abs(s - 0.5) < 0.08: print(" ← 可能: 次谐波 (Ω_Ctrl/2)")
        elif abs(s - 1.5) < 0.1: print(" ← 可能: 3/2 谐波")
        else: print()
    else:
        print(f"  [{n_printed}] 负斜率 s = {feat['slope']:.2f}", end="")
        s = abs(feat["slope"])
        if abs(s - 1.0) < 0.15: print(" ← 可能: ω+Ω_Ctrl=const (差频混合)")
        elif abs(s - 2.0) < 0.15: print(" ← 可能: ω+2Ω_Ctrl=const")
        elif abs(s - 0.5) < 0.08: print(" ← 可能: 2ω+Ω_Ctrl=const")
        else: print(f" (offset ≈ 待查)")

print("\n验证建议:")
print("  1. 在 PSD 图上取斜线上几个点，验算 ω 和 Ω_Ctrl 的关系")
print("  2. 关掉 XY 控制场（Ω_Ctrl=0），看特征是否消失 → 判断是否与控制场相关")
print("  3. 改变 Pump 调制频率（90kHz），看特征频率偏移 → 判断是否与 Pump 相关")
