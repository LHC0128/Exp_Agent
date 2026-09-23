# %% [markdown] Cell 0
# # XY AW offset 电压标定 — 数据分析与可视化
#
# **无需连接任何仪器**，仅读取本地 `data/XY_AW_Voltage_Calibration/<run>/`
# 下的原始数据进行分析。
#
# ## 数据结构
# - 数据目录: `data/XY_AW_Voltage_Calibration/<MMDD_HHMM_xy_aw_cal>/`
# - 原始数据 (按 XY AW offset 电压分组):
#   - `raw/dc_v00_0.0400V/zfreq_0000_500.000Hz.npz`
#   - `raw/dc_v00_0.0400V/zfreq_0001_600.000Hz.npz`
#   - ...
#   - `raw/dc_v00_0.0400V/frequency_index.json`  (子目录索引)
#   - `raw/frequency_index.json`  (全局索引: 所有 XY DC × Z freq)
# - 字段 (`npz`):
#   - `xy_dc_voltage_V`、`z_freq_Hz`、`is_baseline`、`z_drive_amplitude_Vpp`、
#     `acquisition_mode`、`actual_rate_Sa_s`
#   - `time_s`、`y_V` (Demod 0 sample.y 时域)
#   - `demod_r_x_values_V / y_values_V / r_values_V / theta_values_rad`、
#     `demod_r_x_mean_V / y_mean_V / r_vector_mean_V / r_mean_scalar_V / r_std_scalar_V`、
#     `demod_r_phase_vector_rad / phase_vector_deg`
#
# ## 分析流程
# 1. 对每个 XY AW offset 电压下的每段 `y_V`，做
#    - **offline lock-in**: `X=2·⟨y·cos⟩, Y=2·⟨y·sin⟩, R=√(X²+Y²)`
#    - **nearest-bin FFT**: 整段 Hann 窗 FFT，取最近 drive_freq 的 bin
# 2. 对每条 response(f_z) 取峰值频率，优先用 Lorentzian / double-Lorentzian 拟合
#   得到 `fit_center_Hz` 与 `fit_width_Hz`；拟合失败时退回最大点
# 3. 拟合 `f_peak_Hz = K_eff * XY_AW_OFFSET_VOLTAGE_V + B_eff`
#
# ## 输出
# - `results/analysis.yaml`、`results/analysis.json`
# - `results/xy_aw_calibration_results.npz`
# - 至少 4 张图：
#   - `xy_aw_calibration_curve.png` (f_peak vs XY_AW_OFFSET_VOLTAGE + 线性拟合)
#   - `xy_aw_calibration_residual.png` (拟合残差)
#   - `frequency_response_examples.png` (典型 XY AW offset 电压下的响应曲线)
#   - `method_peak_comparison.png` (FFT / offline lock-in / hardware 峰位对比)

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

from scipy import optimize as scipy_optimize
from scipy import signal as scipy_signal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style
set_plot_style("paper")

print("导入完成（离线分析模式，无需仪器）")

# %% Cell 2
# ========== 配置与数据选择 ==========
EXPERIMENT_TYPE = "XY_AW_Voltage_Calibration"

USE_LATEST = True
DATA_DIR_OVERRIDE = None
# DATA_DIR_OVERRIDE = "data/XY_AW_Voltage_Calibration/0701_1200_xy_aw_cal"

if DATA_DIR_OVERRIDE:
    DATA_DIR = project_root / DATA_DIR_OVERRIDE
elif USE_LATEST:
    base = project_root / "data" / EXPERIMENT_TYPE
    if base.exists():
        dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
        DATA_DIR = dirs[0] if dirs else None
    else:
        DATA_DIR = None
else:
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_xy_aw_cal"

if DATA_DIR is None or not DATA_DIR.exists():
    raise FileNotFoundError(
        f"未找到数据目录: {DATA_DIR}\n"
        f"请确认 data/{EXPERIMENT_TYPE}/ 下有实验数据，"
        f"或设置 USE_LATEST=False / DATA_DIR_OVERRIDE 手动指定路径"
    )

raw_dir = DATA_DIR / "raw"
results_dir = DATA_DIR / "results"
results_dir.mkdir(parents=True, exist_ok=True)
print(f"数据目录: {DATA_DIR}")

# ---- 加载 experiment_config.yaml ----
config_path = DATA_DIR / "experiment_config.yaml"
config = {}
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    print("实验配置已加载")
else:
    print("⚠ 未找到 experiment_config.yaml")

ACQUISITION_MODE = config.get("acquisition_mode", "daq_fft_y")
_xy_voltage_scan_config = config.get("xy_dc_voltage_scan", {})
XY_DC_LIST_CONFIG = _xy_voltage_scan_config.get(
    "XY_AW_OFFSET_VOLTAGE_LIST_V",
    _xy_voltage_scan_config.get("XY_DC_VOLTAGE_LIST_V", None),
)
LEGACY = config.get("legacy_reference_only_NOT_result", {}) or {}
A_ENV_K_LEGACY = LEGACY.get("A_ENV_K_Hz_per_V_LEGACY", None)
A_ENV_B_LEGACY = LEGACY.get("A_ENV_B_Hz_LEGACY", None)

# ---- 离线锁相配置 ----
OFFLINE_LOCKIN_USE_INTEGER_CYCLES = True

# ---- FFT 配置 ----
FFT_USE_HANN = True

# ---- 峰位拟合策略 ----
# 对每个 XY_DC 电压点的 response(f_z)，同时计算以下三种峰位：
#   1. max_bin: 直接取最大响应点的频率
#   2. single_lorentzian: 单 Lorentzian 拟合
#   3. double_lorentzian: Bloch steady-state 模型拟合 (推荐主结果)
# 主标定结果优先级: double_lorentzian > single_lorentzian > max_bin
PEAK_STRATEGY = "double_lorentzian > single_lorentzian > max_bin"
FIT_MIN_POINTS = 6
FIT_EXCLUDE_OUTLIER_PCT = 0.0

# ---- 双 Lorentzian 参数 ----
DLZ_GAMMA_INIT_HZ = 100.0
DLZ_GAMMA_MIN_HZ = 1e-3
DLZ_GAMMA_MAX_HZ = None         # None 时取 (f_max - f_min)/2
DLZ_OMEGA_INIT_HZ = None        # None 时取 -3dB peak
DLZ_OMEGA_MIN_HZ = 1.0
DLZ_OMEGA_MAX_HZ = None
DLZ_INCLUDE_OFFSET = True
DLZ_OFFSET_INIT_V = 0.0

# ---- 线性标定 (f_peak vs XY_DC) 配置 ----
LINEAR_FIT_MIN_POINTS = 3

print(f"\n采集模式 (config): {ACQUISITION_MODE}")
print(f"取值方法: offline lock-in (primary), nearest-bin FFT, HW Demod 3 (cross-check)")
print(f"峰位策略: {PEAK_STRATEGY}")

# %% Cell 3
# ========== 加载频率索引与实验配置 ==========
index_json_path = raw_dir / "frequency_index.json"
index_npz_path = raw_dir / "frequency_index.npz"

if index_json_path.exists():
    with open(index_json_path, encoding="utf-8") as f:
        global_index = json.load(f)
    print(f"全局频点索引 (JSON) 已加载: {len(global_index)} 个测量点")
elif index_npz_path.exists():
    idx_npz = np.load(index_npz_path, allow_pickle=True)
    global_index = idx_npz["records"].tolist()
    print(f"全局频点索引 (NPZ) 已加载: {len(global_index)} 个测量点")
else:
    raise FileNotFoundError(f"未找到频点索引: {raw_dir}/frequency_index.*")

# 按 xy_dc_voltage_V 分组
v_groups = {}  # xy_dc -> list of records
for rec in global_index:
    v = float(rec["xy_dc_voltage_V"])
    v_groups.setdefault(v, []).append(rec)

# 按 XY DC 排序（升序）
xy_dc_sorted = sorted(v_groups.keys())
print(f"\nXY AW offset 电压点数: {len(xy_dc_sorted)}")
print(f"  电压范围: [{min(xy_dc_sorted):.4f}, {max(xy_dc_sorted):.4f}] V")
for v in xy_dc_sorted:
    print(f"    XY_DC={v:.4f} V: {len(v_groups[v])} 个 Z 频点")


# %% Cell 4
# ============================================================
# 频点级提取函数
# ============================================================

def nearest_bin_fft_amp(y: np.ndarray, fs: float, drive_freq: float):
    """整段 Hann 窗 FFT，取最近 drive_freq 的 bin 幅值.

    返回 amp_V, bin_freq_Hz, freq_error_Hz
    """
    n = len(y)
    if n < 4 or fs <= 0:
        return np.nan, np.nan, np.nan
    y0 = y - np.mean(y)
    if FFT_USE_HANN:
        window = scipy_signal.windows.hann(n, sym=False)
        cg = window.sum()
        yw = y0 * window
    else:
        window = np.ones(n)
        cg = float(n)
        yw = y0
    Y = np.fft.rfft(yw)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    amp = np.abs(Y) * 2.0 / cg
    df = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    idx = int(np.round(drive_freq / df))
    idx = max(0, min(idx, len(amp) - 1))
    return float(amp[idx]), float(freqs[idx]), float(freqs[idx] - drive_freq)


