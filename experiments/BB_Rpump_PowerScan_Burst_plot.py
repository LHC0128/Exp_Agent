# %% [markdown] Cell 0
# # Bell-Bloom 周期泵浦 R_pump_eff 数据分析与可视化
#
# **无需连接任何仪器**，仅读取本地数据文件进行分析。
#
# 对每个 pump power DC 电压：
# 1. 从 SDS 数据中识别 burst 起止时刻 (从 CH2 同步信号的上升沿/下降沿)
# 2. 利用 Hilbert 变换提取 Bell-Bloom 信号的包络
# 3. 拟合 build-up 段: `S(t) = offset + A · (1 - exp(-(t - t0) / tau_BB))`
# 4. 拟合 dark decay 段: `S(t) = offset + A · exp(-(t - t0) / tau_dark)`
# 5. 计算 Gamma_BB, Gamma_dark, R_pump_eff = Gamma_BB - Gamma_dark
# 6. 汇总扫描结果：R_pump_eff vs Pump Power DC 电压
#
# 保存：
# - `results/analysis.yaml`
# - `results/transient_fit_power_scan.png`
# - `results/rpump_eff_vs_pump_power.png`
# - `results/residuals_power_scan.png`

# %% Cell 1
from pathlib import Path
import sys
# 自动定位项目根目录
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import json
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.signal import hilbert

print("导入完成（离线分析模式，无需仪器）")

# %% Cell 2
# ========== 拟合模型定义: 全振荡 S(t) = offset + A(t) · sin(omega·t + phi) ==========
# [关键] 用绝对时间拟合, 这样 sin 项与物理 Larmor 相位一致.
#       t0 / t1 由 npz 提供 (已知常量), 通过闭包注入, 模型函数只需 4 个拟合参数:
#       (offset, A_ampl, tau, omega, phi)
#       envelope 项用 np.maximum(0, t - t0) 防止 t<t0 时 exp 溢出.


def make_env_build_up(t0):
    """包络 build-up: S_env(t) = offset + A * (1 - exp(-(t-t0)/tau))"""
    def model(t_abs, offset, A_inf, tau_BB):
        t_eff = np.maximum(0, t_abs - t0)
        return offset + A_inf * (1.0 - np.exp(-t_eff / tau_BB))
    return model


def make_bb_decay(t1):
    """返回 Bell-Bloom dark decay 模型: S(t) = offset + A_0·e^(-(t-t1)/tau)·sin(omegat+phi)"""
    def model(t_abs, offset, A_0, tau_dark, omega, phi):
        t_eff = np.maximum(0, t_abs - t1)
        envelope = np.exp(-t_eff / tau_dark)
        return offset + A_0 * envelope * np.sin(omega * t_abs + phi)
    return model


# ---- 拟合边界 ----
TAU_BB_BOUNDS   = (1e-5,  0.05)                      # 10us ~ 50ms
TAU_DARK_BOUNDS = (1e-5,  0.05)                      # 10us ~ 50ms
LARMOR_OMEGA_BOUNDS = (2*np.pi*85e3, 2*np.pi*95e3)   # Larmor ±5%
PHASE_BOUNDS = (0.0, 2*np.pi)                        # 0 ~ 2π


def _try_fit(model, t, y, p0, bounds, maxfev=20000):
    """统一的拟合封装, 返回 popt / pcov 或 (None, None) 表示失败."""
    try:
        popt, pcov = curve_fit(model, t, y, p0=p0, bounds=bounds, maxfev=maxfev)
        return popt, pcov
    except Exception as e:
        print(f"    拟合失败: {e}")
        return None, None


print("[OK] 全振荡拟合模型定义完成 (offset, A, tau, omega, phi)")

# %% Cell 3
# ========== 选择数据目录 ==========
EXPERIMENT_TYPE = "bb_rpump_power_scan_burst"

USE_LATEST = True
if USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    if base.exists():
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        DATA_DIR = dirs[0] if dirs else None
    else:
        DATA_DIR = None
else:
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_tag"

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {DATA_DIR}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，"
        f"或设置 USE_LATEST=False 手动指定路径"
    )

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

# ---- 加载实验配置 ----
config_path = DATA_DIR / "experiment_config.yaml"
config = {}
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    print(f"实验配置已加载")

# ---- 列出所有 power 点 npz (按 shot_p{ii:02d}_avg.npz 排序) ----
# 仅取每个功率点的"平均"文件 (shot_p00_avg.npz), 避免重复处理单帧 _r{n}.npz
shot_files = sorted(raw_dir.glob("shot_p*_avg.npz"))
if not shot_files:
    raise FileNotFoundError(f"未找到 shot_p*_avg.npz: {raw_dir}")

print(f"共 {len(shot_files)} 个功率扫描点")


