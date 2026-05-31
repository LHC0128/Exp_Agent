# %% [markdown] Cell 0
# # T₂ 数据分析 & 可视化
#
# 支持两种模式：
# - **单功率**：从 `fid_waveforms.npz` 加载 FID 波形，阻尼振荡拟合 T₂
# - **多功率扫描**：从 `power_scan_waveforms.npz` 加载各功率 FID，逐点拟合 T₂
# 无需连接任何仪器。

# %% Cell 1
from pathlib import Path
import sys
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

print("导入完成（离线分析模式，无需仪器）")

# %% Cell 2
# ---- 阻尼振荡拟合模型 ----
def damped_oscillation(t, A, T2, f_L, phi, offset):
    """V(t) = A * exp(-t/T2) * cos(2π·f_L·t + φ) + offset"""
    return A * np.exp(-t / T2) * np.cos(2 * np.pi * f_L * t + phi) + offset


def fit_T2(t_fid, v_fid, f_L_guess=90000):
    """两步法拟合 T₂：FFT 定频 + Hilbert 初值 + 阻尼振荡完整拟合（含 offset）。

    返回 (T2_ms, T2_err_ms, popt, success)
    popt = [A, T2, f_L, phi, offset] 可直接用于 damped_oscillation()
    """
    dt = t_fid[1] - t_fid[0]
    fs = 1.0 / dt

    # 1. FFT 精确定频
    n = len(v_fid)
    freqs = np.fft.rfftfreq(n, dt)
    spectrum = np.abs(np.fft.rfft(v_fid))
    idx_lo = max(1, int((f_L_guess - 5000) * n / fs))
    idx_hi = min(len(spectrum) - 1, int((f_L_guess + 5000) * n / fs))
    f_peak = freqs[idx_lo + np.argmax(spectrum[idx_lo:idx_hi+1])]

    # 2. Hilbert 包络 → 初值 (A, T2)
    from scipy.signal import hilbert
    analytic = hilbert(v_fid - np.mean(v_fid))
    envelope = np.abs(analytic)
    A_g = envelope[0]
    T2_g = 0.003

    def exp_env(t, A, T2, C):
        return A * np.exp(-t / T2) + C

    fit_len = int(len(envelope) * 0.8)
    try:
        popt_env, _ = curve_fit(
            exp_env, t_fid[:fit_len], envelope[:fit_len],
            p0=[A_g, T2_g, envelope[-1]],
            bounds=([0, 1e-6, -1], [A_g * 10, 0.5, 1]), maxfev=5000,
        )
        A_init, T2_init = abs(popt_env[0]), abs(popt_env[1])
    except Exception:
        A_init, T2_init = A_g, T2_g

    # 3. 阻尼振荡完整拟合（频率紧束，offset 自由）
    offset_init = np.mean(v_fid)
    phi_init = 0.0
    # 频率紧束到 FFT 峰值 ±200 Hz，迫使拟合器不漂移
    df_tight = 200
    f_lo = max(f_peak - df_tight, f_L_guess * 0.99)
    f_hi = min(f_peak + df_tight, f_L_guess * 1.01)

    try:
        popt, pcov = curve_fit(
            damped_oscillation, t_fid, v_fid,
            p0=[A_init, T2_init, f_peak, phi_init, offset_init],
            bounds=(
                [0, 1e-6, f_lo, -np.pi, -1.0],
                [A_init * 5, 0.1, f_hi, np.pi, 1.0],
            ),
            maxfev=20000,
        )
        T2_s = abs(popt[1])
        T2_err = np.sqrt(pcov[1, 1]) if pcov[1, 1] > 0 else 0.0
        success = T2_err < T2_s * 2
        return T2_s * 1000, T2_err * 1000, popt, success
    except Exception:
        return np.nan, np.nan, [np.nan] * 5, False


