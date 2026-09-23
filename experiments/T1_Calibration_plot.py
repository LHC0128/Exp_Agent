# %% [markdown] Cell 0
# # T₁ 数据分析 & 可视化
#
# 支持两种模式：
# - **单功率**：从 `t1_measurement.npz` 加载 PDB 衰减波形，指数拟合 T₁
# - **多功率扫描**：从 `power_scan_results.npz` 加载 T₁ 序列，线性拟合 T₁⁻¹ vs P
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
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")
from scipy.optimize import curve_fit

print("导入完成（离线分析模式，无需仪器）")

# %% Cell 2
# ========== 选择数据目录 & 分析模式 ==========
# 修改为实际路径:
USE_LATEST = True  # True = 自动用最新数据; False = 用下面指定的路径
if USE_LATEST:
    base = project_root / "data" / "T1_Calibration"
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    if dirs:
        DATA_DIR = dirs[0]
    else:
        raise FileNotFoundError(f"未找到数据目录: {base}")
else:
    DATA_DIR = project_root / "data" / "T1_Calibration" / "0529_1405_test"
# DATA_DIR = Path("d:/Code/exp_agent/data/T1_Calibration/MMDD_HHMM_test")

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)

# 自动检测: 优先多功率扫描，其次单功率
POWER_SCAN_PATH = raw_dir / "power_scan_results.npz"
SINGLE_PATH = raw_dir / "t1_measurement.npz"
DO_POWER_SCAN = POWER_SCAN_PATH.exists()

print(f"数据目录: {DATA_DIR}")
print(f"分析模式: {'多功率扫描' if DO_POWER_SCAN else '单功率测量'}")

# %% Cell 3
# ---- 指数衰减模型 ----
def exp_decay(t, A, T1, C):
    """A·exp(-t/T1) + C"""
    return A * np.exp(-t / T1) + C

# %% Cell 4
# ========== 加载数据 ==========

if DO_POWER_SCAN:
    loaded = np.load(POWER_SCAN_PATH)
    probe_powers = loaded["probe_power"]
    T1_values = loaded["T1"]
    T1_errs = loaded["T1_err"]
    print(f"加载 {len(probe_powers)} 个功率点的 T₁ 数据")

    # ---- 图1: T₁ vs P_probe ----
    fig1, ax1 = new_figure(kind="standard", height_mm=65)
    ax1.errorbar(probe_powers, np.array(T1_values) * 1e3,
                 yerr=np.array(T1_errs) * 1e3, fmt="o-", capsize=3)
    ax1.set_xlabel("Probe control voltage (V)")
    ax1.set_ylabel("$T_1$ (ms)")
    ax1.set_title("$T_1$ vs Probe Power")
    ax1.grid(False)
    fig1.set_layout_engine("constrained")
    save_figure(fig1, results_dir / "T1_vs_probe_power.png", close=False)
    print(f"图表已保存: {results_dir / 'T1_vs_probe_power.png'}")

    # ---- 图2: T₁⁻¹ vs P_probe 线性拟合 ----
    valid = ~np.isnan(T1_values) & (T1_values > 0)
    p_valid = probe_powers[valid]
    inv_T1 = 1.0 / T1_values[valid]
    inv_T1_err = T1_errs[valid] / (T1_values[valid] ** 2)

    if len(p_valid) >= 2:
        coeffs = np.polyfit(p_valid, inv_T1, 1, w=1.0 / np.maximum(inv_T1_err, 1e-6))
        poly = np.poly1d(coeffs)
        T1_0_inv = coeffs[1]
        alpha = coeffs[0]
        T1_0 = 1.0 / T1_0_inv if T1_0_inv > 0 else np.inf

        fig2, ax2 = new_figure(kind="standard", height_mm=65)
        ax2.errorbar(p_valid, inv_T1, yerr=inv_T1_err, fmt="o", capsize=3, label="Data")
        p_fit = np.linspace(p_valid.min(), p_valid.max(), 50)
        ax2.plot(p_fit, poly(p_fit), "r--",
                 label=f"Fit: $T_{{1,0}}^{{-1}}$={T1_0_inv:.2f} s$^{{-1}}$, "
                       f"$\\alpha$={alpha:.3f}")
        ax2.set_xlabel("Probe control voltage (V)")
        ax2.set_ylabel("$T_1^{-1}$ (s$^{-1}$)")
        ax2.set_title("$T_1^{-1}$ vs Probe Power")
        ax2.legend()
        ax2.grid(False)
        fig2.set_layout_engine("constrained")
        save_figure(fig2, results_dir / "inv_T1_vs_probe_power.png", close=False)
        print(f"图表已保存: {results_dir / 'inv_T1_vs_probe_power.png'}")

        fit_result = {
            "T1_0_inv": float(T1_0_inv), "T1_0_ms": float(T1_0 * 1e3),
            "alpha": float(alpha),
            "probe_power": p_valid.tolist(),
            "inv_T1": inv_T1.tolist(), "inv_T1_err": inv_T1_err.tolist(),
        }
        with open(results_dir / "analysis.yaml", "w") as f:
            yaml.dump(fit_result, f, default_flow_style=False, allow_unicode=True)
        print(f"\n本征弛豫率 T₁₀⁻¹ = {T1_0_inv:.2f} s⁻¹")
        print(f"本征弛豫时间 T₁₀ = {T1_0*1e3:.1f} ms")
        print(f"光致退相干系数 α = {alpha:.4f} s⁻¹/V")
    else:
        print("⚠ 有效数据点不足，无法进行线性拟合")

    # ---- 图3: 多功率衰减曲线 + 拟合叠加 ----
    wave_path = raw_dir / "power_scan_waveforms.npz"
    if wave_path.exists():
        w_loaded = np.load(wave_path)
        t_full = w_loaded["t_axis"]
        v_stack = w_loaded["vdiff_stack"]
        popt_stack = w_loaded["popt_stack"]
        powers = w_loaded["probe_power"]
        n_pts = len(powers)

        # 截取 t ≥ 0
        mask = t_full >= 0
        t_decay = t_full[mask]

        n_cols = min(3, n_pts)
        n_rows = int(np.ceil(n_pts / n_cols))
        fig3, axes3 = new_figure(nrows=n_rows, ncols=n_cols, kind="wide", height_mm=max(65, 55 * (n_rows)))
        if n_pts == 1:
            axes3 = np.array([axes3])
        axes3 = axes3.flatten()

        for idx in range(n_pts):
            ax = axes3[idx]
            v_decay = v_stack[idx][mask]
            ax.plot(t_decay * 1e3, v_decay, linewidth=0.4, color="steelblue", label="Data")
            # 叠加拟合曲线
            if not np.isnan(popt_stack[idx]).any():
                v_fit = exp_decay(t_decay, *popt_stack[idx])
                ax.plot(t_decay * 1e3, v_fit, "r--", linewidth=1.0)
                T1_i = abs(popt_stack[idx][1]) * 1e3
                ax.text(0.97, 0.92, f"$T_1$ = {T1_i:.1f} ms", transform=ax.transAxes,
                        ha="right", va="top", fontsize=8, color="red")
            ax.set_title(f"Probe = {powers[idx]:.3f} V")
            ax.set_xlabel("Time (ms)")
            ax.set_ylabel("V_diff (V)")
            ax.grid(False)

        for idx in range(n_pts, len(axes3)):
            axes3[idx].set_visible(False)

        fig3.suptitle("$T_1$ decay curves — all probe settings", fontsize=8)
        fig3.set_layout_engine("constrained")
        save_figure(fig3, results_dir / "T1_all_decay_curves.png", close=False)
        print(f"图表已保存: {results_dir / 'T1_all_decay_curves.png'}")
    else:
        print("⚠ 未找到 power_scan_waveforms.npz，无法绘制衰减曲线（需新版采集程序）")