# ====== 拟合窗口 / baseline 参数 (放在 fit_one_shot 之前避免 NameError) ======
PRE_BASELINE_FRAC = 0.5  # burst 前 50% 时间作为 baseline 估计区间
POST_DARK_TIME_S = float(
    (config.get("burst_pump", {}) or {}).get("post_dark_s", 0.020)
)
print(f"dark decay 拟合段长度: {POST_DARK_TIME_S*1000:.2f} ms")


# %% Cell 4
# ========== 单点拟合函数 ==========
def compute_envelope(signal, larmor_samples_hint=None):
    """用 Hilbert 变换求信号包络。"""
    analytic = hilbert(signal)
    return np.abs(analytic)


def fit_one_shot(npz_path, pump_high_v):
    """对单个 shot npz 文件做完整分析：分段 + 拟合 + 汇总。

    自动检测活跃窗口:
      - build-up: 从 burst 开始到包络饱和 (达到峰值 90%), 避免拟合饱和平顶
      - decay:   从 burst 结束到包络衰减至 baseline×1.5, 避免拟合纯噪声段
    """
    data = np.load(npz_path, allow_pickle=True)
    time_s = data["time_s"]
    signal = data["signal"]
    t_burst_start = float(data["t_burst_start_s"])
    t_burst_end = float(data["t_burst_end_s"])

    # ---- 包络 (用于窗口检测 / 初始猜测 / 可视化, 拟合走原始 signal) ----
    envelope = compute_envelope(signal)

    # ---- baseline: pre-burst 段的中位数 ----
    pre_mask = time_s < t_burst_start
    if "baseline_offset" in data.files:
        baseline_offset = float(data["baseline_offset"])
    else:
        baseline_offset = float(np.median(envelope[pre_mask])) if pre_mask.sum() > 10 else float(np.median(envelope[:1000]))

    # ---- 对齐包络 (使 envelope 基线与 raw signal 的 DC 电平一致, 便于画图对比) ----
    signal_dc = float(np.median(signal[pre_mask])) if pre_mask.sum() > 10 else 0.0
    envelope_aligned = envelope - baseline_offset + signal_dc

    # ================================================================
    # Build-up 活跃窗口: 从包络梯度检测
    # ================================================================
    # 取 burst 段内包络; 找包络不再显著上升的点 (饱和点)
    bm = (time_s >= t_burst_start) & (time_s <= t_burst_end)
    env_b = envelope[bm]
    t_b = time_s[bm]
    env_peak_idx = int(np.argmax(env_b))
    env_peak = float(env_b[env_peak_idx])

    # 从峰值往前找 90% 饱和点 → 活跃 build-up 终点
    build_active_end = t_burst_end  # fallback
    for i in range(env_peak_idx, -1, -1):
        if env_b[i] < 0.9 * (env_peak - baseline_offset) + baseline_offset:
            build_active_end = float(t_b[min(i + 1, len(t_b) - 1)])
            break
    build_active_end = min(build_active_end, t_burst_start + 0.005)  # 最多 5ms

    build_mask = (time_s >= t_burst_start) & (time_s <= build_active_end)
    t_build_abs = time_s[build_mask]
    sig_build = signal[build_mask]

    # 初始猜测: 从包络估计 tau (63% 上升点)
    tau_init_build = 5e-4  # 默认 0.5ms
    for i in range(len(env_b)):
        if env_b[i] - baseline_offset > 0.63 * (env_peak - baseline_offset):
            tau_init_build = max(float(t_b[i] - t_burst_start), 1e-4)
            break

    # 用包络(而非原始信号)拟合 — 避免全振荡模型在快上升段的 A/tau 简并
    env_build_fit = envelope[build_mask]
    if len(t_build_abs) < 200:
        popt_build, pcov_build = None, None
    else:
        p0_build = [baseline_offset, env_peak - baseline_offset, tau_init_build]
        bounds_build = (
            [-np.inf, 0.0,   TAU_BB_BOUNDS[0]],
            [ np.inf, np.inf, TAU_BB_BOUNDS[1]],
        )
        model_env = make_env_build_up(t_burst_start)
        popt_build, pcov_build = _try_fit(
            model_env, t_build_abs, env_build_fit, p0_build, bounds_build, maxfev=50000
        )

    fit_success_build = popt_build is not None
    tau_BB = tau_BB_err = fit_offset_BB = fit_amp_BB = fit_omega_BB = fit_phi_BB = None
    if fit_success_build:
        fit_offset_BB, fit_amp_BB, tau_BB = popt_build
        if pcov_build is not None and pcov_build[2, 2] > 0:
            tau_BB_err = float(np.sqrt(pcov_build[2, 2]))
        else:
            tau_BB_err = np.inf
        fit_reliable_BB = (tau_BB > 1e-5) and (tau_BB_err < tau_BB * 5) and (tau_BB < 0.045)
    else:
        fit_reliable_BB = False

    # ================================================================
    # Decay 活跃窗口: 从包络梯度检测
    # ================================================================
    dm = (time_s >= t_burst_end) & (time_s <= t_burst_end + POST_DARK_TIME_S)
    env_d = envelope[dm]
    t_d = time_s[dm]

    # 找包络降到 baseline*1.5 的点 → 活跃 decay 终点
    decay_active_end = t_burst_end + 0.003  # fallback: 3ms
    for i in range(1, len(env_d)):
        if env_d[i] < baseline_offset * 1.5 + 0.002:
            decay_active_end = float(t_d[i])
            break

    decay_mask = (time_s >= t_burst_end) & (time_s <= decay_active_end)
    t_decay_abs = time_s[decay_mask]
    sig_decay = signal[decay_mask]

    # 初始猜测: 从包络估计 tau (1/e 衰减点)
    tau_init_decay = 1e-3  # 默认 1ms
    if len(env_d) > 1:
        env_d_init_val = float(env_d[0]) - baseline_offset
        for i in range(1, len(env_d)):
            if (env_d[i] - baseline_offset) < env_d_init_val / np.e:
                tau_init_decay = max(float(t_d[i] - t_burst_end), 1e-4)
                break

    if len(t_decay_abs) < 200:
        popt_decay, pcov_decay = None, None
    else:
        A_g_d = max(env_d_init_val, 1e-3) * 2
        omega_g = 2 * np.pi * 90000.0
        phi_g = 0.0
        p0_decay = [baseline_offset, A_g_d, tau_init_decay, omega_g, phi_g]
        bounds_decay = (
            [-np.inf, 0.0,   1e-5,  LARMOR_OMEGA_BOUNDS[0], PHASE_BOUNDS[0]],
            [ np.inf, np.inf, 0.05, LARMOR_OMEGA_BOUNDS[1], PHASE_BOUNDS[1]],
        )
        model_decay = make_bb_decay(t_burst_end)
        popt_decay, pcov_decay = _try_fit(
            model_decay, t_decay_abs, sig_decay, p0_decay, bounds_decay, maxfev=50000
        )

    fit_success_decay = popt_decay is not None
    tau_dark = tau_dark_err = fit_offset_dark = fit_amp_dark = None
    fit_omega_dark = fit_phi_dark = None
    if fit_success_decay:
        fit_offset_dark, fit_amp_dark, tau_dark, fit_omega_dark, fit_phi_dark = popt_decay
        if pcov_decay is not None and pcov_decay[2, 2] > 0:
            tau_dark_err = float(np.sqrt(pcov_decay[2, 2]))
        else:
            tau_dark_err = np.inf
        fit_reliable_dark = (tau_dark > 1e-5) and (tau_dark_err < tau_dark * 5) and (tau_dark < 0.045)
    else:
        fit_reliable_dark = False

    # ---- 拟合曲线 (在活跃段填实, 其余 NaN) ----
    if fit_success_build:
        model_env_eval = make_env_build_up(t_burst_start)
        fit_build_curve = np.full_like(time_s, np.nan)
        # 包络模型拟合的结果在 envelope 尺度上; 对齐到 signal 尺度便于画图
        fit_build_curve[build_mask] = (
            model_env_eval(t_build_abs, *popt_build) - baseline_offset + signal_dc
        )
    else:
        fit_build_curve = np.full_like(time_s, np.nan)

    if fit_success_decay:
        model_decay_eval = make_bb_decay(t_burst_end)
        fit_decay_curve = np.full_like(time_s, np.nan)
        # decay 用全振荡模型, 已在 signal 尺度上
        fit_decay_curve[decay_mask] = model_decay_eval(t_decay_abs, *popt_decay)
    else:
        fit_decay_curve = np.full_like(time_s, np.nan)

    # ---- 残差 ----
    # build-up: 比较对齐包络 vs 对齐拟合曲线 (两者都在 signal 尺度)
    # decay:   比较原始 signal vs 全振荡拟合 (两者都在 signal 尺度)
    res_build = np.full_like(time_s, np.nan)
    res_decay = np.full_like(time_s, np.nan)
    res_build[build_mask] = envelope_aligned[build_mask] - fit_build_curve[build_mask]
    res_decay[decay_mask] = signal[decay_mask] - fit_decay_curve[decay_mask]

    # ---- R² ----
    def _r2(y, yhat):
        ss_res = np.nansum((y - yhat) ** 2)
        ss_tot = np.nansum((y - np.nanmean(y)) ** 2)
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    r2_build = _r2(envelope_aligned[build_mask], fit_build_curve[build_mask]) if fit_success_build and np.sum(build_mask) > 5 else float("nan")
    r2_decay = _r2(signal[decay_mask], fit_decay_curve[decay_mask]) if fit_success_decay and np.sum(decay_mask) > 5 else float("nan")

    # ---- R_pump_eff ----
    Gamma_BB = 1.0 / tau_BB if (fit_success_build and tau_BB and tau_BB > 0) else float("nan")
    Gamma_dark = 1.0 / tau_dark if (fit_success_decay and tau_dark and tau_dark > 0) else float("nan")
    R_pump_eff = Gamma_BB - Gamma_dark if (fit_success_build and fit_success_decay) else float("nan")

    rms_res_build = float(np.sqrt(np.nanmean(res_build[build_mask] ** 2))) if fit_success_build else float("nan")
    rms_res_decay = float(np.sqrt(np.nanmean(res_decay[decay_mask] ** 2))) if fit_success_decay else float("nan")

    return {
        "pump_high_v": pump_high_v,
        "t_burst_start_s": t_burst_start,
        "t_burst_end_s": t_burst_end,
        "baseline_offset": baseline_offset,
        # ---- Build-up 拟合结果 ----
        "tau_BB_s": tau_BB,
        "tau_BB_err_s": tau_BB_err,
        "Gamma_BB_per_s": Gamma_BB,
        "omega_build_Hz": (fit_omega_BB / (2*np.pi)) if fit_omega_BB else None,
        "phi_build_rad": fit_phi_BB,
        "r2_build": r2_build,
        "rms_res_build_V": rms_res_build,
        "fit_reliable_BB": fit_reliable_BB,
        # ---- Decay 拟合结果 ----
        "tau_dark_s": tau_dark,
        "tau_dark_err_s": tau_dark_err,
        "Gamma_dark_per_s": Gamma_dark,
        "omega_decay_Hz": (fit_omega_dark / (2*np.pi)) if fit_omega_dark else None,
        "phi_decay_rad": fit_phi_dark,
        "r2_decay": r2_decay,
        "rms_res_decay_V": rms_res_decay,
        "fit_reliable_dark": fit_reliable_dark,
        # ---- 综合 ----
        "R_pump_eff_per_s": R_pump_eff,
        # ---- 用于画图 ----
        "time_s": time_s,
        "signal": signal,
        "envelope": envelope_aligned,          # 对齐到 signal DC 电平的包络
        "envelope_raw": envelope,              # 原始 Hilbert 包络 (用于参考)
        "signal_dc": signal_dc,
        "fit_build_curve": fit_build_curve,    # 已对齐到 signal 尺度
        "fit_decay_curve": fit_decay_curve,
        "res_build": res_build,
        "res_decay": res_decay,
        "fit_success_build": fit_success_build,
        "fit_success_decay": fit_success_decay,
        "popt_build": popt_build,
        "popt_decay": popt_decay,
    }