def offline_lockin_r(y: np.ndarray, fs: float, drive_freq: float,
                     use_integer_cycles: bool = True):
    """对 y_V 做软件锁相，返回 X/Y/R/phase（V）与有效标记."""
    if y is None or len(y) < 4 or fs <= 0:
        return {"X": np.nan, "Y": np.nan, "R": np.nan,
                "phase_deg": np.nan, "is_valid": False,
                "n_cycles_used": 0, "n_samples_used": 0}

    duration = len(y) / fs
    if use_integer_cycles:
        n_cycles = int(np.floor(duration * drive_freq))
        if n_cycles < 1:
            return {"X": np.nan, "Y": np.nan, "R": np.nan,
                    "phase_deg": np.nan, "is_valid": False,
                    "n_cycles_used": 0, "n_samples_used": 0}
        n_samples = int(round(n_cycles * fs / drive_freq))
        n_samples = min(n_samples, len(y))
        if n_samples < 4:
            return {"X": np.nan, "Y": np.nan, "R": np.nan,
                    "phase_deg": np.nan, "is_valid": False,
                    "n_cycles_used": 0, "n_samples_used": 0}
        y_seg = y[:n_samples]
        n_cycles_used = n_cycles
        n_samples_used = n_samples
    else:
        y_seg = y
        n_cycles_used = int(duration * drive_freq)
        n_samples_used = len(y_seg)

    y_seg = y_seg - np.mean(y_seg)
    t = np.arange(len(y_seg)) / fs
    ref_cos = np.cos(2.0 * np.pi * drive_freq * t)
    ref_sin = np.sin(2.0 * np.pi * drive_freq * t)
    X = 2.0 * np.mean(y_seg * ref_cos)
    Y = 2.0 * np.mean(y_seg * ref_sin)
    R = float(np.sqrt(X * X + Y * Y))
    phase_deg = float(np.degrees(np.arctan2(Y, X)))
    return {"X": float(X), "Y": float(Y), "R": R,
            "phase_deg": phase_deg, "is_valid": True,
            "n_cycles_used": int(n_cycles_used),
            "n_samples_used": int(n_samples_used)}


# %% Cell 5
# ============================================================
# Lorentzian / Double-Lorentzian 拟合（拟合 fit_center 与 fit_width）
# ============================================================

def fit_single_lorentzian(freqs, response, min_points=FIT_MIN_POINTS):
    """R(f) = A * hwhm^2 / ((f - f0)^2 + hwhm^2)."""
    out = {"success": False, "f0_Hz": None, "hwhm_Hz": None,
           "fwhm_Hz": None, "A": None, "residual_std": None,
           "n_points": 0, "message": "",
           "fit_curve_freqs": np.array([]),
           "fit_curve_values": np.array([])}
    if freqs is None or response is None or len(freqs) < min_points:
        out["message"] = "insufficient points"
        return out
    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    if int(np.sum(valid)) < min_points:
        out["message"] = "too few valid points"
        return out
    f_v = np.asarray(freqs, dtype=float)[valid]
    r_v = np.asarray(response, dtype=float)[valid]
    out["n_points"] = int(np.sum(valid))
    peak_idx = int(np.argmax(r_v))
    A_init = float(r_v[peak_idx])
    f0_init = float(f_v[peak_idx])
    hwhm_init = max((f_v.max() - f_v.min()) / 4.0, 10.0)
    hwhm_upper = max((f_v.max() - f_v.min()) / 2.0,
                     10.0 * float(np.diff(np.sort(f_v)).mean()))

    def model(f, A, f0, hwhm):
        return A * hwhm**2 / ((f - f0)**2 + hwhm**2)

    try:
        popt, pcov = scipy_optimize.curve_fit(
            model, f_v, r_v,
            p0=[A_init, f0_init, hwhm_init],
            bounds=([0.0, float(f_v.min()), 1e-3],
                    [10.0 * max(A_init, 1.0),
                     float(f_v.max()), hwhm_upper]),
            maxfev=10000,
        )
        A_fit, f0_fit, hwhm_fit = (float(popt[0]), float(popt[1]),
                                   float(popt[2]))
        perr = np.sqrt(np.diag(pcov))
        residual = r_v - model(f_v, *popt)
        fit_grid = np.linspace(float(f_v.min()), float(f_v.max()), 400)
        fit_vals = model(fit_grid, *popt)
        out.update({
            "success": True,
            "A": A_fit, "f0_Hz": f0_fit, "hwhm_Hz": hwhm_fit,
            "fwhm_Hz": float(2.0 * hwhm_fit),
            "A_unc": float(perr[0]),
            "f0_unc_Hz": float(perr[1]),
            "hwhm_unc_Hz": float(perr[2]),
            "fwhm_unc_Hz": float(2.0 * perr[2]),
            "residual_std": float(np.std(residual)),
            "residual_max": float(np.max(np.abs(residual))),
            "fit_curve_freqs": fit_grid,
            "fit_curve_values": fit_vals,
            "message": "ok",
        })
    except Exception as e:
        out["message"] = f"fit failed: {type(e).__name__}: {e}"
    return out


def fit_double_lorentzian(freqs, response, min_points=FIT_MIN_POINTS,
                          LARMOR_HINT_Hz=None):
    """R(f) = C + P0 · sqrt(Γ²+f²) /
                 sqrt((Γ² - f² + Ω²)² + 4·Γ²·f²).
    """
    out = {"success": False, "P0": None, "Gamma_Hz": None,
           "Omega_Ctrl_Hz": None, "offset_V": None,
           "fwhm_Hz": None, "residual_std": None,
           "n_points": 0, "message": "",
           "fit_curve_freqs": np.array([]),
           "fit_curve_values": np.array([])}
    if freqs is None or response is None or len(freqs) < min_points:
        out["message"] = "insufficient points"
        return out
    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    if int(np.sum(valid)) < min_points:
        out["message"] = "too few valid points"
        return out
    f_v = np.asarray(freqs, dtype=float)[valid]
    r_v = np.asarray(response, dtype=float)[valid]
    out["n_points"] = int(np.sum(valid))

    f_min_data = float(f_v.min())
    f_max_data = float(f_v.max())
    peak_idx = int(np.argmax(r_v))
    peak_val = float(r_v[peak_idx])
    valley_val = float(np.min(r_v))

    Omega_0 = (DLZ_OMEGA_INIT_HZ
               if DLZ_OMEGA_INIT_HZ is not None
               else (LARMOR_HINT_Hz
                     if LARMOR_HINT_Hz is not None
                     else float(f_v[peak_idx])))
    Gamma_0 = DLZ_GAMMA_INIT_HZ
    P0_0 = float(2.0 * Gamma_0 * max(peak_val - valley_val, 1e-6))
    C_0 = DLZ_OFFSET_INIT_V

    Gamma_max = DLZ_GAMMA_MAX_HZ or max((f_max_data - f_min_data) / 2.0,
                                        10.0)
    Omega_max = DLZ_OMEGA_MAX_HZ or max(f_max_data * 1.05, Omega_0 * 2.0)
    P0_max = max(10.0 * P0_0,
                 abs(r_v.max() - C_0) * (Gamma_max + f_max_data))

    if DLZ_INCLUDE_OFFSET:
        bounds = ([0.0, DLZ_GAMMA_MIN_HZ, DLZ_OMEGA_MIN_HZ,
                   -10.0 * abs(C_0) - 1e-3],
                  [P0_max, Gamma_max, Omega_max,
                   10.0 * (abs(C_0) + 1e-3)])
    else:
        bounds = ([0.0, DLZ_GAMMA_MIN_HZ, DLZ_OMEGA_MIN_HZ],
                  [P0_max, Gamma_max, Omega_max])

    def model(f, P0, Gamma, Omega, C):
        num = np.sqrt(Gamma**2 + f**2)
        d1 = Gamma**2 - f**2 + Omega**2
        d2 = 4.0 * Gamma**2 * f**2
        den = np.sqrt(d1 * d1 + d2)
        return C + P0 * num / np.maximum(den, 1e-12)

    def model_no_offset(f, P0, Gamma, Omega):
        return model(f, P0, Gamma, Omega, 0.0)

    best_popt = None
    best_pcov = None
    best_resid = np.inf

    # 多次 restart
    for g_factor in [0.3, 0.7, 1.0, 1.5, 3.0]:
        for o_factor in [0.95, 1.0, 1.05]:
            for p_factor in [0.5, 1.0, 1.5]:
                p0_init = [p_factor * P0_0, g_factor * Gamma_0,
                           o_factor * Omega_0]
                if DLZ_INCLUDE_OFFSET:
                    p0_init.append(C_0)
                try:
                    if DLZ_INCLUDE_OFFSET:
                        popt_, pcov_ = scipy_optimize.curve_fit(
                            model, f_v, r_v,
                            p0=p0_init, bounds=bounds, maxfev=10000,
                        )
                    else:
                        popt_, pcov_ = scipy_optimize.curve_fit(
                            model_no_offset, f_v, r_v,
                            p0=p0_init, bounds=bounds, maxfev=10000,
                        )
                    rpred = (model(f_v, *popt_) if DLZ_INCLUDE_OFFSET
                             else model_no_offset(f_v, *popt_))
                    resid = float(np.sum((r_v - rpred)**2))
                    if resid < best_resid:
                        best_resid = resid
                        best_popt = list(popt_)
                        best_pcov = pcov_
                except Exception:
                    continue

    if best_popt is None:
        out["message"] = "all fits failed"
        return out

    if DLZ_INCLUDE_OFFSET:
        P0_fit, Gamma_fit, Omega_fit, offset_fit = (
            float(best_popt[0]), float(best_popt[1]),
            float(best_popt[2]), float(best_popt[3]))
    else:
        P0_fit, Gamma_fit, Omega_fit, offset_fit = (
            float(best_popt[0]), float(best_popt[1]),
            float(best_popt[2]), 0.0)

    perr = np.sqrt(np.diag(best_pcov))
    fit_grid = np.linspace(f_min_data, f_max_data, 400)
    if DLZ_INCLUDE_OFFSET:
        fit_vals = model(fit_grid, *best_popt)
        residual = r_v - model(f_v, *best_popt)
    else:
        fit_vals = model_no_offset(fit_grid, *best_popt)
        residual = r_v - model_no_offset(f_v, *best_popt)

    out.update({
        "success": True,
        "P0": P0_fit, "Gamma_Hz": Gamma_fit, "Omega_Ctrl_Hz": Omega_fit,
        "offset_V": offset_fit,
        "fwhm_Hz": float(2.0 * Gamma_fit),
        "P0_unc": float(perr[0]),
        "Gamma_unc_Hz": float(perr[1]),
        "Omega_unc_Hz": float(perr[2]),
        "offset_unc_V": (float(perr[3])
                         if DLZ_INCLUDE_OFFSET and len(perr) > 3 else None),
        "residual_std": float(np.std(residual)),
        "residual_max": float(np.max(np.abs(residual))),
        "fit_curve_freqs": fit_grid,
        "fit_curve_values": fit_vals,
        "message": "ok",
    })
    return out