# %% Cell 3
# ========== 选择数据目录 & 分析模式 ==========
USE_LATEST = True  # True = 自动用最新数据; False = 用下面指定的路径
if USE_LATEST:
    base = project_root / "data" / "T2_Calibration"
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    if dirs:
        DATA_DIR = dirs[0]
    else:
        raise FileNotFoundError(f"未找到数据目录: {base}")
else:
    DATA_DIR = project_root / "data" / "T2_Calibration" / "0529_1442_optical_FID"
# DATA_DIR = Path("d:/Code/exp_agent/data/T2_Calibration/MMDD_HHMM_optical_FID")

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)

# 自动检测模式
POWER_SCAN_PATH = raw_dir / "power_scan_waveforms.npz"
SINGLE_PATH = raw_dir / "fid_waveforms.npz"
DO_POWER_SCAN = POWER_SCAN_PATH.exists()

print(f"数据目录: {DATA_DIR}")
print(f"分析模式: {'多功率扫描' if DO_POWER_SCAN else '单功率测量'}")

plt.rcParams.update({
    "figure.dpi": 120, "font.size": 11,
    "axes.labelsize": 12, "axes.titlesize": 13,
})

# %% Cell 4
# ========== 单功率模式 ==========
if not DO_POWER_SCAN:
    if not SINGLE_PATH.exists():
        raise FileNotFoundError(f"数据文件不存在: {SINGLE_PATH}")

    loaded = np.load(SINGLE_PATH)
    avg_waveform = loaded["avg_waveform"]
    time_axis = loaded["time"]
    waveforms_array = loaded["waveforms"]
    RF_GATE_FREQ = float(loaded["rf_gate_freq"])
    TRIGGER_SLOPE = str(loaded.get("trigger_slope", "RISing"))
    BURST_DURATION = float(loaded.get("burst_duration", 0.0))

    print(f"时间轴: {len(time_axis)} 点, {time_axis[0]*1000:.3f} ~ {time_axis[-1]*1000:.1f} ms")
    print(f"波形矩阵: {waveforms_array.shape}")

    # 确定 FID 起点
    if TRIGGER_SLOPE == "FALLing":
        t_fid, v_fid = time_axis, avg_waveform
    else:
        mask = time_axis > BURST_DURATION
        t_fid = (time_axis[mask] - BURST_DURATION)
        v_fid = avg_waveform[mask]

    T2_ms, T2_err_ms, popt, fit_success = fit_T2(t_fid, v_fid, RF_GATE_FREQ)
    A_fit, T2_fit, f_L_fit, phi_fit, offset_fit = popt

    print(f"\nT₂ = {T2_ms:.3f} ± {T2_err_ms:.3f} ms")
    print(f"f_L = {f_L_fit:.1f} Hz (设定 {RF_GATE_FREQ} Hz)")

    # ---- 四面板图 ----
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    ax = axes[0, 0]
    ax.plot(t_fid * 1000, v_fid, linewidth=0.4, color='steelblue', label='Data')
    if fit_success:
        v_fit = damped_oscillation(t_fid, *popt)
        ax.plot(t_fid * 1000, v_fit, '--', linewidth=1.2, color='darkorange',
                label=f'T₂ = {T2_ms:.2f} ms')
        env = A_fit * np.exp(-t_fid / T2_fit) + offset_fit
        ax.plot(t_fid * 1000, env, ':', linewidth=1.0, color='red', alpha=0.7)
        ax.plot(t_fid * 1000, -A_fit * np.exp(-t_fid / T2_fit) + offset_fit,
                ':', linewidth=1.0, color='red', alpha=0.7)
    ax.set_xlabel('Time (ms)'); ax.set_ylabel('PD Voltage (V)')
    ax.set_title(f'Optical FID — T₂ = {T2_ms:.2f} ms')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    ax = axes[0, 1]
    zoom = min(0.005, t_fid[-1])
    mz = t_fid <= zoom
    ax.plot(t_fid[mz] * 1000, v_fid[mz], linewidth=0.4, color='steelblue')
    if fit_success:
        ax.plot(t_fid[mz] * 1000, damped_oscillation(t_fid[mz], *popt),
                '--', linewidth=1.2, color='darkorange')
    ax.set_xlabel('Time (ms)'); ax.set_ylabel('PD Voltage (V)')
    ax.set_title(f'First {zoom*1000:.0f} ms (zoom)')
    ax.grid(True, alpha=0.3)

    ax = axes[1, 0]
    if fit_success:
        residual = v_fid - damped_oscillation(t_fid, *popt)
        ax.plot(t_fid * 1000, residual, linewidth=0.3, color='gray')
        ax.axhline(0, color='black', linestyle=':', linewidth=0.8)
        ax.set_xlabel('Time (ms)'); ax.set_ylabel('Residual (V)')
        ax.set_title(f'Residual (RMS = {np.std(residual):.4f} V)')
        ax.grid(True, alpha=0.3)

    ax = axes[1, 1]
    n_show = min(waveforms_array.shape[0], 20)
    for j in range(n_show):
        ax.plot(time_axis * 1000, waveforms_array[j], linewidth=0.12, alpha=0.5, color='steelblue')
    ax.plot(time_axis * 1000, avg_waveform, linewidth=0.8, color='darkorange', label='Average')
    ax.set_xlabel('Time (ms)'); ax.set_ylabel('PD Voltage (V)')
    ax.set_title(f'All {int(n_show)} Shots + Average')
    ax.legend(); ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(results_dir / "T2_fid_analysis.png", dpi=150, bbox_inches='tight')
    plt.show()

    if fit_success:
        res = {"method": "optical_FID", "T2_ms": T2_ms, "T2_err_ms": T2_err_ms,
               "f_L_fit_Hz": float(f_L_fit), "f_L_set_Hz": float(RF_GATE_FREQ),
               "amplitude_V": float(A_fit), "offset_V": float(offset_fit)}
        with open(results_dir / "T2_results.yaml", "w", encoding="utf-8") as f:
            yaml.dump(res, f, default_flow_style=False, allow_unicode=True)