# %% Cell 5
# ========== 顺序对每个 power 点做拟合 ==========
print(f"\n{'='*60}")
print(f"开始分析 {len(shot_files)} 个功率点")
print(f"{'='*60}")

results = []
for idx, fpath in enumerate(shot_files):
    # 从 summary 中读取 pump_high_v; fallback 从文件名
    try:
        d_tmp = np.load(fpath, allow_pickle=True)
        pump_high_v = float(d_tmp["pump_power_voltage"])
    except Exception:
        pump_high_v = float("nan")
    print(f"\n--- [{idx+1}/{len(shot_files)}] {fpath.name}, "
          f"V={pump_high_v:.3f} ---")

    res = fit_one_shot(fpath, pump_high_v)
    res["file"] = fpath.name
    results.append(res)

    # 简洁打印
    if res["fit_success_build"]:
        msg_b = (f"tau_BB={res['tau_BB_s']*1e3:.3f} ± "
                 f"{res['tau_BB_err_s']*1e3:.3f} ms (R^2={res['r2_build']:.4f})")
        if not res["fit_reliable_BB"]:
            msg_b += " [WARN]拟合不可靠"
    else:
        msg_b = "tau_BB=拟合失败"
    if res["fit_success_decay"]:
        msg_d = (f"tau_dark={res['tau_dark_s']*1e3:.3f} ± "
                 f"{res['tau_dark_err_s']*1e3:.3f} ms (R^2={res['r2_decay']:.4f})")
        if not res["fit_reliable_dark"]:
            msg_d += " [WARN]拟合不可靠"
    else:
        msg_d = "tau_dark=拟合失败"

    R_str = f"{res['R_pump_eff_per_s']:.3f}" if np.isfinite(res["R_pump_eff_per_s"]) else "NaN"
    print(f"  build-up: {msg_b}")
    print(f"  decay:    {msg_d}")
    print(f"  R_pump_eff = {R_str} /s")