# %% Cell 6
# ============================================================
# 对每个 XY AW offset 电压点：加载所有 Z 频点数据 → 提取 FFT + lock-in → 拟合
# ============================================================
print("\n" + "=" * 60)
print("开始对每个 XY AW offset 电压点提取响应曲线与拟合")
print("=" * 60)

per_voltage_results = {}   # xy_dc -> dict

for v_idx, v in enumerate(xy_dc_sorted):
    print(f"\n--- XY_DC = {v:.4f} V ({v_idx+1}/{len(xy_dc_sorted)}) ---")
    recs = sorted(v_groups[v], key=lambda r: r["z_freq_Hz"])
    z_freqs = np.array([r["z_freq_Hz"] for r in recs], dtype=float)

    fft_amps = np.full(len(recs), np.nan)
    li_R = np.full(len(recs), np.nan)
    li_X = np.full(len(recs), np.nan)
    li_Y = np.full(len(recs), np.nan)
    li_phase_deg = np.full(len(recs), np.nan)
    hw_R_vec = np.full(len(recs), np.nan)
    hw_R_scalar = np.full(len(recs), np.nan)
    hw_R_std = np.full(len(recs), np.nan)
    hw_phase_deg = np.full(len(recs), np.nan)
    actual_rates = np.full(len(recs), np.nan)
    file_paths = []

    for k, rec in enumerate(recs):
        fpath = raw_dir / rec["subdir"] / rec["file"]
        if not fpath.exists():
            print(f"  ⚠ 频点文件不存在: {fpath}")
            continue
        data = np.load(fpath, allow_pickle=True)
        file_paths.append(str(fpath.relative_to(raw_dir)))

        fs = float(data.get("actual_rate_Sa_s", 0.0))
        actual_rates[k] = fs
        y = data.get("y_V", None)
        if y is None:
            continue
        y_arr = np.asarray(y, dtype=float)

        # ---- 1. Nearest-bin FFT ----
        amp_val, bin_freq, _ = nearest_bin_fft_amp(
            y_arr, fs, float(z_freqs[k]))
        fft_amps[k] = amp_val

        # ---- 2. Offline lock-in ----
        li_res = offline_lockin_r(
            y_arr, fs, float(z_freqs[k]),
            use_integer_cycles=OFFLINE_LOCKIN_USE_INTEGER_CYCLES,
        )
        li_R[k] = li_res["R"]
        li_X[k] = li_res["X"]
        li_Y[k] = li_res["Y"]
        li_phase_deg[k] = li_res["phase_deg"]

        # ---- 3. Hardware Demod R (如果有) ----
        if "demod_r_r_vector_mean_V" in data.files:
            hw_R_vec[k] = float(data["demod_r_r_vector_mean_V"])
            hw_R_scalar[k] = float(data["demod_r_r_mean_scalar_V"])
            hw_R_std[k] = float(data["demod_r_r_std_scalar_V"])
            hw_phase_deg[k] = float(data["demod_r_phase_vector_deg"])

    # ---- 对每条响应曲线取峰值 + 拟合 ----
    def extract_peak_with_fit(zf, resp, label):
        """对一条 response(zf) 曲线，同时提取 max_bin / single_lorentzian /
           double_lorentzian 峰位.

        返回 dict 包含三种方法的峰位、拟合曲线、拟合质量。
        主标定结果优先级: double_lorentzian > single_lorentzian > max_bin.
        兼容字段 fit_center_Hz / fit_curve_freqs / fit_curve_values 指向主结果.
        """
        out = {
            # ---- 兼容字段（指向 primary）----
            "fit_center_Hz": np.nan,
            "fit_width_Hz": np.nan,
            "fit_success": False,
            "fit_message": "no data",
            "fit_curve_freqs": np.array([]),
            "fit_curve_values": np.array([]),
            "fit_residual_std": np.nan,
            # ---- 各方法峰位 ----
            "f_peak_max_Hz": np.nan,
            "resp_peak_max_V": np.nan,
            "f_peak_single_Hz": np.nan,
            "f_peak_double_Hz": np.nan,
            "f_peak_primary_Hz": np.nan,
            "primary_source": "",
            "single_fwhm_Hz": np.nan,
            "double_fwhm_Hz": np.nan,
            # ---- 各方法拟合质量 ----
            "single_success": False,
            "double_success": False,
            "single_fit_message": "",
            "double_fit_message": "",
            "single_residual_std": np.nan,
            "double_residual_std": np.nan,
            # ---- 各方法拟合曲线 ----
            "fit_curve_single_freqs": np.array([]),
            "fit_curve_single_values": np.array([]),
            "fit_curve_double_freqs": np.array([]),
            "fit_curve_double_values": np.array([]),
            # ---- 原始数据 ----
            "resp_curve_freqs": np.array([]),
            "resp_curve_values": np.array([]),
            "n_points": 0,
            "label": label,
        }

        valid = np.isfinite(zf) & np.isfinite(resp) & (zf > 0)
        if not np.any(valid):
            out["fit_message"] = "no valid data"
            return out
        zf_v = np.asarray(zf, dtype=float)[valid]
        resp_v = np.asarray(resp, dtype=float)[valid]
        out["resp_curve_freqs"] = zf_v
        out["resp_curve_values"] = resp_v
        out["n_points"] = int(np.sum(valid))

        # ---- 1. max-bin ----
        peak_idx = int(np.argmax(resp_v))
        out["f_peak_max_Hz"] = float(zf_v[peak_idx])
        out["resp_peak_max_V"] = float(resp_v[peak_idx])

        # ---- 2. single Lorentzian ----
        single_fit = fit_single_lorentzian(zf_v, resp_v, min_points=FIT_MIN_POINTS)
        if single_fit["success"]:
            out["f_peak_single_Hz"] = float(single_fit["f0_Hz"])
            out["single_fwhm_Hz"] = float(single_fit["fwhm_Hz"])
            out["single_success"] = True
            out["single_fit_message"] = single_fit["message"]
            out["single_residual_std"] = single_fit.get("residual_std", np.nan)
            out["fit_curve_single_freqs"] = single_fit["fit_curve_freqs"]
            out["fit_curve_single_values"] = single_fit["fit_curve_values"]
        else:
            out["single_fit_message"] = single_fit.get("message", "fail")

        # ---- 3. double Lorentzian / Bloch steady-state ----
        double_fit = fit_double_lorentzian(zf_v, resp_v, min_points=FIT_MIN_POINTS)
        if double_fit["success"]:
            out["f_peak_double_Hz"] = float(double_fit["Omega_Ctrl_Hz"])
            out["double_fwhm_Hz"] = float(double_fit["fwhm_Hz"])
            out["double_success"] = True
            out["double_fit_message"] = double_fit["message"]
            out["double_residual_std"] = double_fit.get("residual_std", np.nan)
            out["fit_curve_double_freqs"] = double_fit["fit_curve_freqs"]
            out["fit_curve_double_values"] = double_fit["fit_curve_values"]
        else:
            out["double_fit_message"] = double_fit.get("message", "fail")

        # ---- 4. 范围检查 (关键约束) ----
        # 拟合中心 f0 / Omega 必须落在扫描频率范围内 [f_min_data, f_max_data]，
        # 否则视为 invalid (例如拟合给出 1 Hz 这种越界值)，
        # 不允许进入 f_peak_primary。
        f_min_data = float(zf_v.min())
        f_max_data = float(zf_v.max())
        out["f_min_scan_Hz"] = f_min_data
        out["f_max_scan_Hz"] = f_max_data

        # 注意 tolerance = 0 (严格落入)，如果数据靠近边界可以放宽。
        # 这里用严格约束，避免 f0 越界数据被误当主峰。
        if out["single_success"]:
            f0_s = float(single_fit["f0_Hz"])
            if not (f_min_data <= f0_s <= f_max_data):
                out["single_success"] = False
                out["single_fit_message"] = (
                    f"f0={f0_s:.2f} Hz outside scan range "
                    f"[{f_min_data:.2f}, {f_max_data:.2f}]"
                )
                # 保留 f_peak_single_Hz / fwhm 用于诊断，但 success=False
        if out["double_success"]:
            f0_d = float(double_fit["Omega_Ctrl_Hz"])
            if not (f_min_data <= f0_d <= f_max_data):
                out["double_success"] = False
                out["double_fit_message"] = (
                    f"Omega_Ctrl={f0_d:.2f} Hz outside scan range "
                    f"[{f_min_data:.2f}, {f_max_data:.2f}]"
                )

        # ---- Primary: double_lorentzian > single_lorentzian > max_bin ----
        fallback_reason = ""
        if out["double_success"]:
            out["f_peak_primary_Hz"] = out["f_peak_double_Hz"]
            out["primary_source"] = "double_lorentzian"
            out["fit_width_Hz"] = out["double_fwhm_Hz"]
            out["fit_residual_std"] = out["double_residual_std"]
            out["fit_curve_freqs"] = out["fit_curve_double_freqs"]
            out["fit_curve_values"] = out["fit_curve_double_values"]
        elif out["single_success"]:
            out["f_peak_primary_Hz"] = out["f_peak_single_Hz"]
            out["primary_source"] = "single_lorentzian"
            out["fit_width_Hz"] = out["single_fwhm_Hz"]
            out["fit_residual_std"] = out["single_residual_std"]
            out["fit_curve_freqs"] = out["fit_curve_single_freqs"]
            out["fit_curve_values"] = out["fit_curve_single_values"]
        else:
            out["f_peak_primary_Hz"] = out["f_peak_max_Hz"]
            out["primary_source"] = "max_bin"
            out["fit_width_Hz"] = np.nan
            if out["f_peak_max_Hz"] != out["f_peak_max_Hz"]:   # NaN
                fallback_reason = "no_valid_data"
                out["fit_message"] = "no valid data"
            elif (single_fit.get("success") and not out["single_success"]):
                fallback_reason = "single_fit_outside_scan_range"
                out["fit_message"] = (
                    "double & single fits failed/out-of-range, "
                    "fell back to max_bin"
                )
            elif (double_fit.get("success") and not out["double_success"]):
                fallback_reason = "double_fit_outside_scan_range"
                out["fit_message"] = (
                    "double fit center outside scan range, "
                    "single fit failed, fell back to max_bin"
                )
            else:
                fallback_reason = "all_fits_failed"
                out["fit_message"] = "all fits failed, fallback to max-bin"

        out["fallback_reason"] = fallback_reason

        # 兼容字段 fit_center_Hz = primary
        out["fit_center_Hz"] = out["f_peak_primary_Hz"]
        out["fit_success"] = out["double_success"] or out["single_success"]

        return out

    fft_summary = extract_peak_with_fit(z_freqs, fft_amps, "nearest_fft_bin")
    li_summary = extract_peak_with_fit(z_freqs, li_R, "offline_lockin")
    have_hw = np.any(np.isfinite(hw_R_vec))
    if have_hw:
        hw_summary = extract_peak_with_fit(z_freqs, hw_R_vec,
                                           "hardware_demod_r_vector")
    else:
        hw_summary = {
            "f_peak_max_Hz": np.nan, "resp_peak_max_V": np.nan,
            "fit_center_Hz": np.nan, "fit_width_Hz": np.nan,
            "fit_success": False, "fit_message": "no hardware data",
            "fit_curve_freqs": np.array([]),
            "fit_curve_values": np.array([]),
            "fit_residual_std": np.nan,
            "f_peak_single_Hz": np.nan, "f_peak_double_Hz": np.nan,
            "f_peak_primary_Hz": np.nan, "primary_source": "",
            "single_success": False, "double_success": False,
            "single_fwhm_Hz": np.nan, "double_fwhm_Hz": np.nan,
            "single_fit_message": "", "double_fit_message": "",
            "single_residual_std": np.nan, "double_residual_std": np.nan,
            "fit_curve_single_freqs": np.array([]),
            "fit_curve_single_values": np.array([]),
            "fit_curve_double_freqs": np.array([]),
            "fit_curve_double_values": np.array([]),
            "resp_curve_freqs": np.array([]),
            "resp_curve_values": np.array([]),
            "f_min_scan_Hz": np.nan, "f_max_scan_Hz": np.nan,
            "fallback_reason": "no_hardware_data",
            "n_points": 0,
            "label": "hardware_demod_r_vector",
        }

    # ---- 打印 ----
    def _fmt_center(c):
        return f"{c:.1f} Hz" if np.isfinite(c) else "NaN"

    print(f"  Z 频点:  {len(z_freqs)}")
    print(f"  FFT peak:    max_bin={_fmt_center(fft_summary['f_peak_max_Hz'])}, "
          f"single={_fmt_center(fft_summary['f_peak_single_Hz'])}, "
          f"double={_fmt_center(fft_summary['f_peak_double_Hz'])}, "
          f"primary={_fmt_center(fft_summary['fit_center_Hz'])}, "
          f"src={fft_summary['primary_source']}")
    print(f"  Lock-in peak: max_bin={_fmt_center(li_summary['f_peak_max_Hz'])}, "
          f"single={_fmt_center(li_summary['f_peak_single_Hz'])}, "
          f"double={_fmt_center(li_summary['f_peak_double_Hz'])}, "
          f"primary={_fmt_center(li_summary['fit_center_Hz'])}, "
          f"src={li_summary['primary_source']}, "
          f"FWHM={_fmt_center(li_summary['fit_width_Hz'])}")
    if have_hw:
        print(f"  HW demod_r:  max_bin={_fmt_center(hw_summary['f_peak_max_Hz'])}, "
              f"double={_fmt_center(hw_summary['f_peak_double_Hz'])}, "
              f"primary={_fmt_center(hw_summary['fit_center_Hz'])}, "
              f"src={hw_summary['primary_source']}")

    per_voltage_results[v] = {
        "xy_dc_voltage_V": float(v),
        "z_freq_Hz": z_freqs,
        "actual_rate_Sa_s": actual_rates,
        "fft_amp_V": fft_amps,
        "offline_lockin_r_V": li_R,
        "offline_lockin_x_V": li_X,
        "offline_lockin_y_V": li_Y,
        "offline_lockin_phase_deg": li_phase_deg,
        "hardware_demod_r_vector_V": hw_R_vec,
        "hardware_demod_r_scalar_V": hw_R_scalar,
        "hardware_demod_r_std_V": hw_R_std,
        "hardware_demod_r_phase_deg": hw_phase_deg,
        "files": file_paths,
        "fft_summary": fft_summary,
        "offline_lockin_summary": li_summary,
        "hardware_demod_r_summary": hw_summary,
    }