# %% Cell 5
# ========== 多功率模式 ==========
if DO_POWER_SCAN:
    w_loaded = np.load(POWER_SCAN_PATH)
    t_axis = w_loaded["t_axis"]
    avg_stack = w_loaded["avg_stack"]
    powers = w_loaded["probe_power"]
    n_pts = len(powers)

    # 尝试从单点文件读取元信息
    fid0_path = raw_dir / "fid_p00.npz"
    if fid0_path.exists():
        meta = np.load(fid0_path)
        RF_GATE_FREQ = float(meta["rf_gate_freq"])
    else:
        RF_GATE_FREQ = 90000

    print(f"加载 {n_pts} 个功率点, 时间轴 {len(t_axis)} 点")

    # ---- 逐点拟合 ----
    T2_vals = np.full(n_pts, np.nan)
    T2_errs = np.full(n_pts, np.nan)
    popt_all = []

    for idx in range(n_pts):
        T2_ms, T2_err_ms, popt, ok = fit_T2(t_axis, avg_stack[idx], RF_GATE_FREQ)
        T2_vals[idx] = T2_ms
        T2_errs[idx] = T2_err_ms
        popt_all.append(popt)
        status = "OK" if ok else "FAIL"
        print(f"  P={powers[idx]:.3f}V: T₂={T2_ms:.2f}±{T2_err_ms:.2f}ms [{status}]")

    # ---- 图1: T₂ vs Probe Power ----
    fig1, ax1 = plt.subplots(figsize=(7, 4.5))
    ax1.errorbar(powers, T2_vals, yerr=T2_errs, fmt="o-", capsize=3)
    ax1.set_xlabel("Probe Power (V)")
    ax1.set_ylabel("$T_2$ (ms)")
    ax1.set_title("$T_2$ vs Probe Power")
    ax1.grid(True, alpha=0.3)
    fig1.tight_layout()
    fig1.savefig(results_dir / "T2_vs_probe_power.png")
    print(f"图表已保存: {results_dir / 'T2_vs_probe_power.png'}")

    # ---- 图2: T₂⁻¹ vs Probe Power 线性拟合 ----
    valid = ~np.isnan(T2_vals) & (T2_vals > 0)
    p_valid = powers[valid]
    inv_T2 = 1.0 / T2_vals[valid]
    inv_T2_err = T2_errs[valid] / (T2_vals[valid] ** 2)

    if len(p_valid) >= 2:
        coeffs = np.polyfit(p_valid, inv_T2, 1, w=1.0 / np.maximum(inv_T2_err, 1e-6))
        poly = np.poly1d(coeffs)
        T2_0_inv = coeffs[1]
        alpha = coeffs[0]
        T2_0 = 1.0 / T2_0_inv if T2_0_inv > 0 else np.inf

        fig2, ax2 = plt.subplots(figsize=(7, 4.5))
        ax2.errorbar(p_valid, inv_T2, yerr=inv_T2_err, fmt="o", capsize=3, label="Data")
        p_fit = np.linspace(p_valid.min(), p_valid.max(), 50)
        ax2.plot(p_fit, poly(p_fit), "r--",
                 label=f"$T_{{2,0}}^{{-1}}$={T2_0_inv:.2f} ms$^{{-1}}$, $\\alpha$={alpha:.3f}")
        ax2.set_xlabel("Probe Power (V)")
        ax2.set_ylabel("$T_2^{-1}$ (ms$^{-1}$)")
        ax2.set_title("$T_2^{-1}$ vs Probe Power")
        ax2.legend(); ax2.grid(True, alpha=0.3)
        fig2.tight_layout()
        fig2.savefig(results_dir / "inv_T2_vs_probe_power.png")
        print(f"图表已保存: {results_dir / 'inv_T2_vs_probe_power.png'}")
        print(f"\n本征弛豫时间 T₂₀ = {T2_0:.2f} ms")
        print(f"光致退相干系数 α = {alpha:.4f} ms⁻¹/V")

        with open(results_dir / "analysis.yaml", "w") as f:
            yaml.dump({"T2_0_ms": float(T2_0), "alpha": float(alpha),
                       "probe_power": p_valid.tolist(), "inv_T2": inv_T2.tolist()}, f)

    # ---- 图3: 多功率 FID 衰减曲线叠加 ----
    n_cols = min(5, n_pts)
    n_rows = int(np.ceil(n_pts / n_cols))
    fig3, axes3 = plt.subplots(n_rows, n_cols, figsize=(3.5 * n_cols, 3 * n_rows))
    if n_pts == 1:
        axes3 = np.array([axes3])
    axes3 = axes3.flatten()

    for idx in range(n_pts):
        ax = axes3[idx]
        ax.plot(t_axis * 1000, avg_stack[idx], linewidth=0.4, color='steelblue')
        if not np.isnan(T2_vals[idx]):
            v_fit = damped_oscillation(t_axis, *popt_all[idx])
            ax.plot(t_axis * 1000, v_fit, 'r--', linewidth=1.0)
            ax.text(0.97, 0.92, f"T₂={T2_vals[idx]:.1f}ms", transform=ax.transAxes,
                    ha="right", va="top", fontsize=9, color="red")
        ax.set_title(f"Probe = {powers[idx]:.3f} V")
        ax.set_xlabel("Time (ms)"); ax.set_ylabel("PD Voltage (V)")
        ax.grid(True, alpha=0.3)

    for idx in range(n_pts, len(axes3)):
        axes3[idx].set_visible(False)

    fig3.suptitle("T₂ FID Decay Curves — All Probe Powers", fontsize=14, y=1.01)
    fig3.tight_layout()
    fig3.savefig(results_dir / "T2_all_fid_curves.png", dpi=150, bbox_inches="tight")
    print(f"图表已保存: {results_dir / 'T2_all_fid_curves.png'}")

plt.show()