# %% Cell 6
# ========== 汇总 + 绘图 ==========
plt.rcParams.update({
    "figure.dpi": 120, "font.size": 11,
    "axes.labelsize": 12, "axes.titlesize": 13,
})

valid_mask = np.array([np.isfinite(r["R_pump_eff_per_s"]) for r in results])
pumps = np.array([r["pump_high_v"] for r in results])
R_eff = np.array([r["R_pump_eff_per_s"] for r in results])
tau_BB_arr = np.array([(r["tau_BB_s"] if r["tau_BB_s"] is not None else np.nan) for r in results])
tau_dark_arr = np.array([(r["tau_dark_s"] if r["tau_dark_s"] is not None else np.nan) for r in results])
Gamma_BB_arr = np.array([(r["Gamma_BB_per_s"] if np.isfinite(r["Gamma_BB_per_s"]) else np.nan) for r in results])
Gamma_dark_arr = np.array([(r["Gamma_dark_per_s"] if np.isfinite(r["Gamma_dark_per_s"]) else np.nan) for r in results])


# %% Cell 6
# ========== 出版级绘图 ==========
import matplotlib.ticker as tck

# --- global rcParams ---
plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 300,
    "font.size": 10, "axes.labelsize": 11, "axes.titlesize": 12,
    "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.linewidth": 0.8, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.size": 3.5, "ytick.major.size": 3.5,
    "xtick.minor.size": 2.0, "ytick.minor.size": 2.0,
    "lines.linewidth": 1.2, "lines.markersize": 5,
    "grid.alpha": 0.25, "grid.linestyle": ":",
    "figure.facecolor": "white", "savefig.bbox": "tight",
})