# %% Cell 7
# ============================================================
# 线性标定：f_peak_Hz = K_eff * XY_AW_OFFSET_VOLTAGE_V + B_eff
# ============================================================
print("\n" + "=" * 60)
print("线性标定: f_peak_Hz = K_eff * XY_AW_OFFSET_VOLTAGE_V + B_eff")
print("=" * 60)


def linear_calibration(x_arr, y_arr, min_points=LINEAR_FIT_MIN_POINTS,
                       x_label="XY_AW_OFFSET_VOLTAGE_V", y_label="f_peak_Hz"):
    """拟合 y = K * x + B，返回 K_eff / B_eff 及不确定度。"""
    out = {
        "success": False, "K_eff": None, "B_eff": None,
        "K_eff_unc": None, "B_eff_unc": None,
        "r_squared": None, "rmse_Hz": None, "n_points": 0,
        "residual_Hz": np.array([]),
        "x_arr": x_arr, "y_arr": y_arr,
        "x_label": x_label, "y_label": y_label,
        "message": "",
    }
    valid = np.isfinite(x_arr) & np.isfinite(y_arr)
    if int(np.sum(valid)) < min_points:
        out["message"] = "insufficient valid points"
        return out
    x_v = np.asarray(x_arr, dtype=float)[valid]
    y_v = np.asarray(y_arr, dtype=float)[valid]
    out["n_points"] = int(len(x_v))

    try:
        K_eff, B_eff = np.polyfit(x_v, y_v, 1)
        y_pred = K_eff * x_v + B_eff
        residual = y_v - y_pred
        ss_res = float(np.sum(residual**2))
        ss_tot = float(np.sum((y_v - np.mean(y_v))**2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        rmse = float(np.sqrt(ss_res / len(x_v)))

        # 误差: 简单 y = K x + B 的协方差矩阵 (假设 x 无误差)
        n = len(x_v)
        if n >= 3 and ss_tot > 0:
            s2 = ss_res / max(n - 2, 1)
            Sxx = float(np.sum((x_v - np.mean(x_v))**2))
            if Sxx > 0:
                K_unc = float(np.sqrt(s2 / Sxx))
                B_unc = float(np.sqrt(s2 * (1.0 / n
                                            + float(np.mean(x_v))**2 / Sxx)))
            else:
                K_unc = np.nan
                B_unc = np.nan
        else:
            K_unc = np.nan
            B_unc = np.nan

        out.update({
            "success": True,
            "K_eff": float(K_eff),
            "B_eff": float(B_eff),
            "K_eff_unc": K_unc,
            "B_eff_unc": B_unc,
            "r_squared": r2,
            "rmse_Hz": rmse,
            "residual_Hz": residual,
            "x_valid": x_v,
            "y_valid": y_v,
            "y_pred": y_pred,
            "message": "ok",
        })
    except Exception as e:
        out["message"] = f"polyfit failed: {type(e).__name__}: {e}"
    return out


# ---- 准备各方法的 f_peak vs XY_DC ----
n_v = len(xy_dc_sorted)
x_dc = np.array(xy_dc_sorted, dtype=float)

y_fit_fft = np.array([per_voltage_results[v]["fft_summary"]["fit_center_Hz"]
                      for v in xy_dc_sorted], dtype=float)
y_fit_li = np.array([per_voltage_results[v]["offline_lockin_summary"]["fit_center_Hz"]
                     for v in xy_dc_sorted], dtype=float)
y_max_fft = np.array([per_voltage_results[v]["fft_summary"]["f_peak_max_Hz"]
                      for v in xy_dc_sorted], dtype=float)
y_max_li = np.array([per_voltage_results[v]["offline_lockin_summary"]["f_peak_max_Hz"]
                     for v in xy_dc_sorted], dtype=float)
have_hw = any(np.any(np.isfinite(per_voltage_results[v][
                         "hardware_demod_r_vector_V"]))
              for v in xy_dc_sorted)
if have_hw:
    y_fit_hw = np.array([per_voltage_results[v][
                         "hardware_demod_r_summary"]["fit_center_Hz"]
                        for v in xy_dc_sorted], dtype=float)
    y_max_hw = np.array([per_voltage_results[v][
                         "hardware_demod_r_summary"]["f_peak_max_Hz"]
                        for v in xy_dc_sorted], dtype=float)
else:
    y_fit_hw = np.full(n_v, np.nan)
    y_max_hw = np.full(n_v, np.nan)

# ---- 各方法的 double / single / max_bin / primary 峰位及其拟合产物 ----
# 对每个 (method, peak_source) 维度都显式保存，便于 yaml/npz 完整记录

def _collect_peak_arrays(method_key):
    """从 per_voltage_results[v][method_key + '_summary'] 收集所有峰位数组."""
    out = {
        "primary": np.full(n_v, np.nan),
        "primary_source": [""] * n_v,
        "double": np.full(n_v, np.nan),
        "single": np.full(n_v, np.nan),
        "max_bin": np.full(n_v, np.nan),
        "fwhm_double": np.full(n_v, np.nan),
        "fwhm_single": np.full(n_v, np.nan),
        "success_double": np.zeros(n_v, dtype=bool),
        "success_single": np.zeros(n_v, dtype=bool),
    }
    for i, v in enumerate(xy_dc_sorted):
        s = per_voltage_results[v][method_key + "_summary"]
        out["primary"][i] = float(s.get("f_peak_primary_Hz", np.nan))
        out["primary_source"][i] = str(s.get("primary_source", ""))
        out["double"][i] = float(s.get("f_peak_double_Hz", np.nan))
        out["single"][i] = float(s.get("f_peak_single_Hz", np.nan))
        out["max_bin"][i] = float(s.get("f_peak_max_Hz", np.nan))
        out["fwhm_double"][i] = float(s.get("double_fwhm_Hz", np.nan))
        out["fwhm_single"][i] = float(s.get("single_fwhm_Hz", np.nan))
        out["success_double"][i] = bool(s.get("double_success", False))
        out["success_single"][i] = bool(s.get("single_success", False))
    return out


li_arrays = _collect_peak_arrays("offline_lockin")
y_primary_lockin = li_arrays["primary"]
primary_source_lockin = li_arrays["primary_source"]
y_double_lockin = li_arrays["double"]
y_single_lockin = li_arrays["single"]
y_maxbin_lockin = li_arrays["max_bin"]
fwhm_double_lockin = li_arrays["fwhm_double"]
fwhm_single_lockin = li_arrays["fwhm_single"]
success_double_lockin = li_arrays["success_double"]
success_single_lockin = li_arrays["success_single"]

fft_arrays = _collect_peak_arrays("fft")
y_primary_fft = fft_arrays["primary"]
primary_source_fft = fft_arrays["primary_source"]
y_double_fft = fft_arrays["double"]
y_single_fft = fft_arrays["single"]
y_maxbin_fft = fft_arrays["max_bin"]
fwhm_double_fft = fft_arrays["fwhm_double"]
fwhm_single_fft = fft_arrays["fwhm_single"]
success_double_fft = fft_arrays["success_double"]
success_single_fft = fft_arrays["success_single"]

if have_hw:
    hw_arrays = _collect_peak_arrays("hardware_demod_r")
    y_primary_hw = hw_arrays["primary"]
    primary_source_hw = hw_arrays["primary_source"]
    y_double_hw = hw_arrays["double"]
    y_single_hw = hw_arrays["single"]
    y_maxbin_hw = hw_arrays["max_bin"]
    fwhm_double_hw = hw_arrays["fwhm_double"]
    fwhm_single_hw = hw_arrays["fwhm_single"]
    success_double_hw = hw_arrays["success_double"]
    success_single_hw = hw_arrays["success_single"]
else:
    y_primary_hw = np.full(n_v, np.nan)
    primary_source_hw = [""] * n_v
    y_double_hw = np.full(n_v, np.nan)
    y_single_hw = np.full(n_v, np.nan)
    y_maxbin_hw = np.full(n_v, np.nan)
    fwhm_double_hw = np.full(n_v, np.nan)
    fwhm_single_hw = np.full(n_v, np.nan)
    success_double_hw = np.zeros(n_v, dtype=bool)
    success_single_hw = np.zeros(n_v, dtype=bool)

# ---- 线性拟合 ----
lin_fit_fft = linear_calibration(x_dc, y_fit_fft)
lin_fit_li = linear_calibration(x_dc, y_fit_li)
lin_max_fft = linear_calibration(x_dc, y_max_fft)
lin_max_li = linear_calibration(x_dc, y_max_li)
if have_hw:
    lin_fit_hw = linear_calibration(x_dc, y_fit_hw)
    lin_max_hw = linear_calibration(x_dc, y_max_hw)
else:
    lin_fit_hw = {"success": False, "message": "no hardware data"}
    lin_max_hw = {"success": False, "message": "no hardware data"}

print("\n[线性标定结果]")
for label, fit in [("FFT fit_center", lin_fit_fft),
                   ("Lock-in fit_center", lin_fit_li),
                   ("FFT max_bin", lin_max_fft),
                   ("Lock-in max_bin", lin_max_li)]:
    if fit.get("success"):
        print(f"  [{label:<22}] K_eff={fit['K_eff']:+.2f} ± "
              f"{fit['K_eff_unc']:.2f} Hz/V, "
              f"B_eff={fit['B_eff']:+.2f} ± {fit['B_eff_unc']:.2f} Hz, "
              f"R²={fit['r_squared']:.4f}, RMSE={fit['rmse_Hz']:.1f} Hz "
              f"(n={fit['n_points']})")
    else:
        print(f"  [{label:<22}] 拟合失败: {fit.get('message', '')}")
if have_hw:
    for label, fit in [("HW demod_r fit_center", lin_fit_hw),
                       ("HW demod_r max_bin", lin_max_hw)]:
        if fit.get("success"):
            print(f"  [{label:<22}] K_eff={fit['K_eff']:+.2f} ± "
                  f"{fit['K_eff_unc']:.2f} Hz/V, "
                  f"B_eff={fit['B_eff']:+.2f} ± {fit['B_eff_unc']:.2f} Hz, "
                  f"R²={fit['r_squared']:.4f}, RMSE={fit['rmse_Hz']:.1f} Hz "
                  f"(n={fit['n_points']})")

# 旧 AW 系数参考线
if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
    print(f"\n[历史参考, 不是本实验结论]")
    print(f"  A_ENV_K_LEGACY = {A_ENV_K_LEGACY} Hz/V")
    print(f"  A_ENV_B_LEGACY = {A_ENV_B_LEGACY} Hz")
    print(f"  旧预测 f = K_LEGACY * XY_DC + B_LEGACY")
    for v in x_dc:
        print(f"    XY_DC={v:.4f} V -> f_legacy={v * A_ENV_K_LEGACY + A_ENV_B_LEGACY:.1f} Hz")


# %% Cell 8
# ============================================================
# 绘图
# ============================================================

# ---- 图 1: xy_aw_calibration_curve.png ----
# f_peak vs XY_AW_OFFSET_VOLTAGE, 含线性拟合
fig1, ax1 = new_figure(kind="wide", height_mm=65)

# 主结果：lock-in fit_center（推荐主结果，按 docs/xy_dc_voltage_calibration.md）
if lin_fit_li.get("success"):
    color_li = "C0"
    ax1.plot(x_dc, y_fit_li, "o-", color=color_li, lw=1.4, ms=7,
             label=f"offline lock-in fit_center")
    x_line = np.linspace(x_dc.min(), x_dc.max(), 200)
    K_li = lin_fit_li["K_eff"]; B_li = lin_fit_li["B_eff"]
    ax1.plot(x_line, K_li * x_line + B_li, "--", color=color_li, lw=1.2,
             label=(f"Linear: f = {K_li:+.2f}·XY_DC "
                    f"{B_li:+.2f}\n"
                    f"  K_eff = {K_li:.2f} ± {lin_fit_li['K_eff_unc']:.2f} Hz/V, "
                    f"B_eff = {B_li:.2f} ± {lin_fit_li['B_eff_unc']:.2f} Hz, "
                    f"R²={lin_fit_li['r_squared']:.4f}"))

# 对照：FFT fit_center
if lin_fit_fft.get("success"):
    color_fft = "C1"
    ax1.plot(x_dc, y_fit_fft, "s-", color=color_fft, lw=1.0, ms=6, alpha=0.8,
             label="nearest-bin FFT fit_center")

# 对照：hardware demod_r (if available)
if have_hw and lin_fit_hw.get("success"):
    color_hw = "C3"
    ax1.plot(x_dc, y_fit_hw, "^-", color=color_hw, lw=1.0, ms=6, alpha=0.7,
             label="hardware Demod 3 fit_center (verify)")

# 旧 AW 参考线
if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
    x_line_ref = np.linspace(x_dc.min(), x_dc.max(), 200)
    y_ref = A_ENV_K_LEGACY * x_line_ref + A_ENV_B_LEGACY
    ax1.plot(x_line_ref, y_ref, ":", color="gray", lw=1.0,
             label=f"Legacy reference (NOT result): "
                   f"f = {A_ENV_K_LEGACY}·XY_DC + {A_ENV_B_LEGACY}")

ax1.set_xlabel("XY AW offset voltage (V)")
ax1.set_ylabel("f_peak (Hz)")
ax1.set_title("XY AW offset voltage calibration — f_peak vs XY_AW_OFFSET_VOLTAGE")
ax1.legend(fontsize=8, loc="best")
ax1.grid(False)
plt.gcf().set_layout_engine("constrained")
save_figure(fig1, results_dir / "xy_aw_calibration_curve.png", close=False)
plt.show()
print(f"已保存: xy_aw_calibration_curve.png")

# ---- 图 2: xy_aw_calibration_residual.png ----
fig2, ax2 = new_figure(kind="wide", height_mm=65)
plotted_any = False
for label, fit, color, marker in [
        ("offline lock-in", lin_fit_li, "C0", "o"),
        ("nearest-bin FFT", lin_fit_fft, "C1", "s"),
        ("HW demod_r", lin_fit_hw, "C3", "^")]:
    if fit.get("success") and len(fit.get("residual_Hz", [])) > 0:
        ax2.plot(fit["x_valid"], fit["residual_Hz"],
                 marker=marker, ls="-", color=color, lw=1.0, ms=6,
                 label=f"{label} residual (RMSE={fit['rmse_Hz']:.1f} Hz)")
        plotted_any = True
ax2.axhline(0, color="black", lw=0.8)
ax2.set_xlabel("XY AW offset voltage (V)")
ax2.set_ylabel("Residual (Hz)")
ax2.set_title("XY AW offset voltage calibration — Linear fit residual")
if plotted_any:
    ax2.legend(fontsize=8, loc="best")
ax2.grid(False)
plt.gcf().set_layout_engine("constrained")
save_figure(fig2, results_dir / "xy_aw_calibration_residual.png", close=False)
plt.show()
print(f"已保存: xy_aw_calibration_residual.png")

# ---- 图 3: frequency_response_examples.png ----
# 选 3-4 个代表性 XY AW offset 电压点画响应曲线
if len(xy_dc_sorted) >= 3:
    example_v_list = [xy_dc_sorted[0],
                      xy_dc_sorted[len(xy_dc_sorted) // 3],
                      xy_dc_sorted[2 * len(xy_dc_sorted) // 3],
                      xy_dc_sorted[-1]]
else:
    example_v_list = list(xy_dc_sorted)

fig3, axes3 = new_figure(nrows=len(example_v_list), ncols=1, kind="wide", height_mm=max(65, 55 * (len(example_v_list))), sharex=False)
if len(example_v_list) == 1:
    axes3 = [axes3]

for ax, v in zip(axes3, example_v_list):
    rec = per_voltage_results[v]
    fft_s = rec["fft_summary"]
    li_s = rec["offline_lockin_summary"]
    # FFT 曲线
    if np.any(np.isfinite(rec["fft_amp_V"])):
        ax.semilogy(rec["z_freq_Hz"], rec["fft_amp_V"],
                    "o-", color="C1", lw=0.8, ms=3, alpha=0.7,
                    label="nearest-bin FFT amp")
    # Lock-in 曲线
    valid_li = np.isfinite(rec["offline_lockin_r_V"]) & (rec["z_freq_Hz"] > 0)
    if np.any(valid_li):
        ax.semilogy(rec["z_freq_Hz"][valid_li],
                    rec["offline_lockin_r_V"][valid_li],
                    "^-", color="C0", lw=0.8, ms=3, alpha=0.7,
                    label="offline lock-in R")
    # Hardware (if available)
    if np.any(np.isfinite(rec["hardware_demod_r_vector_V"])):
        ax.semilogy(rec["z_freq_Hz"],
                    rec["hardware_demod_r_vector_V"],
                    "s-", color="C3", lw=0.8, ms=3, alpha=0.7,
                    label="HW demod_r vector")

    # 拟合曲线叠加 — 离线锁相 double / single
    # Lock-in double fit (主峰拟合，蓝色实线)
    if li_s.get("double_success") and len(li_s["fit_curve_double_freqs"]) > 0:
        ax.plot(li_s["fit_curve_double_freqs"], li_s["fit_curve_double_values"],
                "-", color="C0", lw=1.6,
                label=(f"LI double: f0={li_s['f_peak_double_Hz']:.1f} Hz, "
                       f"FWHM={li_s['double_fwhm_Hz']:.1f} Hz"))
    # Lock-in single fit (蓝色虚线)
    if li_s.get("single_success") and len(li_s["fit_curve_single_freqs"]) > 0:
        ax.plot(li_s["fit_curve_single_freqs"], li_s["fit_curve_single_values"],
                "--", color="C0", lw=1.0, alpha=0.75,
                label=(f"LI single: f0={li_s['f_peak_single_Hz']:.1f} Hz, "
                       f"FWHM={li_s['single_fwhm_Hz']:.1f} Hz"))
    # FFT double fit (橙色实线，较淡)
    if fft_s.get("double_success") and len(fft_s["fit_curve_double_freqs"]) > 0:
        ax.plot(fft_s["fit_curve_double_freqs"], fft_s["fit_curve_double_values"],
                "-", color="C1", lw=1.0, alpha=0.55,
                label=(f"FFT double: f0={fft_s['f_peak_double_Hz']:.1f} Hz, "
                       f"FWHM={fft_s['double_fwhm_Hz']:.1f} Hz"))
    # FFT single fit (橙色虚线，较淡)
    if fft_s.get("single_success") and len(fft_s["fit_curve_single_freqs"]) > 0:
        ax.plot(fft_s["fit_curve_single_freqs"], fft_s["fit_curve_single_values"],
                "--", color="C1", lw=0.8, alpha=0.45,
                label=(f"FFT single: f0={fft_s['f_peak_single_Hz']:.1f} Hz, "
                       f"FWHM={fft_s['single_fwhm_Hz']:.1f} Hz"))
    # Hardware double fit (若有 HW 数据)
    if np.any(np.isfinite(rec["hardware_demod_r_vector_V"])):
        hw_s = rec["hardware_demod_r_summary"]
        if hw_s.get("double_success") and len(hw_s["fit_curve_double_freqs"]) > 0:
            ax.plot(hw_s["fit_curve_double_freqs"],
                    hw_s["fit_curve_double_values"],
                    "-", color="C3", lw=0.9, alpha=0.5,
                    label=(f"HW double: f0={hw_s['f_peak_double_Hz']:.1f} Hz, "
                           f"FWHM={hw_s['double_fwhm_Hz']:.1f} Hz"))
        if hw_s.get("single_success") and len(hw_s["fit_curve_single_freqs"]) > 0:
            ax.plot(hw_s["fit_curve_single_freqs"],
                    hw_s["fit_curve_single_values"],
                    "--", color="C3", lw=0.8, alpha=0.4,
                    label=(f"HW single: f0={hw_s['f_peak_single_Hz']:.1f} Hz, "
                           f"FWHM={hw_s['single_fwhm_Hz']:.1f} Hz"))
    # 旧 AW 参考线
    if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
        f_ref = A_ENV_K_LEGACY * float(v) + A_ENV_B_LEGACY
        ax.axvline(f_ref, color="gray", ls=":", lw=0.8,
                   label=f"legacy f = {f_ref:.1f} Hz")

    ax.set_ylabel("Response (V)")
    ax.set_title(f"XY_DC = {v:.4f} V")
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(False)

axes3[-1].set_xlabel("Z RF drive frequency (Hz)")
fig3.suptitle("Frequency response examples — offline lock-in vs FFT vs HW",
              fontsize=8)
plt.gcf().set_layout_engine("constrained")
save_figure(fig3, results_dir / "frequency_response_examples.png", close=False)
plt.show()
print(f"已保存: frequency_response_examples.png")

# ---- 图 4: method_peak_comparison.png ----
# 同时画出 lock-in / FFT /(HW) 的 double / single / max_bin 共 6-9 条曲线，
# 并用粗黑线 + 黑色描边突出 lock-in primary（即 f_peak_lockin_primary_Hz，
# 优先级 double > single > max_bin）。
fig4, ax4 = new_figure(kind="wide", height_mm=65)

# 先画所有非 primary 曲线 (linestyle 区分: "-"=double, "--"=single, ":"=max_bin)
def _plot_one(y_arr, color, marker, ls, lw, ms, alpha, label):
    valid = np.isfinite(x_dc) & np.isfinite(y_arr)
    if np.any(valid):
        ax4.plot(x_dc[valid], y_arr[valid], marker=marker, ls=ls,
                 color=color, lw=lw, ms=ms, alpha=alpha, label=label)

# ---- offline lock-in (C0) ----
_plot_one(y_double_lockin, "C0", "o", "-",  1.0, 5, 0.55, "lock-in double")
_plot_one(y_single_lockin, "C0", "o", "--", 1.0, 4, 0.55, "lock-in single")
_plot_one(y_maxbin_lockin, "C0", "o", ":",  0.9, 3, 0.45, "lock-in max_bin")
# ---- nearest-bin FFT (C1) ----
_plot_one(y_double_fft, "C1", "s", "-",  1.0, 5, 0.55, "FFT double")
_plot_one(y_single_fft, "C1", "s", "--", 1.0, 4, 0.55, "FFT single")
_plot_one(y_maxbin_fft, "C1", "s", ":",  0.9, 3, 0.45, "FFT max_bin")
# ---- hardware Demod 3 (C3, 若存在) ----
if have_hw:
    _plot_one(y_double_hw, "C3", "^", "-",  1.0, 5, 0.55, "HW double")
    _plot_one(y_single_hw, "C3", "^", "--", 1.0, 4, 0.55, "HW single")
    _plot_one(y_maxbin_hw, "C3", "^", ":",  0.9, 3, 0.45, "HW max_bin")

# ---- PRIMARY 强调层 (lock-in primary = double > single > max_bin) ----
# 粗黑实线连接 + 黑色描边的大圆点; zorder 提高，使叠加在上层
valid_primary = np.isfinite(x_dc) & np.isfinite(y_primary_lockin)
if np.any(valid_primary):
    ax4.plot(x_dc[valid_primary], y_primary_lockin[valid_primary],
             ls="-", color="black", lw=2.4, alpha=0.95, zorder=10,
             label=f"LOCK-IN primary ({PEAK_STRATEGY})")
    ax4.plot(x_dc[valid_primary], y_primary_lockin[valid_primary],
             ls="", marker="o", ms=11,
             markerfacecolor="C0", markeredgecolor="black",
             markeredgewidth=1.6, zorder=11)

# ---- 主结果线性拟合线 (即 offline lock-in primary 的 K_eff · XY_DC + B_eff) ----
if lin_fit_li.get("success"):
    K_li = lin_fit_li["K_eff"]; B_li = lin_fit_li["B_eff"]
    x_line = np.linspace(x_dc.min(), x_dc.max(), 200)
    ax4.plot(x_line, K_li * x_line + B_li, "--", color="black", lw=1.2,
             alpha=0.85,
             label=(f"PRIMARY fit: K={K_li:+.2f} Hz/V, "
                    f"B={B_li:+.2f} Hz (R²={lin_fit_li['r_squared']:.4f})"))

# ---- 历史参考 ----
if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
    x_line_ref = np.linspace(x_dc.min(), x_dc.max(), 200)
    ax4.plot(x_line_ref,
             A_ENV_K_LEGACY * x_line_ref + A_ENV_B_LEGACY,
             ":", color="gray", lw=0.8, alpha=0.7,
             label=f"Legacy ref (NOT result): K={A_ENV_K_LEGACY}, B={A_ENV_B_LEGACY}")

ax4.set_xlabel("XY AW offset voltage (V)")
ax4.set_ylabel("Peak frequency (Hz)")
ax4.set_title(f"Method peak comparison — double / single / max_bin / primary "
              f"({PEAK_STRATEGY})")
ax4.legend(fontsize=7, loc="best", ncol=2)
ax4.grid(False)
plt.gcf().set_layout_engine("constrained")
save_figure(fig4, results_dir / "method_peak_comparison.png", close=False)
plt.show()
print(f"已保存: method_peak_comparison.png")


# %% Cell 9
# ============================================================
# 保存 analysis.yaml / analysis.json / xy_aw_calibration_results.npz
# ============================================================
print("\n" + "=" * 60)
print("保存分析结果")
print("=" * 60)


def _to_jsonable(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.float32, np.float64)):
        return float(o)
    if isinstance(o, (np.int32, np.int64)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, dict):
        return {k: _to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_jsonable(v) for v in o]
    return o


def _clean_fit_summary(s):
    """剔除 fit_curve_* 大数组，保留关键字段。"""
    if not isinstance(s, dict):
        return {}
    skip = {"fit_curve_freqs", "fit_curve_values",
            "resp_curve_freqs", "resp_curve_values",
            "fit_curve_single_freqs", "fit_curve_single_values",
            "fit_curve_double_freqs", "fit_curve_double_values"}
    return {k: _to_jsonable(v) for k, v in s.items() if k not in skip}


per_voltage_for_yaml = []
for v in xy_dc_sorted:
    rec = per_voltage_results[v]
    per_voltage_for_yaml.append({
        "xy_dc_voltage_V": float(v),
        "n_z_freq_points": int(len(rec["z_freq_Hz"])),
        "fft_summary": _clean_fit_summary(rec["fft_summary"]),
        "offline_lockin_summary": _clean_fit_summary(
            rec["offline_lockin_summary"]),
        "hardware_demod_r_summary": _clean_fit_summary(
            rec["hardware_demod_r_summary"]),
    })


def _linfit_to_clean(fit, primary_label):
    """linear_calibration 的 dict → 简洁可序列化字段。"""
    if not isinstance(fit, dict):
        return {}
    out = {
        "method": primary_label,
        "success": bool(fit.get("success", False)),
        "K_eff_Hz_per_V": fit.get("K_eff"),
        "B_eff_Hz": fit.get("B_eff"),
        "K_eff_unc_Hz_per_V": fit.get("K_eff_unc"),
        "B_eff_unc_Hz": fit.get("B_eff_unc"),
        "r_squared": fit.get("r_squared"),
        "rmse_Hz": fit.get("rmse_Hz"),
        "n_points": fit.get("n_points", 0),
        "message": fit.get("message", ""),
    }
    return out


analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "acquisition_mode_config": ACQUISITION_MODE,
    "peak_strategy": PEAK_STRATEGY,
    "primary_method": "offline_lockin",
    "primary_peak_source_policy": PEAK_STRATEGY,
    "offline_lockin_use_integer_cycles": OFFLINE_LOCKIN_USE_INTEGER_CYCLES,
    "n_xy_dc_points": int(len(xy_dc_sorted)),
    "xy_dc_voltage_list_V": [float(v) for v in xy_dc_sorted],
    "purpose": (
        "标定 XY_AW_OFFSET_VOLTAGE 与响应中心频率 f_peak 的线性关系，"
        "得到 K_eff / B_eff 用于后续 ConstXY 测量由目标频率换算控制电压。"
    ),
    "legacy_reference_NOT_result": {
        "A_ENV_K_LEGACY_Hz_per_V": A_ENV_K_LEGACY,
        "A_ENV_B_LEGACY_Hz": A_ENV_B_LEGACY,
        "note": "旧 AW 方案的历史系数，仅作参考；本实验不作为结论。",
    },
    # ---- 各方法 peak 数组 (与 npz 中同名字段保持一致) ----
    "peak_arrays": {
        "offline_lockin": {
            "f_peak_lockin_primary_Hz": [float(v) for v in y_primary_lockin],
            "primary_peak_source_lockin": list(primary_source_lockin),
            "f_peak_lockin_double_Hz": [float(v) for v in y_double_lockin],
            "f_peak_lockin_single_Hz": [float(v) for v in y_single_lockin],
            "f_peak_lockin_max_bin_Hz": [float(v) for v in y_maxbin_lockin],
            "fwhm_lockin_double_Hz": [float(v) for v in fwhm_double_lockin],
            "fwhm_lockin_single_Hz": [float(v) for v in fwhm_single_lockin],
            "success_lockin_double": [bool(x) for x in success_double_lockin],
            "success_lockin_single": [bool(x) for x in success_single_lockin],
        },
        "fft": {
            "f_peak_fft_primary_Hz": [float(v) for v in y_primary_fft],
            "primary_peak_source_fft": list(primary_source_fft),
            "f_peak_fft_double_Hz": [float(v) for v in y_double_fft],
            "f_peak_fft_single_Hz": [float(v) for v in y_single_fft],
            "f_peak_fft_max_bin_Hz": [float(v) for v in y_maxbin_fft],
            "fwhm_fft_double_Hz": [float(v) for v in fwhm_double_fft],
            "fwhm_fft_single_Hz": [float(v) for v in fwhm_single_fft],
            "success_fft_double": [bool(x) for x in success_double_fft],
            "success_fft_single": [bool(x) for x in success_single_fft],
        },
    },
    "per_voltage": per_voltage_for_yaml,
    "linear_calibration": {
        "formula": "f_peak_Hz = K_eff * XY_AW_OFFSET_VOLTAGE_V + B_eff",
        # ---- source 说明 ----
        "primary_method": "offline_lockin",
        "primary_peak_source_policy": PEAK_STRATEGY,
        # ---- 各方法线性拟合 ----
        "lockin_fit_center": _linfit_to_clean(
            lin_fit_li, "offline lock-in fit_center (PRIMARY)"),
        "fft_fit_center": _linfit_to_clean(lin_fit_fft,
                                           "nearest-bin FFT fit_center"),
        "lockin_max_bin": _linfit_to_clean(lin_max_li,
                                           "offline lock-in max_bin"),
        "fft_max_bin": _linfit_to_clean(lin_max_fft,
                                        "nearest-bin FFT max_bin"),
        "hw_fit_center": _linfit_to_clean(lin_fit_hw,
                                          "hardware Demod 3 fit_center"),
        "hw_max_bin": _linfit_to_clean(lin_max_hw,
                                       "hardware Demod 3 max_bin"),
        # ---- primary 系数 (明确来自 offline lock-in primary) ----
        "primary_K_eff_Hz_per_V": (
            lin_fit_li.get("K_eff") if lin_fit_li.get("success") else None),
        "primary_B_eff_Hz": (
            lin_fit_li.get("B_eff") if lin_fit_li.get("success") else None),
    },
}
if have_hw:
    analysis["peak_arrays"]["hardware"] = {
        "f_peak_hw_primary_Hz": [float(v) for v in y_primary_hw],
        "primary_peak_source_hw": list(primary_source_hw),
        "f_peak_hw_double_Hz": [float(v) for v in y_double_hw],
        "f_peak_hw_single_Hz": [float(v) for v in y_single_hw],
        "f_peak_hw_max_bin_Hz": [float(v) for v in y_maxbin_hw],
        "fwhm_hw_double_Hz": [float(v) for v in fwhm_double_hw],
        "fwhm_hw_single_Hz": [float(v) for v in fwhm_single_hw],
        "success_hw_double": [bool(x) for x in success_double_hw],
        "success_hw_single": [bool(x) for x in success_single_hw],
    }

# ---- 保存 YAML ----
a_yaml = results_dir / "analysis.yaml"
with open(a_yaml, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"已保存: {a_yaml}")

# ---- 保存 JSON ----
a_json = results_dir / "analysis.json"
with open(a_json, "w", encoding="utf-8") as f:
    json.dump(_to_jsonable(analysis), f, indent=2, ensure_ascii=False)
print(f"已保存: {a_json}")

# ---- 保存 NPZ (频响数据 + 标定结果) ----
save_npz = {
    # 标定主结果
    "xy_dc_voltage_V": x_dc,
    "y_fit_lockin_Hz": y_fit_li,           # 旧 alias = lock-in primary
    "y_fit_fft_Hz": y_fit_fft,             # 旧 alias = FFT primary
    "y_max_lockin_Hz": y_max_li,
    "y_max_fft_Hz": y_max_fft,
    # ---- offline lock-in 各方法峰位 ----
    "f_peak_lockin_primary_Hz": y_primary_lockin,
    "primary_peak_source_lockin": np.array(primary_source_lockin, dtype=object),
    "f_peak_lockin_double_Hz": y_double_lockin,
    "f_peak_lockin_single_Hz": y_single_lockin,
    "f_peak_lockin_max_bin_Hz": y_maxbin_lockin,
    "fwhm_lockin_double_Hz": fwhm_double_lockin,
    "fwhm_lockin_single_Hz": fwhm_single_lockin,
    "success_lockin_double": success_double_lockin,
    "success_lockin_single": success_single_lockin,
    # ---- FFT 各方法峰位 ----
    "f_peak_fft_primary_Hz": y_primary_fft,
    "primary_peak_source_fft": np.array(primary_source_fft, dtype=object),
    "f_peak_fft_double_Hz": y_double_fft,
    "f_peak_fft_single_Hz": y_single_fft,
    "f_peak_fft_max_bin_Hz": y_maxbin_fft,
    "fwhm_fft_double_Hz": fwhm_double_fft,
    "fwhm_fft_single_Hz": fwhm_single_fft,
    "success_fft_double": success_double_fft,
    "success_fft_single": success_single_fft,
    # ---- 线性拟合系数 ----
    "K_eff_lockin_Hz_per_V": (lin_fit_li.get("K_eff")
                               if lin_fit_li.get("success") else np.nan),
    "B_eff_lockin_Hz": (lin_fit_li.get("B_eff")
                         if lin_fit_li.get("success") else np.nan),
    "K_eff_lockin_unc_Hz_per_V": (lin_fit_li.get("K_eff_unc")
                                   if lin_fit_li.get("success") else np.nan),
    "B_eff_lockin_unc_Hz": (lin_fit_li.get("B_eff_unc")
                             if lin_fit_li.get("success") else np.nan),
    "r_squared_lockin": (lin_fit_li.get("r_squared")
                          if lin_fit_li.get("success") else np.nan),
    "rmse_Hz_lockin": (lin_fit_li.get("rmse_Hz")
                        if lin_fit_li.get("success") else np.nan),
    "K_eff_fft_Hz_per_V": (lin_fit_fft.get("K_eff")
                            if lin_fit_fft.get("success") else np.nan),
    "B_eff_fft_Hz": (lin_fit_fft.get("B_eff")
                      if lin_fit_fft.get("success") else np.nan),
    "K_eff_fft_unc_Hz_per_V": (lin_fit_fft.get("K_eff_unc")
                                if lin_fit_fft.get("success") else np.nan),
    "B_eff_fft_unc_Hz": (lin_fit_fft.get("B_eff_unc")
                          if lin_fit_fft.get("success") else np.nan),
    # ---- 配置与策略 ----
    "peak_strategy": np.array(PEAK_STRATEGY),
    "primary_method": np.array("offline_lockin"),
    "offline_lockin_use_integer_cycles": np.array(
        OFFLINE_LOCKIN_USE_INTEGER_CYCLES),
}
if have_hw:
    save_npz["y_fit_hw_Hz"] = y_fit_hw       # 旧 alias = HW primary
    save_npz["y_max_hw_Hz"] = y_max_hw
    # ---- HW 各方法峰位 ----
    save_npz["f_peak_hw_primary_Hz"] = y_primary_hw
    save_npz["primary_peak_source_hw"] = np.array(primary_source_hw, dtype=object)
    save_npz["f_peak_hw_double_Hz"] = y_double_hw
    save_npz["f_peak_hw_single_Hz"] = y_single_hw
    save_npz["f_peak_hw_max_bin_Hz"] = y_maxbin_hw
    save_npz["fwhm_hw_double_Hz"] = fwhm_double_hw
    save_npz["fwhm_hw_single_Hz"] = fwhm_single_hw
    save_npz["success_hw_double"] = success_double_hw
    save_npz["success_hw_single"] = success_single_hw
    save_npz["K_eff_hw_Hz_per_V"] = (lin_fit_hw.get("K_eff")
                                      if lin_fit_hw.get("success") else np.nan)
    save_npz["B_eff_hw_Hz"] = (lin_fit_hw.get("B_eff")
                                if lin_fit_hw.get("success") else np.nan)

# 旧 AW 参考（仅作参考）
if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
    save_npz["A_ENV_K_LEGACY_Hz_per_V"] = np.float64(A_ENV_K_LEGACY)
    save_npz["A_ENV_B_LEGACY_Hz"] = np.float64(A_ENV_B_LEGACY)
    save_npz["legacy_f_peak_Hz"] = np.float64(
        A_ENV_K_LEGACY) * x_dc + np.float64(A_ENV_B_LEGACY)

# 每个 XY AW offset 电压点的完整响应曲线
for i, v in enumerate(xy_dc_sorted):
    rec = per_voltage_results[v]
    tag = f"v{i:02d}_{v:.4f}V".replace(".", "p").replace("-", "n")
    save_npz[f"{tag}_z_freq_Hz"] = rec["z_freq_Hz"]
    save_npz[f"{tag}_fft_amp_V"] = rec["fft_amp_V"]
    save_npz[f"{tag}_offline_lockin_r_V"] = rec["offline_lockin_r_V"]
    save_npz[f"{tag}_offline_lockin_x_V"] = rec["offline_lockin_x_V"]
    save_npz[f"{tag}_offline_lockin_y_V"] = rec["offline_lockin_y_V"]
    save_npz[f"{tag}_offline_lockin_phase_deg"] = rec[
        "offline_lockin_phase_deg"]
    if have_hw:
        save_npz[f"{tag}_hw_demod_r_vector_V"] = rec[
            "hardware_demod_r_vector_V"]
        save_npz[f"{tag}_hw_demod_r_scalar_V"] = rec[
            "hardware_demod_r_scalar_V"]
        save_npz[f"{tag}_hw_demod_r_phase_deg"] = rec[
            "hardware_demod_r_phase_deg"]
    # 拟合中心
    save_npz[f"{tag}_fit_center_Hz_fft"] = np.float64(
        rec["fft_summary"].get("fit_center_Hz", np.nan))
    save_npz[f"{tag}_fit_center_Hz_lockin"] = np.float64(
        rec["offline_lockin_summary"].get("fit_center_Hz", np.nan))
    save_npz[f"{tag}_fit_width_Hz_lockin"] = np.float64(
        rec["offline_lockin_summary"].get("fit_width_Hz", np.nan))

npz_path = results_dir / "xy_aw_calibration_results.npz"
np.savez(npz_path, **save_npz)
print(f"已保存: {npz_path}")


# %% Cell 10
# ========== 关键结果摘要 ==========
print("\n" + "=" * 60)
print("XY AW offset 电压标定 — 关键结果")
print("=" * 60)
print(f"实验类型:     {EXPERIMENT_TYPE}")
print(f"数据目录:     {DATA_DIR}")
print(f"采集模式:     {ACQUISITION_MODE}")
print(f"峰位策略:     {PEAK_STRATEGY}")
print(f"XY AW offset 电压点数: {len(xy_dc_sorted)}")
print()
if lin_fit_li.get("success"):
    K = lin_fit_li["K_eff"]
    B = lin_fit_li["B_eff"]
    print(f"🎯 PRIMARY (offline lock-in fit_center):")
    print(f"   f_peak_Hz = ({K:+.4f} ± {lin_fit_li['K_eff_unc']:.4f}) * "
          f"XY_AW_OFFSET_VOLTAGE_V + ({B:+.4f} ± {lin_fit_li['B_eff_unc']:.4f})")
    print(f"   R² = {lin_fit_li['r_squared']:.4f}, "
          f"RMSE = {lin_fit_li['rmse_Hz']:.2f} Hz, n = {lin_fit_li['n_points']}")
    print()
    print(f"📐 使用方法: 给定目标中心频率 TARGET_FREQ_Hz，")
    print(f"   XY_AW_OFFSET_VOLTAGE_V = (TARGET_FREQ_Hz - B_eff) / K_eff")
    print(f"   例如: TARGET_FREQ_Hz = 2000 -> "
          f"XY_AW_OFFSET_VOLTAGE_V = {(2000 - B) / K:.4f} V")
else:
    print("⚠ 离线锁相线性标定失败")

if lin_fit_fft.get("success"):
    print(f"\n[cross-check, FFT fit_center]:")
    print(f"   K_eff = {lin_fit_fft['K_eff']:.2f} Hz/V, "
          f"B_eff = {lin_fit_fft['B_eff']:.2f} Hz, "
          f"R² = {lin_fit_fft['r_squared']:.4f}")

if have_hw and lin_fit_hw.get("success"):
    print(f"\n[verify, HW Demod 3 fit_center]:")
    print(f"   K_eff = {lin_fit_hw['K_eff']:.2f} Hz/V, "
          f"B_eff = {lin_fit_hw['B_eff']:.2f} Hz, "
          f"R² = {lin_fit_hw['r_squared']:.4f}")

if A_ENV_K_LEGACY is not None and A_ENV_B_LEGACY is not None:
    print(f"\n[legacy reference, NOT result]:")
    print(f"   A_ENV_K_LEGACY = {A_ENV_K_LEGACY} Hz/V, "
          f"A_ENV_B_LEGACY = {A_ENV_B_LEGACY} Hz")

print("\n[OK] XY AW offset 电压标定 — 数据分析与可视化完成")

# %%