else:
    # ---- 单功率模式 ----
    if not SINGLE_PATH.exists():
        raise FileNotFoundError(f"数据文件不存在: {SINGLE_PATH}")

    loaded = np.load(SINGLE_PATH, allow_pickle=True)
    t_axis = loaded["t_axis"]
    vdiff = loaded["vdiff_avg"]
    T1 = float(loaded["T1"])
    T1_err = float(loaded["T1_err"])
    popt = loaded["popt"]
    print(f"加载单功率数据: T₁ = {T1*1e3:.2f} ± {T1_err*1e3:.2f} ms")

    # ---- 图1: PDB 衰减 + 拟合 ----
    t_fit_plot = t_axis[t_axis >= 0]
    fig1, ax1 = new_figure(kind="standard", height_mm=65)
    ax1.plot(t_axis * 1e3, vdiff, linewidth=0.5, label="PDB $V_{\\rm diff}$")
    if not np.isnan(T1):
        ax1.plot(t_fit_plot * 1e3, exp_decay(t_fit_plot, *popt), "r--", linewidth=1.5,
                 label=f"Fit: $T_1$ = {T1*1e3:.2f} $\\pm$ {T1_err*1e3:.2f} ms")
    ax1.axvline(0, color="gray", linestyle=":", alpha=0.5, label="$t_0$ (Pump OFF)")
    ax1.set_xlabel("Time (ms)")
    ax1.set_ylabel("PDB Signal $V_{\\rm diff}$ (V)")
    ax1.set_title("PDB Differential Signal Decay & $T_1$ Fit")
    ax1.legend()
    ax1.grid(False)
    fig1.set_layout_engine("constrained")
    save_figure(fig1, results_dir / "PDB_T1_decay.png", close=False)
    print(f"图表已保存: {results_dir / 'PDB_T1_decay.png'}")

    # ---- 图2: 拟合残差 ----
    if not np.isnan(T1):
        fig2, ax2 = new_figure(kind="standard", height_mm=65)
        residual = vdiff[t_axis >= 0] - exp_decay(t_fit_plot, *popt)
        ax2.plot(t_fit_plot * 1e3, residual * 1e3, linewidth=0.5)
        ax2.set_xlabel("Time (ms)")
        ax2.set_ylabel("Residual (mV)")
        ax2.set_title("Fit Residual ($t \\geq 0$)")
        ax2.grid(False)
        fig2.set_layout_engine("constrained")
        save_figure(fig2, results_dir / "T1_fit_residual.png", close=False)
        print(f"图表已保存: {results_dir / 'T1_fit_residual.png'}")

    print(f"\nT₁ = {T1*1e3:.2f} ± {T1_err*1e3:.2f} ms")

plt.show()