# Colorblind-friendly palette (Wong 2011, Nature Methods)
CB_BLUE   = "#0072B2"
CB_ORANGE = "#E69F00"
CB_GREEN  = "#009E73"
CB_RED    = "#D55E00"
CB_PURPLE = "#CC79A7"
CB_GRAY   = "#999999"


def save_figure(fig, base_name):
    """Save PNG (300 dpi) + PDF to results_dir."""
    for fmt in ["png", "pdf"]:
        path = results_dir / f"{base_name}.{fmt}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
    print(f"  已保存: {base_name}.png/.pdf")


# --- experimental condition annotation ---
_fc = config.get("burst_pump", {}).get("modulation_freq_Hz", 90000) / 1000
_dc = config.get("burst_pump", {}).get("fixed_duty_cycle_pct", 5.0)
_Nc = config.get("burst_pump", {}).get("burst_cycles", 1000)
COND_TEXT = (
    f"$f_L={_fc:.0f}" + r"\,\mathrm{kHz},\;"
    f"\\mathrm{{duty}}={_dc:.1f}" + r"\%,"
    f"\;N={_Nc}$"
)

# --- assemble data arrays ---
valid_mask = np.array([np.isfinite(r["R_pump_eff_per_s"]) for r in results])
pumps  = np.array([r["pump_high_v"] for r in results])
R_eff  = np.array([r["R_pump_eff_per_s"] for r in results])
tBB    = np.array([(r["tau_BB_s"] if r["tau_BB_s"] is not None else np.nan) for r in results])
tD     = np.array([(r["tau_dark_s"] if r["tau_dark_s"] is not None else np.nan) for r in results])
eBB    = np.array([(r["tau_BB_err_s"] if r["tau_BB_err_s"] is not None and np.isfinite(r["tau_BB_err_s"]) else np.nan) for r in results])
eD     = np.array([(r["tau_dark_err_s"] if r["tau_dark_err_s"] is not None and np.isfinite(r["tau_dark_err_s"]) else np.nan) for r in results])
r2b    = np.array([(r["r2_build"] if np.isfinite(r["r2_build"]) else np.nan) for r in results])
GBB    = np.array([(r["Gamma_BB_per_s"] if np.isfinite(r["Gamma_BB_per_s"]) else np.nan) for r in results])
GD     = np.array([(r["Gamma_dark_per_s"] if np.isfinite(r["Gamma_dark_per_s"]) else np.nan) for r in results])

# error propagation
with np.errstate(divide="ignore", invalid="ignore"):
    e_R   = np.sqrt((eBB / tBB**2)**2 + (eD / tD**2)**2)
    e_GBB = eBB / tBB**2
    e_GD  = eD  / tD**2

low_quality = (r2b < 0.7) | (R_eff < 0) | (~valid_mask)
hq = ~low_quality

print(f"[data ready] {low_quality.sum()}/{len(results)} low-quality pts")


# ================================================================
# Fig 1: Representative transient fits (3 panels)
# ================================================================
REPRESENT_V = [0.02, 0.12, 0.20]
repr_indices = []
for rv in REPRESENT_V:
    for i, r in enumerate(results):
        if abs(r["pump_high_v"] - rv) < 0.005:
            repr_indices.append(i)
            break
if len(repr_indices) < 2:
    repr_indices = [0, len(results)//2, -1]

fig1, axes1 = plt.subplots(1, 3, figsize=(15, 4.2), sharey=True)
DS = max(1, len(results[0]["signal"]) // 5000)

for ax, idx in zip(axes1, repr_indices):
    r = results[idx]
    t_ms = r["time_s"][::DS] * 1000.0
    ax.plot(t_ms, r["signal"][::DS] * 1e3, color=CB_GRAY, lw=0.2, alpha=0.30, label="Raw (90 kHz)")
    ax.plot(t_ms, r["envelope"][::DS] * 1e3, color=CB_BLUE, lw=1.0, label="Envelope")
    if r["fit_success_build"]:
        ax.plot(t_ms, r["fit_build_curve"][::DS] * 1e3, color=CB_RED, lw=1.4, ls="--",
                label="$\\tau_{\\rm BB}$" + f"={r['tau_BB_s']*1e3:.2f} ms")
    if r["fit_success_decay"]:
        ax.plot(t_ms, r["fit_decay_curve"][::DS] * 1e3, color=CB_GREEN, lw=1.4, ls="-.",
                label="$\\tau_{\\rm dark}$" + f"={r['tau_dark_s']*1e3:.2f} ms")
    ax.axvspan(0, r["t_burst_end_s"] * 1000, color=CB_ORANGE, alpha=0.08)
    ax.axvline(0, color=CB_ORANGE, ls=":", lw=0.6)
    ax.axvline(r["t_burst_end_s"] * 1000, color=CB_ORANGE, ls=":", lw=0.6)
    r2_str = f"$R^2={r['r2_build']:.2f}$" if np.isfinite(r["r2_build"]) else ""
    ax.set_title(f"Pump {r['pump_high_v']:.2f} V  |  {r2_str}", fontsize=10)
    ax.set_xlabel("Time (ms)")
    ax.set_xlim(-5, 35)
    ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
    ax.grid(True, alpha=0.2, lw=0.3)

axes1[0].set_ylabel("Signal / Envelope (mV)")
handles, labels = axes1[0].get_legend_handles_labels()
axes1[0].legend(handles, labels, fontsize=7, loc="upper right", framealpha=0.8)

fig1.suptitle(f"Bell-Bloom transient fits (representative powers)   [{COND_TEXT}]",
              fontsize=11, y=1.02)
fig1.tight_layout()
save_figure(fig1, "transient_fit_examples_pub")
plt.show()


# ================================================================
# Fig 2: R_pump vs Pump Power (with error bars)
# ================================================================
fig2, ax2 = plt.subplots(figsize=(7, 4.5))
if hq.sum() > 0:
    ax2.errorbar(pumps[hq], R_eff[hq], yerr=e_R[hq],
                 fmt="o", color=CB_BLUE, capsize=3, capthick=0.8, markersize=7, zorder=5,
                 label="$R^2 \\geq 0.7$")
if low_quality.sum() > 0:
    ax2.errorbar(pumps[low_quality], np.maximum(R_eff[low_quality], 0),
                 yerr=e_R[low_quality],
                 fmt="o", mfc="none", mec=CB_GRAY, color=CB_GRAY,
                 capsize=3, capthick=0.8, markersize=7, alpha=0.55,
                 label="Low SNR / $R^2 < 0.7$")
if hq.sum() >= 3:
    w = 1.0 / np.maximum(e_R[hq], 1.0)
    coeff = np.polyfit(pumps[hq], R_eff[hq], 1, w=w)
    p_fit = np.linspace(pumps.min(), pumps.max(), 50)
    ax2.plot(p_fit, np.polyval(coeff, p_fit), "--", color=CB_RED, lw=1.0,
             label=f"Linear: $R_{{\\rm pump}} = {coeff[0]:.0f}\\,V {coeff[1]:+.0f}$")

ax2.axhline(0, color="k", lw=0.6)
ax2.set_xlabel("Pump high voltage (V)")
ax2.set_ylabel("$R_{\\rm pump}$ (s$^{-1}$)")
ax2.set_title(f"$R_{{\\rm pump}}$ vs Pump Power  [{COND_TEXT}]", fontsize=11)
ax2.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
ax2.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
ax2.grid(True, alpha=0.2, lw=0.3)
ax2.legend(fontsize=8, framealpha=0.8)
fig2.tight_layout()
save_figure(fig2, "rpump_eff_vs_pump_power_pub")
plt.show()


# ================================================================
# Fig 3: Gamma rates vs Pump Power
# ================================================================
fig3, ax3 = plt.subplots(figsize=(7, 4.5))
ax3.errorbar(pumps, GBB * 1e-3, yerr=e_GBB * 1e-3,
             fmt="o-", color=CB_RED, capsize=3, capthick=0.8, lw=1.2,
             label="$\\Gamma_{\\rm BB}=1/\\tau_{\\rm BB}$")
ax3.errorbar(pumps, GD * 1e-3, yerr=e_GD * 1e-3,
             fmt="s-", color=CB_GREEN, capsize=3, capthick=0.8, lw=1.2,
             label="$\\Gamma_{\\rm dark}=1/\\tau_{\\rm dark}$")
ax3.set_xlabel("Pump high voltage (V)")
ax3.set_ylabel("Rate ($10^3$ s$^{-1}$)")
ax3.set_title("$\\Gamma_{\\rm BB},\\;\\Gamma_{\\rm dark}$ vs Pump Power  " + f"[{COND_TEXT}]", fontsize=11)
ax3.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
ax3.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
ax3.grid(True, alpha=0.2, lw=0.3)
ax3.legend(fontsize=9, framealpha=0.8)
fig3.tight_layout()
save_figure(fig3, "gamma_rates_vs_pump_power_pub")
plt.show()


# ================================================================
# Fig 4: Zoomed transient
# ================================================================
mid_idx = min(len(results) - 1, len(results) // 2)
r = results[mid_idx]
t_ms_full = r["time_s"] * 1000.0
zoom_mask = (t_ms_full >= -3) & (t_ms_full <= r["t_burst_end_s"] * 1000 + 8)
t_zoom = t_ms_full[zoom_mask]
ds_zoom = max(1, len(t_zoom) // 8000)

fig4, (ax4t, ax4b) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
ax4t.plot(t_zoom[::ds_zoom], r["signal"][zoom_mask][::ds_zoom] * 1e3,
          color=CB_GRAY, lw=0.2, alpha=0.25, label="Raw (90 kHz)")
ax4t.plot(t_zoom[::ds_zoom], r["envelope"][zoom_mask][::ds_zoom] * 1e3,
          color=CB_BLUE, lw=1.2, label="Envelope")
if r["fit_success_build"]:
    ax4t.plot(t_zoom[::ds_zoom], r["fit_build_curve"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_RED, lw=1.5, ls="--", label="Build-up fit")
if r["fit_success_decay"]:
    ax4t.plot(t_zoom[::ds_zoom], r["fit_decay_curve"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_GREEN, lw=1.5, ls="-.", label="Decay fit")
ax4t.axvspan(0, r["t_burst_end_s"] * 1000, color=CB_ORANGE, alpha=0.07)
ax4t.axvline(0, color=CB_ORANGE, ls=":", lw=0.8)
ax4t.axvline(r["t_burst_end_s"] * 1000, color=CB_ORANGE, ls=":", lw=0.8)
ax4t.set_ylabel("Signal / Envelope (mV)")
ax4t.grid(True, alpha=0.2, lw=0.3)
ax4t.legend(fontsize=7, loc="upper right", framealpha=0.8)
tb_s = r["tau_BB_s"] * 1e3 if r["tau_BB_s"] is not None else 0
td_s = r["tau_dark_s"] * 1e3 if r["tau_dark_s"] is not None else 0
ax4t.set_title(f"Zoom: Pump {r['pump_high_v']:.2f} V  |  "
               + "$\\tau_{\\rm BB}$" + f"={tb_s:.2f} ms,  "
               + "$\\tau_{\\rm dark}$" + f"={td_s:.2f} ms", fontsize=10)

ax4b.axhline(0, color="k", lw=0.5)
if r["fit_success_build"]:
    ax4b.plot(t_zoom[::ds_zoom], r["res_build"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_RED, lw=0.5, alpha=0.8, label="Build-up resid.")
if r["fit_success_decay"]:
    ax4b.plot(t_zoom[::ds_zoom], r["res_decay"][zoom_mask][::ds_zoom] * 1e3,
              color=CB_GREEN, lw=0.5, alpha=0.8, label="Decay resid.")
ax4b.set_xlabel("Time (ms)")
ax4b.set_ylabel("Residual (mV)")
ax4b.grid(True, alpha=0.2, lw=0.3)
ax4b.legend(fontsize=7, loc="upper right", framealpha=0.8)
for _ax in [ax4t, ax4b]:
    _ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
    _ax.yaxis.set_minor_locator(tck.AutoMinorLocator(2))
fig4.tight_layout()
save_figure(fig4, "transient_fit_zoom_pub")
plt.show()


# ================================================================
# Fig 5: Residuals heatmap + RMS
# ================================================================
n_pts = len(results)
t_ref = results[0]["time_s"] * 1000.0
t_hm_min, t_hm_max = -2, 18
hm_mask = (t_ref >= t_hm_min) & (t_ref <= t_hm_max)
t_hm = t_ref[hm_mask]

res_matrix = np.full((n_pts, hm_mask.sum()), np.nan)
rms_vals = []
for i, r in enumerate(results):
    rb = r["res_build"] * 1e3
    rd = r["res_decay"] * 1e3
    combined = np.where(np.isfinite(rb), rb, rd)
    res_matrix[i, :] = combined[hm_mask]
    bm = (r["time_s"] >= 0) & (r["time_s"] <= r["t_burst_end_s"])
    if np.any(np.isfinite(r["res_build"][bm])):
        rms_vals.append(float(np.sqrt(np.nanmean(r["res_build"][bm]**2))) * 1e3)
    else:
        rms_vals.append(np.nan)

fig5, (ax5h, ax5r) = plt.subplots(2, 1, figsize=(10, 5.5),
                                   gridspec_kw={"height_ratios": [3, 1]})
vlim = float(np.nanmax(np.abs(res_matrix))) * 0.8
im = ax5h.pcolormesh(t_hm, pumps, res_matrix, cmap="RdBu_r",
                      vmin=-vlim, vmax=vlim, shading="auto", rasterized=True)
cb = fig5.colorbar(im, ax=ax5h, label="Residual (mV)")
ax5h.axvline(0, color="k", lw=0.5, ls="--")
t_end_ms = float(results[0]["t_burst_end_s"]) * 1000
ax5h.axvline(t_end_ms, color=CB_ORANGE, lw=0.8, ls=":")
ax5h.set_ylabel("Pump high voltage (V)")
ax5h.set_title(f"Fit residuals heatmap  [{COND_TEXT}]", fontsize=11)

ax5r.plot(pumps, rms_vals, "o-", color=CB_PURPLE, lw=1.2, label="RMS build-up resid.")
ax5r.set_xlabel("Pump high voltage (V)")
ax5r.set_ylabel("RMS res. (mV)")
ax5r.grid(True, alpha=0.2, lw=0.3)
ax5r.legend(fontsize=8)
for _ax in [ax5h, ax5r]:
    _ax.xaxis.set_minor_locator(tck.AutoMinorLocator(2))
fig5.tight_layout()
save_figure(fig5, "residuals_diagnostics_pub")
plt.show()


# ================================================================
# Save analysis (with error propagation)
# ================================================================
analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "method": "Hilbert envelope -> build-up env fit (3 params) -> decay full-osc fit (5 params)",
    "n_points": len(results),
    "cond_text": COND_TEXT,
    "per_point": [],
}

for i, r in enumerate(results):
    analysis["per_point"].append({
        "pump_high_V": float(r["pump_high_v"]),
        "file": r["file"],
        "tau_BB_s": (float(r["tau_BB_s"]) if r["tau_BB_s"] is not None else None),
        "tau_BB_err_s": (float(r["tau_BB_err_s"]) if r["tau_BB_err_s"] is not None and np.isfinite(r["tau_BB_err_s"]) else None),
        "Gamma_BB_per_s": (float(r["Gamma_BB_per_s"]) if np.isfinite(r["Gamma_BB_per_s"]) else None),
        "e_Gamma_BB_per_s": (float(e_GBB[i]) if np.isfinite(e_GBB[i]) else None),
        "tau_dark_s": (float(r["tau_dark_s"]) if r["tau_dark_s"] is not None else None),
        "tau_dark_err_s": (float(r["tau_dark_err_s"]) if r["tau_dark_err_s"] is not None and np.isfinite(r["tau_dark_err_s"]) else None),
        "Gamma_dark_per_s": (float(r["Gamma_dark_per_s"]) if np.isfinite(r["Gamma_dark_per_s"]) else None),
        "e_Gamma_dark_per_s": (float(e_GD[i]) if np.isfinite(e_GD[i]) else None),
        "R_pump_eff_per_s": (float(r["R_pump_eff_per_s"]) if np.isfinite(r["R_pump_eff_per_s"]) else None),
        "e_R_pump_eff_per_s": (float(e_R[i]) if np.isfinite(e_R[i]) else None),
        "r2_build": (float(r["r2_build"]) if np.isfinite(r["r2_build"]) else None),
        "fit_reliable_BB": bool(r["fit_reliable_BB"]),
        "fit_reliable_dark": bool(r["fit_reliable_dark"]),
        "low_quality": bool(low_quality[i]),
    })

a_path = results_dir / "analysis_pub.yaml"
with open(a_path, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"\n[analysis] {a_path}")
a_json = results_dir / "analysis_pub.json"
with open(a_json, "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2, ensure_ascii=False)
print(f"[analysis] {a_json}")


# --- summary table ---
print(f"\n{'='*72}")
print(f"  {'Pump V':>8}  {'tau_BB (ms)':>12}  {'tau_dark (ms)':>14}  {'R_pump (1/s)':>20}")
print(f"{'='*72}")
for i, r in enumerate(results):
    v  = r["pump_high_v"]
    tb = f"{r['tau_BB_s']*1e3:.3f}" if r["fit_success_build"] else "NaN"
    td = f"{r['tau_dark_s']*1e3:.3f}" if r["fit_success_decay"] else "NaN"
    Rp = f"{r['R_pump_eff_per_s']:.1f} +/- {e_R[i]:.1f}" if np.isfinite(r["R_pump_eff_per_s"]) else "NaN"
    fl = " [LQ]" if low_quality[i] else ""
    print(f"  {v:8.3f}  {tb:>12}  {td:>14}  {Rp:>20}{fl}")
print(f"{'='*72}")
n_lq = low_quality.sum()
if n_lq:
    print(f"\n[Note] {n_lq} pts marked LQ: R^2<0.7 or R_pump<0")
print("\n[OK] Publication-quality figures done")
