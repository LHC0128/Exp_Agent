# %% [markdown] Cell 0
# # 射频场频率响应 — 数据分析与可视化（XY 恒定 DC 场方案，重构版）
#
# 加载 ConstXY 频率响应采集数据，对每个频点的 Demod 0 Y 时域数据做：
# 1. **direct FFT**：整段 1 s y_V 直接 FFT，取最接近 drive_freq 的 bin
# 2. **offline lock-in**：对同一段 y_V 做软件锁相 (X=2⟨y·cos⟩, Y=2⟨y·sin⟩, R=√(X²+Y²))
# 3. **hardware demod_r**：硬件 Demod 3 通过**物理回环**读数 (vector / scalar mean)：
#    Demod0 Y -> Aux Out 2 -> physical cable -> Signal Input 2 (DC coupled) -> Demod 3
#
# 三组结果交叉对比并计算 -3 dB 带宽。
#
# ## 数据结构
# - 数据文件: `data/RF_Field_Sensitivity_ConstXY_FreqSweep/<MMDD_HHMM_tag>/{raw,results}/`
# - 频点逐点保存:
#   - `raw/freq_XXXX_FREQHz.npz` (signal) 或 `raw/freq_XXXX_0Hz_BASELINE.npz` (baseline)
#   - `raw/frequency_index.json` — 频点索引 (兼容 NPZ/JSON 读取)
# - 字段:
#   - `freq_Hz`、`is_baseline`、`z_drive_amplitude_Vpp`、`acquisition_mode`、`actual_rate_Sa_s`
#   - DAQ: `time_s`、`y_V`
#   - Demod 3: `demod_r_x_values_V/y_values_V/r_values_V/theta_values_rad`、
#     `demod_r_x_mean_V/y_mean_V/r_vector_mean_V/r_mean_scalar_V/r_std_scalar_V`、
#     `demod_r_phase_vector_rad/phase_vector_deg`
#
# ## 取值方法（`DAQ_RESPONSE_METHOD`）
# - `"nearest_fft_bin"`（默认）：整段 1 s y_V 直接 FFT，取最接近 drive_freq 的 bin，
#   避免在高频段 Welch 分辨率不足时被固定干扰线误选。
# - `"welch_nearest_bin"`：Welch 谱，但只取最接近 drive_freq 的 bin（不做窗口搜索）。
# - `"welch_local_peak"`：Welch 谱 + 局部窗口取最大峰（仅 debug，不参与默认带宽）。
#
# **无需连接任何仪器**，仅读取本地数据文件进行分析。

# %% Cell 1
from pathlib import Path
import sys
# 自动定位项目根目录（以 params/ 目录为标记）
project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import yaml
import json

from scipy import optimize as scipy_optimize

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from scipy import signal as scipy_signal

print("库导入完成")

# %% Cell 2
# ========== 选择数据目录 ==========
EXPERIMENT_TYPE = "RF_Field_Sensitivity_ConstXY_FreqSweep"

# True 自动选最新一组数据；False 时手动指定
USE_LATEST = True
DATA_DIR_OVERRIDE = None  # 例如 "data/RF_Field_Sensitivity_ConstXY_FreqSweep/0701_1039_constxy_freq"
# DATA_DIR_OVERRIDE = "data/RF_Field_Sensitivity_ConstXY_FreqSweep/0701_1039_constxy_freq"

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
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_constxy_freq"

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

# ========== daq_fft_y 取值方法配置 ==========
# "nearest_fft_bin"   — 整段 1 s y_V 直接 FFT，取最接近 drive_freq 的 bin（推荐）
# "welch_nearest_bin" — Welch 谱，但只取最接近 drive_freq 的 bin（不做窗口搜索）
# "welch_local_peak"  — Welch 谱 + 局部窗口取最大峰（仅 debug；不参与默认带宽）
DAQ_RESPONSE_METHOD = "nearest_fft_bin"
LOCAL_PEAK_REL_WINDOW = 0.01       # welch_local_peak 时的窗口半宽（±1%）
MAX_PICK_FREQ_ERROR_HZ = 20.0      # picked_freq 偏离 drive_freq 超过该值 → suspicious
EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH = True  # 带宽/峰值计算是否排除 suspicious 点

# ========== 离线锁相配置 ==========
# 对同一段 y_V 执行软件锁相，与 direct FFT / hardware demod_r 三组对比。
# True  — 只使用整数个周期的样本 (减少边界效应)
# False — 使用整段 y_V
OFFLINE_LOCKIN_USE_INTEGER_CYCLES = True

# ========== Lorentzian 拟合配置 ==========
# 在离散 -3 dB FWHM 之外，另对每组响应做 Lorentzian 拟合得到亚 bin 精度的线宽。
# 提供两个物理模型：
#   1) 单 Lorentzian 反平方（最简近似）：
#       R(f) = A · [hwhm² / ((f - f0)² + hwhm²)]
#   2) 双 Lorentzian 叠加（dispersive + absorptive, 物理更准确）：
#       |R_xx(f)|² = P0² · (Γ² + f²) /
#                      ((Γ² - f² + Ω_Ctrl²)² + 4 Γ² f²)
#       拟合 R(f) 时整体开根号：
#       R(f) = C + P0 · √(Γ² + f²) /
#                       √((Γ² - f² + Ω_Ctrl²)² + 4 Γ² f²)
#     其中 C 是测量 offset（基线），Ω_Ctrl 应与 LARMOR_FREQ_HZ 自洽。
#     这就是 spin 1/2 在 Larmor 控制下的稳态 Bloch 响应，远端呈 1/f 衰减。
ENABLE_LORENTZIAN_FIT = True
FIT_MODEL = "double_lorentzian"   # "single_lorentzian" / "double_lorentzian"
                                  # 单 Lorentzian 只是简化的反平方近似；
                                  # 默认走 double_lorentzian（物理模型，物理线宽 Γ）

# ---- 单 Lorentzian（旧模型，作为对照）----
LORENTZIAN_FIT_DEFAULT_A = 1.0
LORENTZIAN_FIT_DEFAULT_F0_HZ = None
LORENTZIAN_FIT_DEFAULT_HWHM_HZ = 200.0
LORENTZIAN_FIT_MAX_HWHM_HZ = None
LORENTZIAN_FIT_MIN_POINTS = 6
LORENTZIAN_FIT_USE_SUSPICIOUS = False

# ---- 双 Lorentzian（物理模型，推荐）----
DLZ_GAMMA_INIT_HZ = 100.0         # 初始 Γ 猜测 (Hz)
DLZ_GAMMA_MIN_HZ = 1e-3           # Γ 下界（避免无衰减发散）
DLZ_GAMMA_MAX_HZ = None           # None 时取 (f_max - f_min)/2
DLZ_OMEGA_INIT_HZ = None          # 初始 Ω_Ctrl 猜测；None 时取 -3dB peak_freq
DLZ_OMEGA_MIN_HZ = 1.0
DLZ_OMEGA_MAX_HZ = None           # None 时取 f_max
DLZ_OFFSET_INIT_V = 0.0
DLZ_INCLUDE_OFFSET = True         # False 时强制 C=0
DLZ_MIN_POINTS = 6
DLZ_USE_SUSPICIOUS = False        # True = 把 suspicious 点也用于拟合

# ========== 加载实验配置 ==========
config_path = DATA_DIR / "experiment_config.yaml"
if config_path.exists():
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    print(f"实验配置已加载")
else:
    print(f"⚠ 未找到 experiment_config.yaml")
    config = {}

acquisition_mode = config.get("acquisition_mode", "both")
xy_const = config.get("xy_const_field", {})
LARMOR_FREQ_HZ = xy_const.get("LARMOR_FREQ_Hz")
XY_DC_VOLTAGE_V = xy_const.get("XY_DC_VOLTAGE_V")
A_ENV_K = xy_const.get("A_ENV_K_Hz_per_V")
A_ENV_B = xy_const.get("A_ENV_B_Hz")

print(f"采集模式: {acquisition_mode}")
print(f"daq_fft_y 取值方法: {DAQ_RESPONSE_METHOD}")
if LARMOR_FREQ_HZ is not None:
    print(f"XY 恒定场: Larmor = {LARMOR_FREQ_HZ} Hz, "
          f"XY_DC_VOLTAGE = {XY_DC_VOLTAGE_V:.4f} V "
          f"(A_ENV_K={A_ENV_K}, A_ENV_B={A_ENV_B})")

# ========== 加载频点索引 ==========
index_path = raw_dir / "frequency_index.npz"
if not index_path.exists():
    raise FileNotFoundError(f"未找到频点索引: {index_path}")

index_json_path = raw_dir / "frequency_index.json"
if index_json_path.exists():
    with open(index_json_path, encoding="utf-8") as f:
        freq_index = json.load(f)
    print(f"频点索引 (JSON) 已加载: {len(freq_index)} 个频点")
else:
    idx_npz = np.load(index_path, allow_pickle=True)
    freq_index = idx_npz["records"].tolist()
    print(f"频点索引 (NPZ) 已加载: {len(freq_index)} 个频点")

# ========== 加载所有频点数据 ==========
point_data = []
for rec in freq_index:
    fpath = raw_dir / rec["file"]
    if not fpath.exists():
        print(f"⚠ 频点文件不存在: {fpath}")
        continue
    data = np.load(fpath, allow_pickle=True)
    point_data.append({
        "idx": rec["idx"],
        "freq_Hz": rec["freq_Hz"],
        "is_baseline": rec["is_baseline"],
        "data": data,
    })

point_data.sort(key=lambda r: r["idx"])
print(f"已加载 {len(point_data)} 个频点数据")

baseline_pts = [p for p in point_data if p["is_baseline"]]
signal_pts = [p for p in point_data if not p["is_baseline"]]
print(f"  基线点 (f=0): {len(baseline_pts)} 个")
print(f"  有效信号点:   {len(signal_pts)} 个")
if signal_pts:
    print(f"  频率范围:     {signal_pts[0]['freq_Hz']:.1f} ~ "
          f"{signal_pts[-1]['freq_Hz']:.1f} Hz")

# %% Cell 3
# ============================================================
# 频点级数据提取
# ============================================================
# 提取每个频点的驱动响应
# - daq_fft_y 模式：根据 DAQ_RESPONSE_METHOD 提取驱动频率处幅值
# - demod_r 模式：直接使用 demod_r_*_mean_V


def _windowed_fft_full(y: np.ndarray, fs: float):
    """整段 N 点 Hann FFT，返回单边振幅谱 (V).

    df = fs/N ≈ 1 Hz (POINT_DURATION=1 s, fs=50000 Sa/s)。
    Hann 窗旁瓣约 -31 dB，主瓣宽度 ≈ 2 bins。
    """
    n = len(y)
    y = y - np.mean(y)
    window = scipy_signal.windows.hann(n, sym=False)
    yw = y * window
    Y = np.fft.rfft(yw)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    cg = window.sum()
    amp = np.abs(Y) * 2.0 / cg
    df = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    return freqs, amp, df


def extract_drive_response(y: np.ndarray, fs: float, drive_freq: float,
                            method: str = "nearest_fft_bin",
                            local_peak_rel_window: float = 0.01):
    """提取驱动频率处的响应.

    参数
    ----------
    y : ndarray
        时域信号 (V)
    fs : float
        采样率 (Sa/s)
    drive_freq : float
        目标驱动频率 (Hz)
    method : str
        "nearest_fft_bin"   — 整段直接 FFT，取最接近 drive_freq 的 bin
        "welch_nearest_bin" — Welch 谱，取最接近 drive_freq 的 bin
        "welch_local_peak"  — Welch 谱 + ±local_peak_rel_window 内取最大峰
    local_peak_rel_window : float
        method="welch_local_peak" 时的窗口半宽（相对值）

    返回
    -------
    dict
        amplitude_V, picked_freq_Hz, freq_error_Hz, method, is_suspicious,
        bin_freq_Hz, nperseg_used, df_Hz
    """
    out = {
        "amplitude_V": np.nan,
        "picked_freq_Hz": np.nan,
        "freq_error_Hz": np.nan,
        "method": method,
        "is_suspicious": False,
        "bin_freq_Hz": np.nan,
        "nperseg_used": None,
        "df_Hz": np.nan,
    }
    if y is None or len(y) < 4:
        return out
    n = len(y)
    y0 = y - np.mean(y)

    if method == "nearest_fft_bin":
        freqs, amp, df = _windowed_fft_full(y0, fs)
        idx = int(np.round(drive_freq / df))
        idx = max(0, min(idx, len(amp) - 1))
        amp_val = float(amp[idx])
        bin_freq = float(freqs[idx])
        picked_freq = bin_freq
        out["nperseg_used"] = n
    elif method == "welch_nearest_bin":
        nperseg = min(n, max(int(0.5 * fs / max(drive_freq, 0.1)), 256))
        if nperseg < 8:
            nperseg = 8
        f_w, psd = scipy_signal.welch(y0, fs=fs, nperseg=nperseg,
                                      scaling="density", detrend="constant")
        df = f_w[1] - f_w[0] if len(f_w) > 1 else 1.0
        amp = np.sqrt(2.0 * psd * df)
        idx = int(np.round(drive_freq / df))
        idx = max(0, min(idx, len(amp) - 1))
        amp_val = float(amp[idx])
        bin_freq = float(f_w[idx])
        picked_freq = bin_freq
        out["nperseg_used"] = int(nperseg)
    elif method == "welch_local_peak":
        nperseg = min(n, max(int(0.5 * fs / max(drive_freq, 0.1)), 256))
        if nperseg < 8:
            nperseg = 8
        f_w, psd = scipy_signal.welch(y0, fs=fs, nperseg=nperseg,
                                      scaling="density", detrend="constant")
        df = f_w[1] - f_w[0] if len(f_w) > 1 else 1.0
        amp = np.sqrt(2.0 * psd * df)
        win_half = max(drive_freq * local_peak_rel_window, 2.0 * df)
        mask = (f_w >= max(0.0, drive_freq - win_half)) & \
               (f_w <= drive_freq + win_half)
        if not np.any(mask):
            return out
        local_amp = amp[mask]
        local_freqs = f_w[mask]
        j = int(np.argmax(local_amp))
        amp_val = float(local_amp[j])
        bin_freq = float(local_freqs[j])
        picked_freq = bin_freq
        out["nperseg_used"] = int(nperseg)
    else:
        raise ValueError(f"未知 DAQ_RESPONSE_METHOD: {method!r}")

    out["amplitude_V"] = amp_val
    out["bin_freq_Hz"] = bin_freq
    out["picked_freq_Hz"] = float(picked_freq)
    out["freq_error_Hz"] = float(picked_freq - drive_freq)
    out["df_Hz"] = float(df)
    out["is_suspicious"] = bool(abs(out["freq_error_Hz"]) > MAX_PICK_FREQ_ERROR_HZ)
    return out


def extract_baseline_amplitude(y: np.ndarray, fs: float,
                                f_lo: float = 10.0,
                                f_hi: float = 5000.0) -> float:
    """对 0 Hz 基线数据计算 [f_lo, f_hi] Hz 频段中位振幅（仅做背景估计）."""
    if y is None or len(y) < 4:
        return np.nan
    freqs, amp, _ = _windowed_fft_full(y, fs)
    band = (freqs >= f_lo) & (freqs <= f_hi)
    if not np.any(band):
        return np.nan
    return float(np.median(amp[band]))


def extract_offline_lockin_response(y: np.ndarray, fs: float, drive_freq: float,
                                     use_integer_cycles: bool = True):
    """对同一段 y_V 执行软件锁相 (offline lock-in)，与 FFT / 硬件 demod_r 对比.

    实现：
      - y 去均值
      - t = np.arange(N) / fs
      - ref_cos = cos(2π·drive_freq·t)
      - ref_sin = sin(2π·drive_freq·t)
      - X = 2·mean(y·ref_cos)
      - Y = 2·mean(y·ref_sin)
      - R = sqrt(X² + Y²)
      - phase_rad = atan2(Y, X)

    参数
    ----------
    y : ndarray
        时域信号 (V)
    fs : float
        采样率 (Sa/s)
    drive_freq : float
        目标驱动频率 (Hz)
    use_integer_cycles : bool
        True  — 只使用整数个周期的样本（减少非整数周期边界效应）；
                n_cycles = floor(duration·drive_freq)
                若 n_cycles < 1 则返回 is_valid=False
        False — 使用整段 y_V

    返回
    -------
    dict
        offline_lockin_x_V, offline_lockin_y_V, offline_lockin_r_V,
        offline_lockin_phase_rad, offline_lockin_phase_deg,
        n_cycles_used, n_samples_used, is_valid
    """
    out = {
        "offline_lockin_x_V": np.nan,
        "offline_lockin_y_V": np.nan,
        "offline_lockin_r_V": np.nan,
        "offline_lockin_phase_rad": np.nan,
        "offline_lockin_phase_deg": np.nan,
        "n_cycles_used": 0,
        "n_samples_used": 0,
        "is_valid": False,
    }
    if y is None or len(y) < 4 or fs <= 0:
        return out

    duration = len(y) / fs

    if use_integer_cycles:
        n_cycles = int(np.floor(duration * drive_freq))
        if n_cycles < 1:
            # 不足一个周期，标记为 invalid
            return out
        # 用整数周期对应的样本数
        n_samples = int(round(n_cycles * fs / drive_freq))
        n_samples = min(n_samples, len(y))
        if n_samples < 4:
            return out
        y_seg = y[:n_samples]
        out["n_cycles_used"] = n_cycles
        out["n_samples_used"] = n_samples
    else:
        y_seg = y
        out["n_cycles_used"] = int(duration * drive_freq)
        out["n_samples_used"] = len(y_seg)

    y_seg = y_seg - np.mean(y_seg)
    t = np.arange(len(y_seg)) / fs
    ref_cos = np.cos(2.0 * np.pi * drive_freq * t)
    ref_sin = np.sin(2.0 * np.pi * drive_freq * t)

    X = 2.0 * np.mean(y_seg * ref_cos)
    Y = 2.0 * np.mean(y_seg * ref_sin)
    R = float(np.sqrt(X * X + Y * Y))
    phase_rad = float(np.arctan2(Y, X))
    phase_deg = float(math.degrees(phase_rad)) if False else float(np.degrees(phase_rad))

    out.update({
        "offline_lockin_x_V": float(X),
        "offline_lockin_y_V": float(Y),
        "offline_lockin_r_V": R,
        "offline_lockin_phase_rad": phase_rad,
        "offline_lockin_phase_deg": phase_deg,
        "is_valid": True,
    })
    return out


# ---- 构建响应数组 ----
records_daq = []      # 每个元素是一个 dict
records_demod_r = []  # 每个元素是一个 dict
baseline_amp = np.nan

if acquisition_mode in ("daq_fft_y", "both"):
    # 先估计基线背景（与取值方法无关）
    for p in baseline_pts:
        d = p["data"]
        if "y_V" not in d.files:
            continue
        y = d["y_V"]
        fs = float(d.get("actual_rate_Sa_s", 0.0))
        if fs <= 0:
            continue
        baseline_amp = extract_baseline_amplitude(y, fs)
        print(f"基线背景幅值 (median, 10-5000 Hz): {baseline_amp:.4e} V")
        break

    # 提取每个频点
    for p in signal_pts:
        d = p["data"]
        if "y_V" not in d.files:
            continue
        y = d["y_V"]
        fs = float(d.get("actual_rate_Sa_s", 0.0))
        if fs <= 0:
            continue
        res = extract_drive_response(
            y, fs, p["freq_Hz"],
            method=DAQ_RESPONSE_METHOD,
            local_peak_rel_window=LOCAL_PEAK_REL_WINDOW,
        )
        amp = res["amplitude_V"]
        amp_corrected = (max(amp - baseline_amp, 0.0)
                         if np.isfinite(amp) else np.nan)
        records_daq.append({
            "freq_Hz": p["freq_Hz"],
            "amp_raw_V": amp,
            "amp_corrected_V": amp_corrected,
            "picked_freq_Hz": res["picked_freq_Hz"],
            "freq_error_Hz": res["freq_error_Hz"],
            "is_suspicious": res["is_suspicious"],
            "method": res["method"],
            "bin_freq_Hz": res["bin_freq_Hz"],
            "df_Hz": res["df_Hz"],
        })

    if records_daq:
        freqs_daq = np.array([r["freq_Hz"] for r in records_daq])
        amps_raw = np.array([r["amp_raw_V"] for r in records_daq])
        amps_corr = np.array([r["amp_corrected_V"] for r in records_daq])
        picked_freqs = np.array([r["picked_freq_Hz"] for r in records_daq])
        freq_errors = np.array([r["freq_error_Hz"] for r in records_daq])
        is_suspicious_arr = np.array([r["is_suspicious"] for r in records_daq])
        n_susp = int(np.sum(is_suspicious_arr))
        print(f"\ndaq_fft_y 模式 ({DAQ_RESPONSE_METHOD}): {len(records_daq)} 个频点")
        print(f"  幅值范围 (raw):       "
              f"[{np.nanmin(amps_raw):.4e}, {np.nanmax(amps_raw):.4e}] V")
        print(f"  幅值范围 (corrected): "
              f"[{np.nanmin(amps_corr):.4e}, {np.nanmax(amps_corr):.4e}] V")
        print(f"  suspicious 点数:      {n_susp}")
        if n_susp > 0:
            susp_idx = np.where(is_suspicious_arr)[0]
            print(f"  suspicious 频率:      "
                  f"{freqs_daq[susp_idx].tolist()[:10]}"
                  f"{'...' if n_susp > 10 else ''}")
    else:
        freqs_daq = np.array([])
        amps_raw = amps_corr = np.array([])
        picked_freqs = freq_errors = is_suspicious_arr = np.array([])
        print("⚠ daq_fft_y 模式无可用数据")

if acquisition_mode in ("demod_r", "both"):
    for p in signal_pts:
        d = p["data"]
        if "demod_r_r_vector_mean_V" not in d.files:
            continue
        records_demod_r.append({
            "freq_Hz": p["freq_Hz"],
            "R_vector_V": float(d["demod_r_r_vector_mean_V"]),
            "R_scalar_mean_V": float(d["demod_r_r_mean_scalar_V"]),
            "R_scalar_std_V": float(d["demod_r_r_std_scalar_V"]),
            "X_mean_V": float(d["demod_r_x_mean_V"]),
            "Y_mean_V": float(d["demod_r_y_mean_V"]),
            "phase_vector_deg": float(d["demod_r_phase_vector_deg"]),
            "phase_vector_rad": float(d["demod_r_phase_vector_rad"]),
        })
    if records_demod_r:
        freqs_dR = np.array([r["freq_Hz"] for r in records_demod_r])
        R_vec = np.array([r["R_vector_V"] for r in records_demod_r])
        R_scalar = np.array([r["R_scalar_mean_V"] for r in records_demod_r])
        R_scalar_std = np.array([r["R_scalar_std_V"] for r in records_demod_r])
        X_mean = np.array([r["X_mean_V"] for r in records_demod_r])
        Y_mean = np.array([r["Y_mean_V"] for r in records_demod_r])
        phase_vec_deg = np.array([r["phase_vector_deg"] for r in records_demod_r])
        phase_vec_rad = np.array([r["phase_vector_rad"] for r in records_demod_r])
        print(f"\ndemod_r 模式: {len(records_demod_r)} 个频点")
        print(f"  R_vec 范围:  [{np.min(R_vec):.4e}, {np.max(R_vec):.4e}] V")
        print(f"  R_scalar 范围: [{np.min(R_scalar):.4e}, {np.max(R_scalar):.4e}] V")
        if np.any(R_vec > 0):
            print(f"  R_scalar / R_vec median: "
                  f"{np.median(R_scalar[R_vec > 0] / R_vec[R_vec > 0]):.4f}")
    else:
        freqs_dR = np.array([])
        R_vec = R_scalar = R_scalar_std = np.array([])
        X_mean = Y_mean = np.array([])
        phase_vec_deg = phase_vec_rad = np.array([])
        print("⚠ demod_r 模式无可用数据")

# ---- 离线锁相（对每一段 y_V，独立于采集模式）----
# 与 daq_fft_y 共用同一段时域数据；硬件 demod_r 来自 npz。
# 0 Hz baseline 不参与（整数周期为 0 → is_valid=False）。
records_offline_lockin = []
for p in signal_pts:
    d = p["data"]
    if "y_V" not in d.files:
        continue
    y = d["y_V"]
    fs = float(d.get("actual_rate_Sa_s", 0.0))
    if fs <= 0:
        continue
    res = extract_offline_lockin_response(
        y, fs, p["freq_Hz"],
        use_integer_cycles=OFFLINE_LOCKIN_USE_INTEGER_CYCLES,
    )
    records_offline_lockin.append({
        "freq_Hz": p["freq_Hz"],
        **res,
    })

if records_offline_lockin:
    freqs_li = np.array([r["freq_Hz"] for r in records_offline_lockin])
    li_x = np.array([r["offline_lockin_x_V"] for r in records_offline_lockin])
    li_y = np.array([r["offline_lockin_y_V"] for r in records_offline_lockin])
    li_r = np.array([r["offline_lockin_r_V"] for r in records_offline_lockin])
    li_phase_deg = np.array([r["offline_lockin_phase_deg"]
                              for r in records_offline_lockin])
    li_phase_rad = np.array([r["offline_lockin_phase_rad"]
                              for r in records_offline_lockin])
    li_n_cycles = np.array([r["n_cycles_used"]
                              for r in records_offline_lockin],
                             dtype=int)
    li_n_samples = np.array([r["n_samples_used"]
                              for r in records_offline_lockin],
                             dtype=int)
    li_valid = np.array([r["is_valid"]
                          for r in records_offline_lockin], dtype=bool)
    n_invalid = int(np.sum(~li_valid))
    n_valid = int(np.sum(li_valid))
    print(f"\noffline lock-in (use_integer_cycles="
          f"{OFFLINE_LOCKIN_USE_INTEGER_CYCLES}): {len(records_offline_lockin)} 个频点")
    print(f"  valid: {n_valid}, invalid: {n_invalid}")
    if n_valid > 0:
        print(f"  R 范围 (valid): "
              f"[{np.nanmin(li_r[li_valid]):.4e}, "
              f"{np.nanmax(li_r[li_valid]):.4e}] V")
else:
    freqs_li = li_x = li_y = li_r = np.array([])
    li_phase_deg = li_phase_rad = np.array([])
    li_n_cycles = li_n_samples = np.array([], dtype=int)
    li_valid = np.array([], dtype=bool)
    print("⚠ offline lock-in 无可用数据")

# %% Cell 4
# ============================================================
# 带宽计算（-3 dB 准则）
# ============================================================
def compute_bandwidth(freqs: np.ndarray, response: np.ndarray,
                       is_suspicious: np.ndarray = None,
                       exclude_suspicious: bool = False):
    """根据 -3 dB 准则计算带宽.

    参数
    ----------
    freqs, response : ndarray
        频率与响应曲线
    is_suspicious : ndarray or None
        与 freqs 等长的 bool 数组，标记可疑点
    exclude_suspicious : bool
        True 时，peak 和 bandwidth 计算均排除可疑点

    返回
    -------
    dict
        peak_freq_Hz, peak_value, f_low_Hz, f_high_Hz, bandwidth_Hz,
        q_factor, threshold, bandwidth_is_censored, n_points, n_excluded
    """
    out = {
        "peak_freq_Hz": None,
        "peak_value": None,
        "f_low_Hz": None,
        "f_high_Hz": None,
        "bandwidth_Hz": None,
        "q_factor": None,
        "threshold": None,
        "bandwidth_is_censored": False,
        "n_points": int(len(freqs)),
        "n_excluded": 0,
    }
    if len(freqs) == 0 or len(response) == 0:
        return out
    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    if exclude_suspicious and is_suspicious is not None and len(is_suspicious) == len(freqs):
        susp_mask = np.asarray(is_suspicious, dtype=bool)
        valid = valid & ~susp_mask
        out["n_excluded"] = int(np.sum(susp_mask & (freqs > 0)))
    if not np.any(valid):
        return out
    f_valid = freqs[valid]
    r_valid = response[valid]
    peak_idx = int(np.argmax(r_valid))
    peak_freq = float(f_valid[peak_idx])
    peak_val = float(r_valid[peak_idx])
    threshold = peak_val / np.sqrt(2.0)

    above = r_valid >= threshold
    if not np.any(above):
        return out
    above_idx = np.where(above)[0]
    f_low = float(f_valid[above_idx[0]])
    f_high = float(f_valid[above_idx[-1]])
    bandwidth = f_high - f_low
    q_factor = peak_freq / bandwidth if bandwidth > 0 else 0.0

    censored = False
    if above_idx[0] == 0 or above_idx[-1] == len(f_valid) - 1:
        censored = True

    out.update({
        "peak_freq_Hz": peak_freq,
        "peak_value": peak_val,
        "f_low_Hz": f_low,
        "f_high_Hz": f_high,
        "bandwidth_Hz": bandwidth,
        "q_factor": q_factor,
        "threshold": threshold,
        "bandwidth_is_censored": censored,
    })
    return out


def fit_lorentzian(freqs: np.ndarray, response: np.ndarray,
                    is_suspicious: np.ndarray = None,
                    exclude_suspicious: bool = True,
                    p0_A: float = None,
                    p0_f0: float = None,
                    p0_hwhm: float = None,
                    hwhm_upper: float = None,
                    min_points: int = 6):
    """对频率响应做 0-baseline Lorentzian 拟合，得到亚 bin 精度的线宽 (FWHM).

    模型：
        R(f) = A · [hwhm² / ((f - f0)² + hwhm²)]
    其中 A 是峰位响应，f0 是共振中心，hwhm 是半高半宽，FWHM = 2·hwhm。

    参数
    ----------
    freqs, response : ndarray
        频率与响应曲线
    is_suspicious : ndarray or None
        与 freqs 等长的 bool 数组
    exclude_suspicious : bool
        True 时，peak 寻找与拟合都排除可疑点
    p0_A, p0_f0, p0_hwhm : float or None
        初始猜测；为 None 时自动基于数据估计
    hwhm_upper : float or None
        hwhm 上界；为 None 时取 (f_max - f_min)/2
    min_points : int
        拟合所需的最少有效点数

    返回
    -------
    dict
        success, A, f0_Hz, hwhm_Hz, fwhm_Hz,
        A_unc, f0_unc_Hz, hwhm_unc_Hz, fwhm_unc_Hz,
        residual_std, residual_max, n_points, fit_curve_freqs, fit_curve_values,
        message
    """
    n_out = 17  # noqa: F841  # 占位，保留字段总数文档
    out_keys = ["success",
                "A", "f0_Hz", "hwhm_Hz", "fwhm_Hz",
                "A_unc", "f0_unc_Hz", "hwhm_unc_Hz", "fwhm_unc_Hz",
                "residual_std", "residual_max",
                "n_points",
                "fit_curve_freqs", "fit_curve_values",
                "message", "model"]
    out = {k: None for k in out_keys}
    out["model"] = "A * hwhm^2 / ((f - f0)^2 + hwhm^2)"
    out["n_points"] = 0
    if freqs is None or response is None or len(freqs) < min_points:
        out["message"] = f"insufficient points (<{min_points})"
        return out

    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    if exclude_suspicious and is_suspicious is not None \
            and len(is_suspicious) == len(freqs):
        valid = valid & ~np.asarray(is_suspicious, dtype=bool)
    if int(np.sum(valid)) < min_points:
        out["message"] = "too few valid points after exclusion"
        return out

    f_v = np.asarray(freqs, dtype=float)[valid]
    r_v = np.asarray(response, dtype=float)[valid]
    out["n_points"] = int(np.sum(valid))

    # 自动初始猜测：从数据峰位估值
    peak_idx = int(np.argmax(r_v))
    A_init = p0_A if p0_A is not None else float(r_v[peak_idx])
    f0_init = p0_f0 if p0_f0 is not None else float(f_v[peak_idx])
    hwhm_init = p0_hwhm if p0_hwhm is not None else max(
        (float(f_v.max()) - float(f_v.min())) / 4.0,
        10.0,
    )
    if hwhm_upper is None:
        hwhm_upper = max((float(f_v.max()) - float(f_v.min())) / 2.0,
                          10.0 * float(np.diff(np.sort(f_v)).mean()))

    def model(f, A, f0, hwhm):
        denom = (f - f0) ** 2 + hwhm ** 2
        return A * hwhm ** 2 / denom

    try:
        popt, pcov = scipy_optimize.curve_fit(
            model, f_v, r_v,
            p0=[A_init, f0_init, hwhm_init],
            bounds=([0.0, float(f_v.min()), 1e-3],
                    [10.0 * max(A_init, 1.0), float(f_v.max()), hwhm_upper]),
            maxfev=10000,
        )
        A_fit, f0_fit, hwhm_fit = float(popt[0]), float(popt[1]), float(popt[2])
        perr = np.sqrt(np.diag(pcov))
        A_unc, f0_unc, hwhm_unc = (
            float(perr[0]), float(perr[1]), float(perr[2]))
        # FWHM 不确定度由线性误差传播（∂FWHM/∂hwhm = 2）
        fwhm_unc = float(2.0 * hwhm_unc)

        residual = r_v - model(f_v, *popt)
        residual_std = float(np.std(residual))
        residual_max = float(np.max(np.abs(residual)))

        # 生成拟合曲线（用密集频率网格方便画图）
        fit_grid = np.linspace(float(f_v.min()), float(f_v.max()), 400)
        fit_vals = model(fit_grid, *popt)

        out.update({
            "success": True,
            "A": A_fit,
            "f0_Hz": f0_fit,
            "hwhm_Hz": hwhm_fit,
            "fwhm_Hz": float(2.0 * hwhm_fit),
            "A_unc": A_unc,
            "f0_unc_Hz": f0_unc,
            "hwhm_unc_Hz": hwhm_unc,
            "fwhm_unc_Hz": fwhm_unc,
            "residual_std": residual_std,
            "residual_max": residual_max,
            "fit_curve_freqs": fit_grid,
            "fit_curve_values": fit_vals,
            "message": "ok",
        })
    except Exception as e:
        out["message"] = f"fit failed: {type(e).__name__}: {e}"
    return out


def fit_doublelorentzian(freqs: np.ndarray, response: np.ndarray,
                          is_suspicious: np.ndarray = None,
                          exclude_suspicious: bool = True,
                          p0_P0: float = None,
                          p0_Gamma: float = None,
                          p0_Omega: float = None,
                          p0_offset: float = None,
                          Gamma_min: float = 1e-3,
                          Gamma_max: float = None,
                          Omega_min: float = 1.0,
                          Omega_max: float = None,
                          include_offset: bool = True,
                          min_points: int = 6,
                          LARMOR_FREQ_HZ: float = None):
    """拟合 spin 1/2 在 Larmor 控制下的稳态 Bloch 响应（双 Lorentzian 叠加）.

    模型 (|R_xx|²，开根号后拟合 R)：
        R(f) = C + P0 · √(Γ² + f²) /
                         √((Γ² - f² + Ω_Ctrl²)² + 4 Γ² f²)
    其中 C 是测量 offset（基线），Ω_Ctrl 应与 LARMOR_FREQ_HZ 自洽。

    参数
    ----------
    freqs, response : ndarray
        频率与响应 (R 幅度，不是 R²) 曲线。
    is_suspicious, exclude_suspicious :
        可疑点过滤。
    p0_P0, p0_Gamma, p0_Omega, p0_offset : float or None
        初始猜测，None 时自动估计。
    Gamma_min / Gamma_max :
        Γ 边界 (Hz)。
    Omega_min / Omega_max :
        Ω_Ctrl 边界 (Hz)。
    include_offset : bool
        False 时强制 C=0（不拟合 offset）。
    min_points : int
        拟合所需最少有效点数。
    LARMOR_FREQ_HZ : float or None
        仅用于打印备注（不参与拟合）。

    返回
    -------
    dict
        success, P0, Gamma_Hz, Omega_Ctrl_Hz, offset_V,
        P0_unc, Gamma_unc_Hz, Omega_unc_Hz, offset_unc_V,
        residual_std, residual_max, n_points,
        fit_curve_freqs, fit_curve_values,
        message, model
    """
    out_keys = ["success",
                "P0", "Gamma_Hz", "Omega_Ctrl_Hz", "offset_V",
                "P0_unc", "Gamma_unc_Hz", "Omega_unc_Hz", "offset_unc_V",
                "residual_std", "residual_max", "n_points",
                "fit_curve_freqs", "fit_curve_values",
                "message", "model", "LARMOR_FREQ_Hz_input"]
    out = {k: None for k in out_keys}
    out["model"] = (
        "C + P0 * sqrt(Gamma^2 + f^2) / "
        "sqrt((Gamma^2 - f^2 + Omega_Ctrl^2)^2 + 4 * Gamma^2 * f^2)")
    out["LARMOR_FREQ_Hz_input"] = LARMOR_FREQ_HZ
    out["n_points"] = 0
    if freqs is None or response is None or len(freqs) < min_points:
        out["message"] = f"insufficient points (<{min_points})"
        return out

    valid = np.isfinite(freqs) & np.isfinite(response) & (freqs > 0)
    if exclude_suspicious and is_suspicious is not None \
            and len(is_suspicious) == len(freqs):
        valid = valid & ~np.asarray(is_suspicious, dtype=bool)
    if int(np.sum(valid)) < min_points:
        out["message"] = "too few valid points after exclusion"
        return out

    f_v = np.asarray(freqs, dtype=float)[valid]
    r_v = np.asarray(response, dtype=float)[valid]
    out["n_points"] = int(np.sum(valid))

    f_min_data = float(f_v.min())
    f_max_data = float(f_v.max())
    peak_idx = int(np.argmax(r_v))
    peak_freq_data = float(f_v[peak_idx])
    peak_val = float(r_v[peak_idx])
    valley_val = float(np.min(r_v))

    # ---- 初始猜测 ----
    Omega_0 = p0_Omega if p0_Omega is not None else peak_freq_data
    if p0_Gamma is not None:
        Gamma_0 = p0_Gamma
    else:
        # 用峰值点响应的 0-baseline (近似 peak - right tail) 反推 Γ
        # 在 Γ << Ω 时，peak_val ≈ P0 · 1/(2Γ)·√(1+(Γ/Ω)²) ≈ P0/(2Γ)
        # 没有 P0 信息时，假设 P0 ≈ peak_val * 2 * Gamma_0
        Gamma_0 = 200.0

    # P0 反推：Γ << Ω 时 peak_val ≈ P0/(2Γ) → P0 ≈ 2Γ · peak_val
    P0_0 = p0_P0 if p0_P0 is not None else float(2.0 * Gamma_0 * (peak_val - valley_val))

    C_0 = p0_offset if p0_offset is not None else float(np.min(r_v))

    # ---- 边界 ----
    if Gamma_max is None:
        Gamma_max = max((f_max_data - f_min_data) / 2.0, 10.0)
    if Omega_max is None:
        Omega_max = max(f_max_data * 1.05, Omega_0 * 2.0)

    P0_max = max(10.0 * P0_0, abs(r_v.max() - C_0) * (Gamma_max + f_max_data))

    if include_offset:
        bounds_full = ([0.0, Gamma_min, Omega_min,
                        -10.0 * abs(C_0) - 1e-3],
                       [P0_max, Gamma_max, Omega_max,
                        10.0 * (abs(C_0) + 1e-3)])
    else:
        bounds_full = None

    def model(f, P0, Gamma, Omega, C):
        num = np.sqrt(Gamma**2 + f**2)
        d1 = Gamma**2 - f**2 + Omega**2
        d2 = 4.0 * Gamma**2 * f**2
        den = np.sqrt(d1 * d1 + d2)
        return C + P0 * num / np.maximum(den, 1e-12)

    def model_no_offset(f, P0, Gamma, Omega):
        return model(f, P0, Gamma, Omega, 0.0)

    def _do_curve_fit(p0_init, bounds_):
        if include_offset:
            return scipy_optimize.curve_fit(
                model, f_v, r_v, p0=list(p0_init),
                bounds=bounds_, maxfev=20000,
            )
        return scipy_optimize.curve_fit(
            model_no_offset, f_v, r_v, p0=list(p0_init[:3]),
            bounds=bounds_, maxfev=20000,
        )

    # ---- 多重 restart + 全局搜索, 选最优 ----
    best_popt = None
    best_pcov = None
    best_resid = np.inf

    # (1) Differential Evolution 全局搜索 (避免卡局部最优)
    if include_offset:
        try:
            def obj(p):
                P0, G, O, C = p
                if G < Gamma_min or O < Omega_min or O > Omega_max:
                    return 1e15
                r_pred = model(f_v, P0, G, O, C)
                if not np.all(np.isfinite(r_pred)):
                    return 1e15
                return float(np.sum((r_v - r_pred) ** 2))

            res_de = scipy_optimize.differential_evolution(
                obj, bounds=bounds_full,
                maxiter=300, tol=1e-9, seed=42,
                polish=False, init="sobol",
            )
            if res_de.fun < best_resid:
                best_resid = res_de.fun
                best_popt = list(res_de.x)
        except Exception:
            pass

    # (2) 多重 Levenberg-Marquardt restart, 散布在 Γ 几个数量级
    candidate_gammas = [Gamma_0 * f for f in [0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]]
    candidate_omegas = [Omega_0 * f for f in [0.95, 0.98, 1.0, 1.02, 1.05]]
    candidate_P0s = [P0_0 * f for f in [0.5, 0.7, 1.0, 1.5]]
    for g_init in candidate_gammas:
        for o_init in candidate_omegas:
            for p_init in candidate_P0s:
                p0_init = [p_init, g_init, o_init, C_0] \
                    if include_offset else [p_init, g_init, o_init]
                try:
                    popt_, pcov_ = _do_curve_fit(
                        p0_init,
                        (bounds_full[:3] if include_offset
                         else (bounds_full[:3] if bounds_full else None))
                        if include_offset else
                        ([0.0, Gamma_min, Omega_min],
                         [P0_max, Gamma_max, Omega_max]),
                    )
                    rpred = (model(f_v, *popt_) if include_offset
                              else model_no_offset(f_v, *popt_))
                    resid = float(np.sum((r_v - rpred) ** 2))
                    if resid < best_resid:
                        best_resid = resid
                        best_popt = list(popt_) if include_offset else \
                            list(popt_) + [0.0]
                        best_pcov = pcov_
                except Exception:
                    continue

    if best_popt is None:
        out["message"] = "all fits failed"
        return out

    # (3) 在 best_popt 附近精修
    bounds_local = bounds_full if include_offset else (
        [0.0, Gamma_min, Omega_min],
        [P0_max, Gamma_max, Omega_max],
    )
    try:
        popt_final, pcov_final = _do_curve_fit(best_popt, bounds_local)
        rpred_final = (model(f_v, *popt_final) if include_offset
                        else model_no_offset(f_v, *popt_final))
        resid_final = float(np.sum((r_v - rpred_final) ** 2))
        if resid_final <= best_resid * 1.05:
            best_popt = list(popt_final) if include_offset else \
                list(popt_final) + [0.0]
            best_pcov = pcov_final
    except Exception:
        pass

    popt = best_popt
    P0_fit, Gamma_fit, Omega_fit, offset_fit = (
        float(popt[0]), float(popt[1]), float(popt[2]), float(popt[3]))
    perr = np.sqrt(np.diag(best_pcov))
    P0_unc, Gamma_unc, Omega_unc, offset_unc = (
        float(perr[0]), float(perr[1]),
        float(perr[2]), float(perr[3]))
    residual = r_v - (model(f_v, *popt) if include_offset
                      else model_no_offset(f_v, *popt))

    fit_grid = np.linspace(f_min_data, f_max_data, 400)
    fit_vals = (model(fit_grid, *popt) if include_offset
                else model_no_offset(fit_grid, *popt))

    out.update({
        "success": True,
        "P0": P0_fit,
        "Gamma_Hz": Gamma_fit,
        "Omega_Ctrl_Hz": Omega_fit,
        "offset_V": offset_fit,
        "P0_unc": P0_unc,
        "Gamma_unc_Hz": Gamma_unc,
        "Omega_unc_Hz": Omega_unc,
        "offset_unc_V": offset_unc,
        "residual_std": float(np.std(residual)),
        "residual_max": float(np.max(np.abs(residual))),
        "fit_curve_freqs": fit_grid,
        "fit_curve_values": fit_vals,
        "message": "ok",
    })
    return out


# daq_fft_y 计算（按配置排除 suspicious）
is_susp_daq = (is_suspicious_arr
               if (acquisition_mode in ("daq_fft_y", "both")
                   and len(is_suspicious_arr) > 0)
               else None)
bandwidth_daq_raw = compute_bandwidth(
    freqs_daq if acquisition_mode in ("daq_fft_y", "both") else np.array([]),
    amps_raw if acquisition_mode in ("daq_fft_y", "both") else np.array([]),
    is_suspicious=is_susp_daq,
    exclude_suspicious=EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH,
)
bandwidth_daq_corr = compute_bandwidth(
    freqs_daq if acquisition_mode in ("daq_fft_y", "both") else np.array([]),
    amps_corr if acquisition_mode in ("daq_fft_y", "both") else np.array([]),
    is_suspicious=is_susp_daq,
    exclude_suspicious=EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH,
)
bandwidth_daq_fft = bandwidth_daq_corr  # 别名：direct FFT 在此即为 daq_fft 主结果

# 离线锁相（无效点自动被 compute_bandwidth 的 valid mask 排除）
bandwidth_offline_lockin = compute_bandwidth(
    freqs_li, li_r,
)

# 硬件 demod_r — vector mean（主响应）
bandwidth_hardware_demod_r_vector = compute_bandwidth(
    freqs_dR if acquisition_mode in ("demod_r", "both") else np.array([]),
    R_vec if acquisition_mode in ("demod_r", "both") else np.array([]),
)
# 硬件 demod_r — scalar mean（诊断 mean(R) 正偏置）
bandwidth_hardware_demod_r_scalar = compute_bandwidth(
    freqs_dR if acquisition_mode in ("demod_r", "both") else np.array([]),
    R_scalar if acquisition_mode in ("demod_r", "both") else np.array([]),
)
bandwidth_hardware_demod_r = bandwidth_hardware_demod_r_vector  # 向后兼容别名
bandwidth_dR = bandwidth_hardware_demod_r_vector  # 向后兼容别名

# ---- 0. Lorentzian 拟合（亚 bin 精度线宽）----
lorentzian_daq_fft = {"enabled": False}
lorentzian_offline_lockin = {"enabled": False}
lorentzian_hardware_demod_r = {"enabled": False}

if ENABLE_LORENTZIAN_FIT:
    # ---- (a) Direct FFT ----
    if acquisition_mode in ("daq_fft_y", "both") and len(freqs_daq) > 0:
        lorentzian_daq_fft = fit_lorentzian(
            freqs_daq, amps_corr,
            is_suspicious=is_susp_daq,
            exclude_suspicious=(EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH
                                and not LORENTZIAN_FIT_USE_SUSPICIOUS),
            p0_hwhm=(bandwidth_daq_fft.get("bandwidth_Hz") or 0) / 2.0
                     if bandwidth_daq_fft.get("bandwidth_Hz") else None,
            min_points=LORENTZIAN_FIT_MIN_POINTS,
            hwhm_upper=LORENTZIAN_FIT_MAX_HWHM_HZ,
        )
        lorentzian_daq_fft["enabled"] = bool(lorentzian_daq_fft.get("success"))
        if lorentzian_daq_fft["enabled"]:
            print(f"[Lorentzian] direct FFT:  A={lorentzian_daq_fft['A']:.3e}, "
                  f"f0={lorentzian_daq_fft['f0_Hz']:.1f} ± "
                  f"{lorentzian_daq_fft['f0_unc_Hz']:.1f} Hz, "
                  f"FWHM={lorentzian_daq_fft['fwhm_Hz']:.1f} ± "
                  f"{lorentzian_daq_fft['fwhm_unc_Hz']:.1f} Hz "
                  f"(residual std={lorentzian_daq_fft['residual_std']:.3e})")
        else:
            print(f"[Lorentzian] direct FFT 拟合失败: {lorentzian_daq_fft['message']}")

    # ---- (b) Offline lock-in ----
    if len(freqs_li) > 0 and np.any(li_valid):
        # 取 valid 的子集
        li_valid_mask = li_valid & np.isfinite(li_r)
        # li 暂无 is_suspicious 字段 (已通过 compute_bandwidth 的 valid mask 跳过整数周期不足)
        lorentzian_offline_lockin = fit_lorentzian(
            freqs_li, li_r,
            is_suspicious=~li_valid_mask,
            exclude_suspicious=True,
            p0_hwhm=(bandwidth_offline_lockin.get("bandwidth_Hz") or 0) / 2.0
                     if bandwidth_offline_lockin.get("bandwidth_Hz") else None,
            min_points=LORENTZIAN_FIT_MIN_POINTS,
            hwhm_upper=LORENTZIAN_FIT_MAX_HWHM_HZ,
        )
        lorentzian_offline_lockin["enabled"] = bool(
            lorentzian_offline_lockin.get("success"))
        if lorentzian_offline_lockin["enabled"]:
            print(f"[Lorentzian] offline LI:  A={lorentzian_offline_lockin['A']:.3e}, "
                  f"f0={lorentzian_offline_lockin['f0_Hz']:.1f} ± "
                  f"{lorentzian_offline_lockin['f0_unc_Hz']:.1f} Hz, "
                  f"FWHM={lorentzian_offline_lockin['fwhm_Hz']:.1f} ± "
                  f"{lorentzian_offline_lockin['fwhm_unc_Hz']:.1f} Hz "
                  f"(residual std={lorentzian_offline_lockin['residual_std']:.3e})")
        else:
            print(f"[Lorentzian] offline LI 拟合失败: {lorentzian_offline_lockin['message']}")

    # ---- (c) Hardware demod_r (vector mean) ----
    if acquisition_mode in ("demod_r", "both") and len(freqs_dR) > 0:
        lorentzian_hardware_demod_r = fit_lorentzian(
            freqs_dR, R_vec,
            is_suspicious=None,
            exclude_suspicious=False,
            p0_hwhm=(bandwidth_hardware_demod_r_vector.get("bandwidth_Hz") or 0) / 2.0
                     if bandwidth_hardware_demod_r_vector.get("bandwidth_Hz") else None,
            min_points=LORENTZIAN_FIT_MIN_POINTS,
            hwhm_upper=LORENTZIAN_FIT_MAX_HWHM_HZ,
        )
        lorentzian_hardware_demod_r["enabled"] = bool(
            lorentzian_hardware_demod_r.get("success"))
        if lorentzian_hardware_demod_r["enabled"]:
            print(f"[Lorentzian] HW demod_r:  A={lorentzian_hardware_demod_r['A']:.3e}, "
                  f"f0={lorentzian_hardware_demod_r['f0_Hz']:.1f} ± "
                  f"{lorentzian_hardware_demod_r['f0_unc_Hz']:.1f} Hz, "
                  f"FWHM={lorentzian_hardware_demod_r['fwhm_Hz']:.1f} ± "
                  f"{lorentzian_hardware_demod_r['fwhm_unc_Hz']:.1f} Hz "
                  f"(residual std={lorentzian_hardware_demod_r['residual_std']:.3e})")
        else:
            print(f"[Lorentzian] HW demod_r 拟合失败: {lorentzian_hardware_demod_r['message']}")

# ---- 0b. 双 Lorentzian 拟合 (dispersive + absorptive, Bloch 稳态响应) ----
doublelorentzian_daq_fft = {"enabled": False}
doublelorentzian_offline_lockin = {"enabled": False}
doublelorentzian_hardware_demod_r = {"enabled": False}
USE_DOUBLE_LORENTZIAN = (FIT_MODEL == "double_lorentzian")

if ENABLE_LORENTZIAN_FIT and USE_DOUBLE_LORENTZIAN:
    print(f"\n[DoubleLorentzian] 模型: C + P0·√(Γ²+f²)/"
          f"√((Γ²-f²+Ω²)²+4Γ²f²)   (LARMOR_FREQ_HZ={LARMOR_FREQ_HZ})")

    def _run_double_lorentzian(label, freq_arr, resp_arr, susp_mask,
                                  peak_hint_Hz, label_tag):
        """Run fit_doublelorentzian and print summary + compare with LARMOR."""
        peak_hint = (bandwidth_daq_fft.get("peak_freq_Hz") if "FFT" in label_tag
                     else bandwidth_offline_lockin.get("peak_freq_Hz")
                     if "lock" in label_tag else
                     bandwidth_hardware_demod_r_vector.get("peak_freq_Hz"))
        if peak_hint is None:
            peak_hint = peak_hint_Hz
        p0_omega = DLZ_OMEGA_INIT_HZ if DLZ_OMEGA_INIT_HZ is not None else peak_hint
        result = fit_doublelorentzian(
            freq_arr, resp_arr,
            is_suspicious=susp_mask,
            exclude_suspicious=(EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH
                                and not DLZ_USE_SUSPICIOUS),
            p0_P0=None,
            p0_Gamma=None,
            p0_Omega=p0_omega,
            p0_offset=DLZ_OFFSET_INIT_V,
            Gamma_min=DLZ_GAMMA_MIN_HZ,
            Gamma_max=DLZ_GAMMA_MAX_HZ,
            Omega_min=DLZ_OMEGA_MIN_HZ,
            Omega_max=DLZ_OMEGA_MAX_HZ,
            include_offset=DLZ_INCLUDE_OFFSET,
            min_points=DLZ_MIN_POINTS,
            LARMOR_FREQ_HZ=LARMOR_FREQ_HZ,
        )
        result["enabled"] = bool(result.get("success"))
        if result["success"]:
            print(f"[DoubleLorentzian] {label}: Γ={result['Gamma_Hz']:.2f} ± "
                  f"{result['Gamma_unc_Hz']:.2f} Hz, "
                  f"Ω_Ctrl={result['Omega_Ctrl_Hz']:.1f} ± "
                  f"{result['Omega_unc_Hz']:.1f} Hz, "
                  f"offset=({result['offset_V']:.2e} ± "
                  f"{result['offset_unc_V']:.1e}) V, "
                  f"P0={result['P0']:.3e} "
                  f"(residual std={result['residual_std']:.3e}, "
                  f"n={result['n_points']})")
            if LARMOR_FREQ_HZ is not None:
                omega_dev = result["Omega_Ctrl_Hz"] - LARMOR_FREQ_HZ
                print(f"  ↳ Ω_Ctrl - LARMOR_FREQ_HZ = {omega_dev:+.2f} Hz "
                      f"(rel = {omega_dev/LARMOR_FREQ_HZ*100:.3f}%)")
        else:
            print(f"[DoubleLorentzian] {label} 拟合失败: {result['message']}")
        return result

    if acquisition_mode in ("daq_fft_y", "both") and len(freqs_daq) > 0:
        doublelorentzian_daq_fft = _run_double_lorentzian(
            "direct FFT", freqs_daq, amps_corr,
            is_susp_daq, peak_hint_Hz=LARMOR_FREQ_HZ,
            label_tag="FFT",
        )

    if len(freqs_li) > 0 and np.any(li_valid):
        li_valid_mask = li_valid & np.isfinite(li_r)
        doublelorentzian_offline_lockin = _run_double_lorentzian(
            "offline LI", freqs_li, li_r,
            ~li_valid_mask, peak_hint_Hz=LARMOR_FREQ_HZ,
            label_tag="lockin",
        )

    if acquisition_mode in ("demod_r", "both") and len(freqs_dR) > 0:
        doublelorentzian_hardware_demod_r = _run_double_lorentzian(
            "HW demod_r", freqs_dR, R_vec,
            None, peak_hint_Hz=LARMOR_FREQ_HZ,
            label_tag="HW",
        )

# ---- 1. 三组响应都 valid 的交集上算比值 ----
def _interp_at(x_src, y_src, x_query):
    """对 x_query 在 (x_src, y_src) 上做线性插值，要求 x_src 单调."""
    return np.interp(x_query, x_src, y_src, left=np.nan, right=np.nan)


ratio_fft_li = np.array([])        # direct FFT / offline lock-in
ratio_hw_li = np.array([])         # hardware demod_r / offline lock-in

# 取三组共有的有效频点
common_freqs = None
if (acquisition_mode in ("daq_fft_y", "both") and len(freqs_daq) > 0
        and len(freqs_li) > 0):
    valid_daq = np.isfinite(amps_corr) & (~is_susp_daq if is_susp_daq is not None
                                            else np.ones_like(freqs_daq, bool))
    common = sorted(set(freqs_daq[valid_daq].tolist())
                    & set(freqs_li[li_valid].tolist()))
    common_freqs = np.array(common)
    if len(common) > 0:
        ratio_fft_li = (_interp_at(freqs_daq, amps_corr, common_freqs)
                        / _interp_at(freqs_li, li_r, common_freqs))

if (acquisition_mode in ("demod_r", "both") and len(freqs_dR) > 0
        and len(freqs_li) > 0):
    common = sorted(set(freqs_dR.tolist())
                    & set(freqs_li[li_valid].tolist()))
    common_hw = np.array(common)
    if len(common_hw) > 0:
        ratio_hw_li = (_interp_at(freqs_dR, R_vec, common_hw)
                       / _interp_at(freqs_li, li_r, common_hw))
else:
    common_hw = np.array([])


def _ratio_stats(arr, label):
    arr_valid = arr[np.isfinite(arr) & (arr > 0)]
    if len(arr_valid) == 0:
        return None
    return {
        "label": label,
        "n": int(len(arr_valid)),
        "median": float(np.median(arr_valid)),
        "min": float(np.min(arr_valid)),
        "max": float(np.max(arr_valid)),
    }


stat_fft_li = _ratio_stats(ratio_fft_li, "FFT/LI")
stat_hw_li = _ratio_stats(ratio_hw_li, "HW/LI")

print("=" * 60)
print(f"带宽分析（-3 dB 准则，daq exclude_suspicious={EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH}）")
print("=" * 60)
if acquisition_mode in ("daq_fft_y", "both"):
    bw = bandwidth_daq_fft
    if bw["bandwidth_Hz"] is not None:
        print(f"[direct FFT]      peak={bw['peak_value']:.4e} V "
              f"@ {bw['peak_freq_Hz']:.1f} Hz "
              f"(exclude {bw['n_excluded']} suspicious)")
        print(f"  f_low={bw['f_low_Hz']:.1f} Hz, "
              f"f_high={bw['f_high_Hz']:.1f} Hz, "
              f"BW={bw['bandwidth_Hz']:.1f} Hz, "
              f"Q={bw['q_factor']:.2f}, "
              f"censored={bw['bandwidth_is_censored']}")
if len(freqs_li) > 0:
    bw = bandwidth_offline_lockin
    if bw["bandwidth_Hz"] is not None:
        print(f"[offline lock-in] peak={bw['peak_value']:.4e} V "
              f"@ {bw['peak_freq_Hz']:.1f} Hz "
              f"(exclude {bw['n_excluded']} invalid)")
        print(f"  f_low={bw['f_low_Hz']:.1f} Hz, "
              f"f_high={bw['f_high_Hz']:.1f} Hz, "
              f"BW={bw['bandwidth_Hz']:.1f} Hz, "
              f"Q={bw['q_factor']:.2f}, "
              f"censored={bw['bandwidth_is_censored']}")
if acquisition_mode in ("demod_r", "both"):
    bw = bandwidth_hardware_demod_r_vector
    if bw["bandwidth_Hz"] is not None:
        print(f"[hardware vector]  peak={bw['peak_value']:.4e} V "
              f"@ {bw['peak_freq_Hz']:.1f} Hz")
        print(f"  f_low={bw['f_low_Hz']:.1f} Hz, "
              f"f_high={bw['f_high_Hz']:.1f} Hz, "
              f"BW={bw['bandwidth_Hz']:.1f} Hz, "
              f"Q={bw['q_factor']:.2f}, "
              f"censored={bw['bandwidth_is_censored']}")
    bw = bandwidth_hardware_demod_r_scalar
    if bw["bandwidth_Hz"] is not None:
        print(f"[hardware scalar]  peak={bw['peak_value']:.4e} V "
              f"@ {bw['peak_freq_Hz']:.1f} Hz")
        print(f"  f_low={bw['f_low_Hz']:.1f} Hz, "
              f"f_high={bw['f_high_Hz']:.1f} Hz, "
              f"BW={bw['bandwidth_Hz']:.1f} Hz, "
              f"Q={bw['q_factor']:.2f}, "
              f"censored={bw['bandwidth_is_censored']}")

print("-" * 60)
if stat_fft_li is not None:
    print(f"direct FFT / offline lock-in: median={stat_fft_li['median']:.4f}, "
          f"[{stat_fft_li['min']:.4f}, {stat_fft_li['max']:.4f}] "
          f"(n={stat_fft_li['n']})")
else:
    print("direct FFT / offline lock-in: <no overlap>")
if stat_hw_li is not None:
    print(f"hardware demod_r / offline lock-in: median={stat_hw_li['median']:.4f}, "
          f"[{stat_hw_li['min']:.4f}, {stat_hw_li['max']:.4f}] "
          f"(n={stat_hw_li['n']})")
else:
    print("hardware demod_r / offline lock-in: <no overlap>")

# %% Cell 5
# ============================================================
# 绘图
# ============================================================
plt.rcParams.update({"figure.dpi": 120, "font.size": 11, "axes.labelsize": 12})

Z_RF_AMPLITUDE = (config.get("freq_sweep", {})
                  .get("Z_RF_AMPLITUDE_Vpp", 0.01))

# ---- 图 1: daq_fft_y 幅频特性 ----
if acquisition_mode in ("daq_fft_y", "both") and len(freqs_daq) > 0:
    fig1, ax1 = plt.subplots(figsize=(10, 5.5))

    # suspicious 点单独标记
    susp_mask = is_susp_daq
    if susp_mask is not None and np.any(susp_mask):
        nonsusp = ~susp_mask
    else:
        nonsusp = np.ones_like(freqs_daq, dtype=bool)

    ax1.semilogy(freqs_daq[nonsusp], amps_raw[nonsusp], "o-",
                 color="C0", lw=1.0, ms=4,
                 label="Raw amplitude (drive bin)")
    if np.any(~nonsusp):
        ax1.semilogy(freqs_daq[~nonsusp], amps_raw[~nonsusp], "x",
                     color="C3", ms=8, mew=2,
                     label=f"Suspicious (n={int(np.sum(~nonsusp))})")
    ax1.semilogy(freqs_daq[nonsusp], amps_corr[nonsusp], "s-",
                 color="C1", lw=1.0, ms=4, alpha=0.85,
                 label="Background-corrected")
    if np.isfinite(baseline_amp):
        ax1.axhline(baseline_amp, color="gray", ls=":", lw=0.8,
                    label=f"Baseline median = {baseline_amp:.2e} V")

    if bandwidth_daq_corr["peak_freq_Hz"] is not None:
        ax1.axvline(bandwidth_daq_corr["peak_freq_Hz"], color="red",
                    ls="--", lw=0.8, alpha=0.6,
                    label=f"Peak: {bandwidth_daq_corr['peak_freq_Hz']:.1f} Hz")
    if bandwidth_daq_corr["f_low_Hz"] is not None:
        ax1.axvline(bandwidth_daq_corr["f_low_Hz"], color="C2",
                    ls=":", lw=0.8, alpha=0.7)
        ax1.axvline(bandwidth_daq_corr["f_high_Hz"], color="C2",
                    ls=":", lw=0.8, alpha=0.7,
                    label=f"-3dB: {bandwidth_daq_corr['f_low_Hz']:.1f}-"
                          f"{bandwidth_daq_corr['f_high_Hz']:.1f} Hz")
    if bandwidth_daq_corr["threshold"] is not None:
        ax1.axhline(bandwidth_daq_corr["threshold"], color="C2", ls=":",
                    lw=0.6, alpha=0.5,
                    label=f"-3dB threshold = {bandwidth_daq_corr['threshold']:.2e} V")

    ax1.set_xlabel("Z RF field drive frequency (Hz)")
    ax1.set_ylabel("Amplitude at drive bin (V)")
    title = (f"ConstXY RF Field Frequency Response — daq_fft_y "
             f"({DAQ_RESPONSE_METHOD}, Z_drive = {Z_RF_AMPLITUDE} Vpp")
    if LARMOR_FREQ_HZ is not None:
        title += f", Larmor = {LARMOR_FREQ_HZ} Hz"
    title += ")"
    ax1.set_title(title)
    ax1.legend(fontsize=8, loc="best")
    ax1.grid(True, alpha=0.3, which="both")

    plt.tight_layout()
    fig1.savefig(results_dir / "frequency_response.png",
                 dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'frequency_response.png'}")
    plt.show()
else:
    print("⚠ 跳过 daq_fft_y 单图 (无数据)")

# ---- 图 2: 三组响应归一化对比 (normalized) ----
have_daq = (acquisition_mode in ("daq_fft_y", "both")
            and len(freqs_daq) > 0)
have_dR = (acquisition_mode in ("demod_r", "both")
           and len(freqs_dR) > 0)
have_li = (len(freqs_li) > 0 and np.any(li_valid))
if have_daq or have_dR or have_li:
    def _safe_norm(a):
        m = np.nanmax(a) if np.any(np.isfinite(a)) else 0
        return a / m if m > 0 else a

    fig2, ax2 = plt.subplots(figsize=(10, 5.5))
    n_curves = 0
    if have_daq:
        nonsusp = (~is_susp_daq if is_susp_daq is not None
                   else np.ones_like(freqs_daq, bool))
        norm_daq = _safe_norm(amps_corr)
        ax2.plot(freqs_daq[nonsusp], norm_daq[nonsusp], "o-",
                 color="C0", lw=1.2, ms=4,
                 label="direct FFT (y_V → nearest bin)")
        if np.any(~nonsusp):
            ax2.plot(freqs_daq[~nonsusp], norm_daq[~nonsusp], "x",
                     color="C0", ms=8, mew=2, alpha=0.5,
                     label=f"FFT suspicious (n={int(np.sum(~nonsusp))})")
        n_curves += 1
    if have_li:
        valid_li = li_valid & np.isfinite(li_r)
        norm_li = _safe_norm(li_r)
        ax2.plot(freqs_li[valid_li], norm_li[valid_li], "^-",
                 color="C2", lw=1.2, ms=4,
                 label="offline lock-in (y_V → software demod)")
        if np.any(~valid_li):
            ax2.plot(freqs_li[~valid_li], norm_li[~valid_li], "x",
                     color="C2", ms=8, mew=2, alpha=0.5,
                     label=f"LI invalid (n={int(np.sum(~valid_li))})")
        n_curves += 1
    if have_dR:
        norm_dR = _safe_norm(R_vec)
        ax2.plot(freqs_dR, norm_dR, "s-", color="C3", lw=1.2, ms=4,
                 label="hardware demod_r (vector mean)")
        n_curves += 1
    if n_curves == 0:
        print("⚠ 跳过归一化对比图 (无可用响应)")

    # ---- 拟合曲线 (在归一化坐标上叠加): 优先双 Lorentzian, 回落到单 Lorentzian ----
    def _pick_fit(single, double):
        if double is not None and double.get("success"):
            return double, "double"
        if single is not None and single.get("success"):
            return single, "single"
        return None, None

    fit_pairs = [
        (have_daq, "C0",
         _pick_fit(lorentzian_daq_fft, doublelorentzian_daq_fft),
         amps_corr if have_daq else None, "FFT"),
        (have_li,  "C2",
         _pick_fit(lorentzian_offline_lockin, doublelorentzian_offline_lockin),
         li_r if have_li else None, "LI"),
        (have_dR,  "C3",
         _pick_fit(lorentzian_hardware_demod_r, doublelorentzian_hardware_demod_r),
         R_vec if have_dR else None, "HW"),
    ]
    for present, color, picked, resp, tag in fit_pairs:
        if not present:
            continue
        fit, kind = picked
        if fit is None:
            continue
        fgrid = fit["fit_curve_freqs"]
        fit_vals = fit["fit_curve_values"]
        # 归一化到与对应数据相同的 peak
        if resp is None or not np.any(np.isfinite(resp)):
            continue
        peak_data = float(np.nanmax(resp))
        peak_fit = float(np.nanmax(fit_vals))
        if peak_fit <= 0 or peak_data <= 0:
            continue
        norm_fit = fit_vals * (peak_data / peak_fit)  # 与数据曲线对齐 peak
        if kind == "double":
            lbl = (f"Double-Lorentzian fit ({tag}): "
                   f"Ω={fit['Omega_Ctrl_Hz']:.1f}±{fit['Omega_unc_Hz']:.1f} Hz, "
                   f"Γ={fit['Gamma_Hz']:.2f}±{fit['Gamma_unc_Hz']:.2f} Hz")
        else:
            lbl = (f"Single-Lorentzian fit ({tag}): "
                   f"f0={fit['f0_Hz']:.1f} Hz, "
                   f"FWHM={fit['fwhm_Hz']:.1f}±{fit['fwhm_unc_Hz']:.1f} Hz")
        ax2.plot(fgrid, _safe_norm(norm_fit), "--", color=color,
                 lw=1.0, alpha=0.7, label=lbl)

    # peak 标注
    for bw, color, tag in [
        (bandwidth_daq_fft, "C0", "FFT"),
        (bandwidth_offline_lockin, "C2", "LI"),
        (bandwidth_hardware_demod_r_vector, "C3", "HW vec"),
    ]:
        if bw["peak_freq_Hz"] is not None:
            ax2.axvline(bw["peak_freq_Hz"], color=color,
                        ls="--", lw=0.7, alpha=0.5,
                        label=f"Peak ({tag}): {bw['peak_freq_Hz']:.1f} Hz")

    ax2.set_xlabel("Z RF field drive frequency (Hz)")
    ax2.set_ylabel("Normalized response (a.u.)")
    title_main = ("ConstXY Frequency Response Comparison — "
                  "direct FFT vs offline lock-in vs hardware demod_r")
    if FIT_MODEL == "double_lorentzian":
        title_main += "\n(fits: dispersive+Lorentzian Bloch steady-state model)"
    ax2.set_title(title_main)
    ax2.legend(fontsize=7, loc="best")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    fig2.savefig(results_dir / "frequency_response_comparison.png",
                 dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'frequency_response_comparison.png'}")
    plt.show()
else:
    print("⚠ 跳过归一化对比图 (无数据)")

# ---- 图 2b: 三组响应绝对幅值 (log scale) ----
if have_daq or have_dR or have_li:
    fig2b, ax2b = plt.subplots(figsize=(10, 5.5))
    if have_daq:
        ax2b.semilogy(freqs_daq, amps_corr, "o-", color="C0",
                      lw=1.0, ms=4, label="direct FFT")
    if have_li:
        valid_li = li_valid & np.isfinite(li_r)
        ax2b.semilogy(freqs_li[valid_li], li_r[valid_li], "^-",
                      color="C2", lw=1.0, ms=4, label="offline lock-in")
    if have_dR:
        ax2b.semilogy(freqs_dR, R_vec, "s-", color="C3",
                      lw=1.0, ms=4, label="hardware demod_r (vector)")
    if np.isfinite(baseline_amp):
        ax2b.axhline(baseline_amp, color="gray", ls=":", lw=0.8,
                     label=f"Baseline median = {baseline_amp:.2e} V")
    ax2b.set_xlabel("Z RF field drive frequency (Hz)")
    ax2b.set_ylabel("Amplitude (V, log scale)")
    ax2b.set_title("ConstXY Frequency Response — Absolute Amplitude Comparison")
    ax2b.legend(fontsize=8, loc="best")
    ax2b.grid(True, alpha=0.3, which="both")
    plt.tight_layout()
    fig2b.savefig(results_dir / "frequency_response_absolute.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'frequency_response_absolute.png'}")
    plt.show()
else:
    print("⚠ 跳过绝对幅值对比图 (无数据)")

# ---- 图 2b-LT: Lorentzian 拟合 + 残差 (三组响应, 各一个 subplot) ----
# 优先使用双 Lorentzian 拟合 (物理模型), 否则回落到单 Lorentzian
def _pick_for_panel(single, double):
    if double is not None and double.get("success"):
        return double, "double"
    if single is not None and single.get("success"):
        return single, "single"
    return None, None


panels_lt = []
if have_daq:
    fit_pick, kind_pick = _pick_for_panel(lorentzian_daq_fft,
                                           doublelorentzian_daq_fft)
    if fit_pick is not None:
        panels_lt.append(("direct FFT", freqs_daq, amps_corr,
                           is_susp_daq, fit_pick, kind_pick, "C0"))
if have_li:
    fit_pick, kind_pick = _pick_for_panel(lorentzian_offline_lockin,
                                           doublelorentzian_offline_lockin)
    if fit_pick is not None:
        valid_li_mask = li_valid & np.isfinite(li_r)
        panels_lt.append(("offline lock-in", freqs_li, li_r,
                           ~valid_li_mask, fit_pick, kind_pick, "C2"))
if have_dR:
    fit_pick, kind_pick = _pick_for_panel(lorentzian_hardware_demod_r,
                                           doublelorentzian_hardware_demod_r)
    if fit_pick is not None:
        panels_lt.append(("hardware demod_r",
                           freqs_dR, R_vec, None,
                           fit_pick, kind_pick, "C3"))

if panels_lt:
    fig_lf, axes_lf = plt.subplots(
        len(panels_lt), 2, figsize=(11, 3.2 * len(panels_lt)),
        gridspec_kw={"width_ratios": [3, 1]}, squeeze=False)
    for ax_row, (tag, freqs, resp, susp, fit, kind, color) in zip(
            axes_lf, panels_lt):
        ax_fit, ax_res = ax_row
        # 用与 -3 dB 同样的 valid+suspicious 过滤
        valid = np.isfinite(freqs) & np.isfinite(resp)
        if susp is not None and len(susp) == len(freqs):
            valid = valid & ~np.asarray(susp, dtype=bool)
        f_v = np.asarray(freqs, dtype=float)[valid]
        r_v = np.asarray(resp, dtype=float)[valid]
        # 拟合曲线
        f_fit = fit["fit_curve_freqs"]
        r_fit = fit["fit_curve_values"]
        # 数据曲线
        ax_fit.plot(f_v, r_v, "o", color=color, ms=4, label=f"{tag} data")
        if kind == "double":
            ax_fit.plot(f_fit, r_fit, "-", color=color, lw=1.4,
                        label=(f"Double-Lorentzian fit:\n"
                               f"  Ω_Ctrl={fit['Omega_Ctrl_Hz']:.1f}"
                               f"±{fit['Omega_unc_Hz']:.1f} Hz\n"
                               f"  Γ={fit['Gamma_Hz']:.2f}"
                               f"±{fit['Gamma_unc_Hz']:.2f} Hz\n"
                               f"  C=({fit['offset_V']:.2e}"
                               f"±{fit['offset_unc_V']:.1e}) V"))
            ax_fit.axvline(fit["Omega_Ctrl_Hz"], color=color,
                           ls="--", lw=0.7, alpha=0.5,
                           label=f"Ω_Ctrl={fit['Omega_Ctrl_Hz']:.1f} Hz")
            if LARMOR_FREQ_HZ is not None:
                ax_fit.axvline(LARMOR_FREQ_HZ, color="magenta", ls=":",
                               lw=0.6, alpha=0.5,
                               label=f"LARMOR set={LARMOR_FREQ_HZ:.1f} Hz")
        else:
            ax_fit.plot(f_fit, r_fit, "-", color=color, lw=1.4,
                        label=(f"Lorentzian fit: "
                               f"f0={fit['f0_Hz']:.1f}±{fit['f0_unc_Hz']:.1f} Hz, "
                               f"FWHM={fit['fwhm_Hz']:.1f}±"
                               f"{fit['fwhm_unc_Hz']:.1f} Hz"))
            threshold = fit["A"] / np.sqrt(2.0)
            ax_fit.axhline(threshold, color="gray", ls=":", lw=0.6,
                           label=f"half max = {threshold:.3e}")
            ax_fit.axvline(fit["f0_Hz"], color=color,
                           ls="--", lw=0.7, alpha=0.5)
            ax_fit.axvline(fit["f0_Hz"] - fit["fwhm_Hz"] / 2,
                           color=color, ls=":", lw=0.6, alpha=0.4)
            ax_fit.axvline(fit["f0_Hz"] + fit["fwhm_Hz"] / 2,
                           color=color, ls=":", lw=0.6, alpha=0.4,
                           label=f"±FWHM/2 = {fit['fwhm_Hz']/2:.1f} Hz")
        ax_fit.set_ylabel("Amplitude (V)")
        ax_fit.set_title(f"{tag} — {kind}-Lorentzian fit "
                         f"(residual std={fit['residual_std']:.2e})")
        ax_fit.legend(fontsize=7, loc="best")
        ax_fit.grid(True, alpha=0.3)

        # 残差
        if kind == "double":
            num = np.sqrt(fit["Gamma_Hz"] ** 2 + f_v ** 2)
            d1 = fit["Gamma_Hz"] ** 2 - f_v ** 2 + fit["Omega_Ctrl_Hz"] ** 2
            d2 = 4.0 * fit["Gamma_Hz"] ** 2 * f_v ** 2
            den = np.sqrt(d1 * d1 + d2)
            r_model = fit["offset_V"] + fit["P0"] * num / np.maximum(den, 1e-12)
        else:
            r_model = (fit["A"] * fit["hwhm_Hz"] ** 2
                       / ((f_v - fit["f0_Hz"]) ** 2 + fit["hwhm_Hz"] ** 2))
        residual = r_v - r_model
        ax_res.axhline(0, color="black", lw=0.8)
        ax_res.axhline(fit["residual_std"], color="gray", ls=":", lw=0.5,
                       alpha=0.6)
        ax_res.axhline(-fit["residual_std"], color="gray", ls=":", lw=0.5,
                       alpha=0.6)
        ax_res.plot(f_v, residual, "o", color=color, ms=3)
        ax_res.set_ylabel("residual")
        ax_res.grid(True, alpha=0.3)
        ax_res.set_xlabel("Frequency (Hz)")

    plt.tight_layout()
    fig_lf.savefig(results_dir / "frequency_response_fits.png",
                   dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'frequency_response_fits.png'}")
    plt.show()
else:
    print("⚠ 跳过 Lorentzian 拟合图（无拟合成功的曲线）")

# ---- 图 2c: direct FFT / offline lock-in 比值 ----
if len(ratio_fft_li) > 0 and len(common_freqs) > 0:
    valid = np.isfinite(ratio_fft_li) & (ratio_fft_li > 0)
    fig2c, ax2c = plt.subplots(figsize=(10, 4.5))
    ax2c.axhline(1.0, color="black", lw=0.8)
    if stat_fft_li is not None:
        ax2c.axhline(stat_fft_li["median"], color="C0", ls="--", lw=0.8,
                     label=f"median = {stat_fft_li['median']:.4f}")
    ax2c.plot(common_freqs[valid], ratio_fft_li[valid], "o-",
              color="C0", lw=1.0, ms=4, label="direct FFT / offline lock-in")
    ax2c.set_xlabel("Z RF field drive frequency (Hz)")
    ax2c.set_ylabel("FFT amplitude / lock-in amplitude")
    ax2c.set_title("ConstXY: Direct FFT vs Offline Lock-in — Amplitude Ratio")
    ax2c.legend(fontsize=8, loc="best")
    ax2c.grid(True, alpha=0.3)
    plt.tight_layout()
    fig2c.savefig(results_dir / "fft_vs_offline_lockin_ratio.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'fft_vs_offline_lockin_ratio.png'}")
    plt.show()
else:
    print("⚠ 跳过 FFT / lock-in 比值图 (无交集)")

# ---- 图 2d: hardware demod_r / offline lock-in 比值 ----
if len(ratio_hw_li) > 0 and len(common_hw) > 0:
    valid = np.isfinite(ratio_hw_li) & (ratio_hw_li > 0)
    fig2d, ax2d = plt.subplots(figsize=(10, 4.5))
    ax2d.axhline(1.0, color="black", lw=0.8)
    if stat_hw_li is not None:
        ax2d.axhline(stat_hw_li["median"], color="C3", ls="--", lw=0.8,
                     label=f"median = {stat_hw_li['median']:.4f}")
    ax2d.plot(common_hw[valid], ratio_hw_li[valid], "s-",
              color="C3", lw=1.0, ms=4, label="hardware demod_r / offline lock-in")
    ax2d.set_xlabel("Z RF field drive frequency (Hz)")
    ax2d.set_ylabel("hardware demod_r / lock-in amplitude")
    ax2d.set_title("ConstXY: Hardware Demod R vs Offline Lock-in — Amplitude Ratio")
    ax2d.legend(fontsize=8, loc="best")
    ax2d.grid(True, alpha=0.3)
    plt.tight_layout()
    fig2d.savefig(results_dir / "hardware_vs_offline_lockin_ratio.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'hardware_vs_offline_lockin_ratio.png'}")
    plt.show()
else:
    print("⚠ 跳过 hardware / lock-in 比值图 (无交集)")

# ---- 图 2e: offline lock-in 相位 ----
if len(freqs_li) > 0 and np.any(li_valid):
    valid = li_valid & np.isfinite(li_phase_deg)
    fig2e, (ax2e1, ax2e2) = plt.subplots(2, 1, figsize=(10, 6.0),
                                           sharex=True)
    ax2e1.plot(freqs_li[valid], li_r[valid], "^-", color="C2",
               lw=1.0, ms=4, label="offline lock-in R")
    if np.isfinite(baseline_amp):
        ax2e1.axhline(baseline_amp, color="gray", ls=":", lw=0.7,
                      label=f"Baseline = {baseline_amp:.2e} V")
    ax2e1.set_ylabel("R (V)")
    ax2e1.set_title("ConstXY: Offline Lock-in — Magnitude & Phase vs Drive Frequency")
    ax2e1.legend(fontsize=8, loc="best")
    ax2e1.grid(True, alpha=0.3)
    ax2e1.set_yscale("log")

    ax2e2.plot(freqs_li[valid], li_phase_deg[valid], "^-", color="C2",
               lw=1.0, ms=4, label="offline lock-in phase")
    if np.any(~li_valid):
        ax2e2.plot(freqs_li[~li_valid], li_phase_deg[~li_valid], "x",
                   color="gray", ms=6, mew=1.5, alpha=0.5,
                   label=f"invalid (n={int(np.sum(~li_valid))})")
    ax2e2.set_xlabel("Z RF field drive frequency (Hz)")
    ax2e2.set_ylabel("Phase (deg)")
    ax2e2.legend(fontsize=8, loc="best")
    ax2e2.grid(True, alpha=0.3)

    plt.tight_layout()
    fig2e.savefig(results_dir / "offline_lockin_phase.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'offline_lockin_phase.png'}")
    plt.show()
else:
    print("⚠ 跳过 offline lock-in 相位图 (无数据)")

# ---- 图 2f: hardware vector vs scalar ----
if have_dR and len(R_vec) > 0 and len(R_scalar) > 0:
    fig2f, ax2f = plt.subplots(figsize=(10, 4.5))
    ax2f.semilogy(freqs_dR, R_vec, "s-", color="C3",
                  lw=1.2, ms=4, label="R_vec = sqrt(<X>² + <Y>²)")
    ax2f.semilogy(freqs_dR, R_scalar, "o-", color="C4",
                  lw=1.0, ms=4, alpha=0.7, label="R_scalar = <|Z|>")
    ax2f.set_xlabel("Z RF field drive frequency (Hz)")
    ax2f.set_ylabel("Hardware Demod R (V, log scale)")
    ax2f.set_title("ConstXY: Hardware Demod — Vector Mean vs Scalar Mean")
    ax2f.legend(fontsize=8, loc="best")
    ax2f.grid(True, alpha=0.3, which="both")
    plt.tight_layout()
    fig2f.savefig(results_dir / "hardware_vector_vs_scalar.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'hardware_vector_vs_scalar.png'}")
    plt.show()
else:
    print("⚠ 跳过 hardware vector vs scalar 图 (无数据)")

# ---- 图 2g: hardware scalar / vector ratio ----
if have_dR and len(R_vec) > 0 and len(R_scalar) > 0:
    ratio_sv = R_scalar / R_vec
    valid = (R_vec > 0) & np.isfinite(ratio_sv)
    fig2g, ax2g = plt.subplots(figsize=(10, 4.5))
    ax2g.axhline(1.0, color="black", lw=0.8)
    median_sv = float(np.median(ratio_sv[valid]))
    ax2g.axhline(median_sv, color="C3", ls="--", lw=0.8,
                 label=f"median = {median_sv:.4f}")
    ax2g.plot(freqs_dR[valid], ratio_sv[valid], "s-", color="C3",
              lw=1.0, ms=4, label="R_scalar / R_vec")
    ax2g.set_xlabel("Z RF field drive frequency (Hz)")
    ax2g.set_ylabel("R_scalar / R_vec")
    ax2g.set_title("ConstXY: Hardware Demod — Scalar / Vector Ratio "
                   "(positive bias of mean|R|)")
    ax2g.legend(fontsize=8, loc="best")
    ax2g.grid(True, alpha=0.3)
    plt.tight_layout()
    fig2g.savefig(results_dir / "hardware_scalar_over_vector_ratio.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'hardware_scalar_over_vector_ratio.png'}")
    plt.show()
else:
    print("⚠ 跳过 hardware scalar/vector 比值图 (无数据)")

# ---- 图 2h: hardware vector phase ----
if have_dR and len(phase_vec_deg) > 0:
    fig2h, ax2h = plt.subplots(figsize=(10, 4.5))
    ax2h.plot(freqs_dR, phase_vec_deg, "s-", color="C3",
              lw=1.0, ms=4, label="hardware vector phase")
    ax2h.set_xlabel("Z RF field drive frequency (Hz)")
    ax2h.set_ylabel("Phase (deg)")
    ax2h.set_title("ConstXY: Hardware Demod R — Vector Phase vs Drive Frequency")
    ax2h.legend(fontsize=8, loc="best")
    ax2h.grid(True, alpha=0.3)
    plt.tight_layout()
    fig2h.savefig(results_dir / "hardware_phase_vector.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'hardware_phase_vector.png'}")
    plt.show()
else:
    print("⚠ 跳过 hardware vector phase 图 (无数据)")

# ---- 图 3: 诊断图 — picked_freq 与 drive_freq 的偏差 ----
if (acquisition_mode in ("daq_fft_y", "both")
        and len(freqs_daq) > 0 and len(freq_errors) > 0):
    fig3, ax3 = plt.subplots(figsize=(10, 4.5))
    nonsusp = ~is_susp_daq if is_susp_daq is not None else np.ones_like(freqs_daq, dtype=bool)
    ax3.axhline(0, color="black", lw=0.6)
    ax3.axhline(MAX_PICK_FREQ_ERROR_HZ, color="C3", ls=":", lw=0.7,
                label=f"+{MAX_PICK_FREQ_ERROR_HZ:.0f} Hz threshold")
    ax3.axhline(-MAX_PICK_FREQ_ERROR_HZ, color="C3", ls=":", lw=0.7,
                label=f"-{MAX_PICK_FREQ_ERROR_HZ:.0f} Hz threshold")
    ax3.plot(freqs_daq[nonsusp], freq_errors[nonsusp], "o-",
             color="C0", lw=0.8, ms=3, label="non-suspicious")
    if np.any(~nonsusp):
        ax3.plot(freqs_daq[~nonsusp], freq_errors[~nonsusp], "x",
                 color="C3", ms=8, mew=2,
                 label=f"suspicious (n={int(np.sum(~nonsusp))})")
    ax3.set_xlabel("Z RF field drive frequency (Hz)")
    ax3.set_ylabel("picked_freq - drive_freq (Hz)")
    ax3.set_title("ConstXY: daq_fft_y — Picked Frequency Error Diagnostic")
    ax3.legend(fontsize=8, loc="best")
    ax3.grid(True, alpha=0.3)
    plt.tight_layout()
    fig3.savefig(results_dir / "daq_picked_frequency_error.png",
                 dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'daq_picked_frequency_error.png'}")
    plt.show()
else:
    print("⚠ 跳过 picked_freq 诊断图 (无数据)")

# ---- 图 4: 典型频点谱示意 ----
example_freqs = []
if len(signal_pts) >= 3:
    example_freqs = [
        signal_pts[0]["freq_Hz"],
        signal_pts[len(signal_pts) // 3]["freq_Hz"],
        signal_pts[2 * len(signal_pts) // 3]["freq_Hz"],
        signal_pts[-1]["freq_Hz"],
    ]
elif signal_pts:
    example_freqs = [p["freq_Hz"] for p in signal_pts]

if acquisition_mode in ("daq_fft_y", "both") and example_freqs:
    fig4, axes = plt.subplots(len(example_freqs), 1,
                              figsize=(10, 2.4 * len(example_freqs)),
                              sharex=False)
    if len(example_freqs) == 1:
        axes = [axes]

    # 建立 freq -> record_daq 的索引，方便拿到 picked_freq_Hz
    rec_by_freq = {r["freq_Hz"]: r for r in records_daq}

    for ax, ef in zip(axes, example_freqs):
        target = next((p for p in signal_pts
                       if abs(p["freq_Hz"] - ef) < 1e-3), None)
        if target is None or "y_V" not in target["data"].files:
            ax.text(0.5, 0.5, f"No data @ {ef:.1f} Hz",
                    ha="center", va="center", transform=ax.transAxes)
            continue
        y = target["data"]["y_V"]
        fs = float(target["data"]["actual_rate_Sa_s"])
        freqs_s, amp_s, df_s = _windowed_fft_full(y, fs)
        f_show_max = max(2 * ef, 200.0)
        m_show = freqs_s <= f_show_max
        ax.semilogy(freqs_s[m_show], amp_s[m_show], color="C0", lw=0.8,
                    label="FFT amplitude")
        ax.axvline(ef, color="red", ls="--", lw=0.8,
                   label=f"drive = {ef:.1f} Hz")
        # 标出实际 picked_freq
        rec = rec_by_freq.get(ef)
        if rec is not None:
            pf = rec["picked_freq_Hz"]
            if np.isfinite(pf) and pf != ef:
                ax.axvline(pf, color="C3", ls="-", lw=1.0,
                           label=f"picked = {pf:.1f} Hz")
        ax.set_ylabel("Amp (V)")
        ax.set_title(f"ConstXY Spectrum @ drive = {ef:.1f} Hz "
                     f"(df={df_s:.2f} Hz)")
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(True, alpha=0.3, which="both")
    axes[-1].set_xlabel("Frequency (Hz)")

    plt.tight_layout()
    fig4.savefig(results_dir / "response_spectrum_examples.png",
                 dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'response_spectrum_examples.png'}")
    plt.show()
else:
    print("⚠ 跳过典型频点谱图 (无 daq_fft_y 数据)")

# %% Cell 6
# ============================================================
# 保存分析结果
# ============================================================
# ---- suspicious 点统计 ----
susp_freqs = []
if is_susp_daq is not None and len(is_susp_daq) > 0:
    susp_idx = np.where(is_susp_daq)[0]
    susp_freqs = freqs_daq[susp_idx].tolist()

# ---- 频点级结果（按用户要求的字段命名） ----
freq_response = {
    "freq_Hz": [],
    "fft_amp_V": [],
    "offline_lockin_r_V": [],
    "offline_lockin_x_V": [],
    "offline_lockin_y_V": [],
    "offline_lockin_phase_deg": [],
    "hardware_demod_r_vector_V": [],
    "hardware_demod_r_scalar_mean_V": [],
    "hardware_demod_r_x_mean_V": [],
    "hardware_demod_r_y_mean_V": [],
    "hardware_demod_r_phase_vector_deg": [],
    "fft_vs_offline_lockin_ratio": [],
    "hardware_vector_vs_offline_lockin_ratio": [],
    "hardware_scalar_over_vector_ratio": [],
    # 兼容旧字段名
    "amp_raw_V": [],
    "amp_corrected_V": [],
    "picked_freq_Hz": [],
    "freq_error_Hz": [],
    "is_suspicious": [],
    "offline_lockin_phase_rad": [],
    "offline_lockin_n_cycles_used": [],
    "offline_lockin_n_samples_used": [],
    "offline_lockin_is_valid": [],
    "hardware_demod_r_vs_offline_lockin_ratio": [],  # 旧别名 → vector
}

# 离线锁相按 freq 索引（方便后面对齐）
li_by_freq = {r["freq_Hz"]: r for r in records_offline_lockin}
hw_by_freq = {r["freq_Hz"]: r for r in records_demod_r}

if acquisition_mode in ("daq_fft_y", "both"):
    for r in records_daq:
        f = r["freq_Hz"]
        freq_response["freq_Hz"].append(f)
        fft_v = r["amp_corrected_V"]
        freq_response["fft_amp_V"].append(fft_v)
        # 兼容旧字段
        freq_response["amp_raw_V"].append(r["amp_raw_V"])
        freq_response["amp_corrected_V"].append(r["amp_corrected_V"])
        freq_response["picked_freq_Hz"].append(r["picked_freq_Hz"])
        freq_response["freq_error_Hz"].append(r["freq_error_Hz"])
        freq_response["is_suspicious"].append(bool(r["is_suspicious"]))

        # 离线锁相对齐
        li = li_by_freq.get(f)
        if li is not None:
            li_r_v = li["offline_lockin_r_V"]
            freq_response["offline_lockin_x_V"].append(li["offline_lockin_x_V"])
            freq_response["offline_lockin_y_V"].append(li["offline_lockin_y_V"])
            freq_response["offline_lockin_r_V"].append(li_r_v)
            freq_response["offline_lockin_phase_deg"].append(li["offline_lockin_phase_deg"])
            freq_response["offline_lockin_phase_rad"].append(li["offline_lockin_phase_rad"])
            freq_response["offline_lockin_n_cycles_used"].append(li["n_cycles_used"])
            freq_response["offline_lockin_n_samples_used"].append(li["n_samples_used"])
            freq_response["offline_lockin_is_valid"].append(bool(li["is_valid"]))
            # ratio: FFT / lock-in
            if (np.isfinite(fft_v) and np.isfinite(li_r_v)
                    and li_r_v > 0 and not r["is_suspicious"]):
                fft_li = float(fft_v / li_r_v)
                freq_response["fft_vs_offline_lockin_ratio"].append(fft_li)
            else:
                fft_li = np.nan
                freq_response["fft_vs_offline_lockin_ratio"].append(np.nan)
        else:
            li_r_v = np.nan
            fft_li = np.nan
            for k in ("offline_lockin_x_V", "offline_lockin_y_V",
                       "offline_lockin_r_V", "offline_lockin_phase_deg",
                       "offline_lockin_phase_rad",
                       "offline_lockin_n_cycles_used",
                       "offline_lockin_n_samples_used",
                       "offline_lockin_is_valid",
                       "fft_vs_offline_lockin_ratio"):
                if k == "offline_lockin_n_cycles_used" or k == "offline_lockin_n_samples_used":
                    freq_response[k].append(0)
                elif k == "offline_lockin_is_valid":
                    freq_response[k].append(False)
                else:
                    freq_response[k].append(np.nan)

        # hardware demod_r 比值（vector 是主响应，scalar 仅诊断）
        hw = hw_by_freq.get(f)
        if hw is not None:
            hw_vec = hw["R_vector_V"]
            hw_scalar = hw["R_scalar_mean_V"]
            hw_x = hw["X_mean_V"]
            hw_y = hw["Y_mean_V"]
            hw_phase = hw["phase_vector_deg"]
            freq_response["hardware_demod_r_vector_V"].append(hw_vec)
            freq_response["hardware_demod_r_scalar_mean_V"].append(hw_scalar)
            freq_response["hardware_demod_r_x_mean_V"].append(hw_x)
            freq_response["hardware_demod_r_y_mean_V"].append(hw_y)
            freq_response["hardware_demod_r_phase_vector_deg"].append(hw_phase)
            if (np.isfinite(hw_vec) and np.isfinite(li_r_v)
                    and li_r_v > 0 and not r["is_suspicious"]):
                hw_li = float(hw_vec / li_r_v)
                freq_response["hardware_vector_vs_offline_lockin_ratio"].append(hw_li)
                freq_response["hardware_demod_r_vs_offline_lockin_ratio"].append(hw_li)
            else:
                freq_response["hardware_vector_vs_offline_lockin_ratio"].append(np.nan)
                freq_response["hardware_demod_r_vs_offline_lockin_ratio"].append(np.nan)
            if np.isfinite(hw_scalar) and np.isfinite(hw_vec) and hw_vec > 0:
                freq_response["hardware_scalar_over_vector_ratio"].append(
                    float(hw_scalar / hw_vec))
            else:
                freq_response["hardware_scalar_over_vector_ratio"].append(np.nan)
        else:
            for k in ("hardware_demod_r_vector_V",
                       "hardware_demod_r_scalar_mean_V",
                       "hardware_demod_r_x_mean_V",
                       "hardware_demod_r_y_mean_V",
                       "hardware_demod_r_phase_vector_deg",
                       "hardware_vector_vs_offline_lockin_ratio",
                       "hardware_demod_r_vs_offline_lockin_ratio",
                       "hardware_scalar_over_vector_ratio"):
                freq_response[k].append(np.nan)

# 若没有 daq_fft_y 数据（demod_r only），也要把 offline_lockin 单独存下来
if (acquisition_mode == "demod_r"
        and not (acquisition_mode in ("daq_fft_y", "both"))):
    for r in records_offline_lockin:
        f = r["freq_Hz"]
        freq_response["freq_Hz"].append(f)
        freq_response["fft_amp_V"].append(np.nan)
        freq_response["amp_raw_V"].append(np.nan)
        freq_response["amp_corrected_V"].append(np.nan)
        freq_response["picked_freq_Hz"].append(np.nan)
        freq_response["freq_error_Hz"].append(np.nan)
        freq_response["is_suspicious"].append(False)
        freq_response["offline_lockin_x_V"].append(r["offline_lockin_x_V"])
        freq_response["offline_lockin_y_V"].append(r["offline_lockin_y_V"])
        freq_response["offline_lockin_r_V"].append(r["offline_lockin_r_V"])
        freq_response["offline_lockin_phase_deg"].append(r["offline_lockin_phase_deg"])
        freq_response["offline_lockin_phase_rad"].append(r["offline_lockin_phase_rad"])
        freq_response["offline_lockin_n_cycles_used"].append(r["n_cycles_used"])
        freq_response["offline_lockin_n_samples_used"].append(r["n_samples_used"])
        freq_response["offline_lockin_is_valid"].append(bool(r["is_valid"]))
        freq_response["fft_vs_offline_lockin_ratio"].append(np.nan)
        hw = hw_by_freq.get(f)
        if hw is not None:
            hw_vec = hw["R_vector_V"]
            hw_scalar = hw["R_scalar_mean_V"]
            li_v = r["offline_lockin_r_V"]
            freq_response["hardware_demod_r_vector_V"].append(hw_vec)
            freq_response["hardware_demod_r_scalar_mean_V"].append(hw_scalar)
            freq_response["hardware_demod_r_x_mean_V"].append(hw["X_mean_V"])
            freq_response["hardware_demod_r_y_mean_V"].append(hw["Y_mean_V"])
            freq_response["hardware_demod_r_phase_vector_deg"].append(hw["phase_vector_deg"])
            if np.isfinite(hw_vec) and np.isfinite(li_v) and li_v > 0:
                hw_li = float(hw_vec / li_v)
                freq_response["hardware_vector_vs_offline_lockin_ratio"].append(hw_li)
                freq_response["hardware_demod_r_vs_offline_lockin_ratio"].append(hw_li)
            else:
                freq_response["hardware_vector_vs_offline_lockin_ratio"].append(np.nan)
                freq_response["hardware_demod_r_vs_offline_lockin_ratio"].append(np.nan)
            if np.isfinite(hw_scalar) and np.isfinite(hw_vec) and hw_vec > 0:
                freq_response["hardware_scalar_over_vector_ratio"].append(
                    float(hw_scalar / hw_vec))
            else:
                freq_response["hardware_scalar_over_vector_ratio"].append(np.nan)
        else:
            for k in ("hardware_demod_r_vector_V",
                       "hardware_demod_r_scalar_mean_V",
                       "hardware_demod_r_x_mean_V",
                       "hardware_demod_r_y_mean_V",
                       "hardware_demod_r_phase_vector_deg",
                       "hardware_vector_vs_offline_lockin_ratio",
                       "hardware_demod_r_vs_offline_lockin_ratio",
                       "hardware_scalar_over_vector_ratio"):
                freq_response[k].append(np.nan)

demod_r_response = {
    "freq_Hz": [],
    "R_vector_V": [],
    "R_scalar_mean_V": [],
    "R_scalar_std_V": [],
    "X_mean_V": [],
    "Y_mean_V": [],
    "phase_vector_deg": [],
    "phase_vector_rad": [],
}
if acquisition_mode in ("demod_r", "both"):
    for r in records_demod_r:
        demod_r_response["freq_Hz"].append(r["freq_Hz"])
        demod_r_response["R_vector_V"].append(r["R_vector_V"])
        demod_r_response["R_scalar_mean_V"].append(r["R_scalar_mean_V"])
        demod_r_response["R_scalar_std_V"].append(r["R_scalar_std_V"])
        demod_r_response["X_mean_V"].append(r["X_mean_V"])
        demod_r_response["Y_mean_V"].append(r["Y_mean_V"])
        demod_r_response["phase_vector_deg"].append(r["phase_vector_deg"])
        demod_r_response["phase_vector_rad"].append(r["phase_vector_rad"])

analysis = {
    "experiment_type": EXPERIMENT_TYPE,
    "data_dir": str(DATA_DIR),
    "acquisition_mode": acquisition_mode,
    "n_points_total": int(len(point_data)),
    "n_baseline_points": int(len(baseline_pts)),
    "n_signal_points": int(len(signal_pts)),
    "freq_range_Hz": ([float(min(p["freq_Hz"] for p in signal_pts)),
                       float(max(p["freq_Hz"] for p in signal_pts))]
                      if signal_pts else None),
    "xy_const_field": xy_const,
    "Z_DRIVE_AMPLITUDE_Vpp": Z_RF_AMPLITUDE,
    "baseline_handling": (
        "0 Hz: Z 场 DC 0V + output OFF，作为基线点；"
        "不参与带宽/峰值计算"
    ),
    "daq_response_method": DAQ_RESPONSE_METHOD,
    "local_peak_rel_window": LOCAL_PEAK_REL_WINDOW,
    "max_pick_freq_error_Hz": MAX_PICK_FREQ_ERROR_HZ,
    "exclude_suspicious_for_bandwidth": EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH,
    "offline_lockin_use_integer_cycles": OFFLINE_LOCKIN_USE_INTEGER_CYCLES,
    "n_suspicious_points": int(len(susp_freqs)),
    "suspicious_freqs_Hz": susp_freqs,
    "n_offline_lockin_invalid": int(np.sum(~li_valid)) if len(li_valid) > 0 else 0,
    "bandwidth_daq_fft": bandwidth_daq_fft,
    "bandwidth_daq_raw": bandwidth_daq_raw,
    "bandwidth_offline_lockin": bandwidth_offline_lockin,
    "bandwidth_hardware_demod_r_vector": bandwidth_hardware_demod_r_vector,
    "bandwidth_hardware_demod_r_scalar": bandwidth_hardware_demod_r_scalar,
    "bandwidth_hardware_demod_r": bandwidth_hardware_demod_r_vector,  # 向后兼容别名
    "bandwidth_daq_corrected": bandwidth_daq_corr,   # 向后兼容别名
    "bandwidth_demod_r": bandwidth_hardware_demod_r_vector,  # 向后兼容别名
    "baseline_background_amp_V": (float(baseline_amp)
                                  if np.isfinite(baseline_amp) else None),
    "ratio_stats": {
        "fft_vs_offline_lockin": stat_fft_li,
        "hardware_demod_r_vs_offline_lockin": stat_hw_li,
    },
    # ---- 拟合 (亚 bin 精度线宽) ----
    "enable_lorentzian_fit": ENABLE_LORENTZIAN_FIT,
    "fit_model": FIT_MODEL,
    "lorentzian_fit_model_single": "A * hwhm^2 / ((f - f0)^2 + hwhm^2)",
    "lorentzian_fit_model_double": (
        "C + P0 * sqrt(Gamma^2 + f^2) / "
        "sqrt((Gamma^2 - f^2 + Omega_Ctrl^2)^2 + 4 * Gamma^2 * f^2)"),
    "LARMOR_FREQ_Hz_configured": LARMOR_FREQ_HZ,
    "LARMOR_FREQ_Hz_input_vs_fit": {
        "expected": LARMOR_FREQ_HZ,
        "FFT_Omega_Ctrl_Hz": (doublelorentzian_daq_fft.get("Omega_Ctrl_Hz")
                                if doublelorentzian_daq_fft.get("success") else None),
        "LI_Omega_Ctrl_Hz": (doublelorentzian_offline_lockin.get("Omega_Ctrl_Hz")
                              if doublelorentzian_offline_lockin.get("success") else None),
        "HW_Omega_Ctrl_Hz": (doublelorentzian_hardware_demod_r.get("Omega_Ctrl_Hz")
                              if doublelorentzian_hardware_demod_r.get("success") else None),
    },
    "lorentzian_daq_fft": _to_jsonable_friendly(lorentzian_daq_fft),
    "lorentzian_offline_lockin": _to_jsonable_friendly(lorentzian_offline_lockin),
    "lorentzian_hardware_demod_r": _to_jsonable_friendly(lorentzian_hardware_demod_r),
    "doublelorentzian_daq_fft": _to_jsonable_friendly(doublelorentzian_daq_fft),
    "doublelorentzian_offline_lockin": _to_jsonable_friendly(doublelorentzian_offline_lockin),
    "doublelorentzian_hardware_demod_r": _to_jsonable_friendly(doublelorentzian_hardware_demod_r),
    "freq_response": freq_response,
    "freq_response_demod_r": demod_r_response,
}

# ---- 关键结果摘要 ----
print("=" * 60)
print("分析摘要")
print("=" * 60)
print(f"实验类型:    {EXPERIMENT_TYPE}")
print(f"数据目录:    {DATA_DIR}")
print(f"采集模式:    {acquisition_mode}")
print(f"取值方法:    {DAQ_RESPONSE_METHOD}")
print(f"频点总数:    {len(point_data)} (基线 {len(baseline_pts)}, "
      f"信号 {len(signal_pts)})")
print(f"Z 驱动幅度:  {Z_RF_AMPLITUDE} Vpp")
if LARMOR_FREQ_HZ is not None:
    print(f"XY Larmor:   {LARMOR_FREQ_HZ} Hz (XY_DC_VOLTAGE = {XY_DC_VOLTAGE_V:.4f} V)")
if signal_pts:
    print(f"频率范围:    {min(p['freq_Hz'] for p in signal_pts):.1f} ~ "
      f"{max(p['freq_Hz'] for p in signal_pts):.1f} Hz")
print(f"suspicious 点: {len(susp_freqs)}")
if susp_freqs:
    print(f"  频率列表:    {susp_freqs[:10]}"
          f"{'...' if len(susp_freqs) > 10 else ''}")
print("-" * 60)
if acquisition_mode in ("daq_fft_y", "both"):
    bw = bandwidth_daq_fft
    if bw["bandwidth_Hz"] is not None:
        censored_tag = " (censored)" if bw["bandwidth_is_censored"] else ""
        print(f"[direct FFT]      -3dB 带宽 = {bw['bandwidth_Hz']:.1f} Hz "
              f"({bw['f_low_Hz']:.1f} ~ {bw['f_high_Hz']:.1f} Hz), "
              f"Q = {bw['q_factor']:.2f}{censored_tag} "
              f"(exclude {bw['n_excluded']})")
if len(freqs_li) > 0:
    bw = bandwidth_offline_lockin
    if bw["bandwidth_Hz"] is not None:
        censored_tag = " (censored)" if bw["bandwidth_is_censored"] else ""
        print(f"[offline lock-in] -3dB 带宽 = {bw['bandwidth_Hz']:.1f} Hz "
              f"({bw['f_low_Hz']:.1f} ~ {bw['f_high_Hz']:.1f} Hz), "
              f"Q = {bw['q_factor']:.2f}{censored_tag} "
              f"(exclude {bw['n_excluded']})")
if acquisition_mode in ("demod_r", "both"):
    bw = bandwidth_hardware_demod_r_vector
    if bw["bandwidth_Hz"] is not None:
        censored_tag = " (censored)" if bw["bandwidth_is_censored"] else ""
        print(f"[hardware vector]  -3dB 带宽 = {bw['bandwidth_Hz']:.1f} Hz "
              f"({bw['f_low_Hz']:.1f} ~ {bw['f_high_Hz']:.1f} Hz), "
              f"Q = {bw['q_factor']:.2f}{censored_tag}")
    bw = bandwidth_hardware_demod_r_scalar
    if bw["bandwidth_Hz"] is not None:
        censored_tag = " (censored)" if bw["bandwidth_is_censored"] else ""
        print(f"[hardware scalar]  -3dB 带宽 = {bw['bandwidth_Hz']:.1f} Hz "
              f"({bw['f_low_Hz']:.1f} ~ {bw['f_high_Hz']:.1f} Hz), "
              f"Q = {bw['q_factor']:.2f}{censored_tag}")
print("-" * 60)
if stat_fft_li is not None:
    print(f"FFT / offline lock-in ratio: median={stat_fft_li['median']:.4f}, "
          f"[{stat_fft_li['min']:.4f}, {stat_fft_li['max']:.4f}] "
          f"(n={stat_fft_li['n']})")
else:
    print("FFT / offline lock-in ratio: <no overlap>")
if stat_hw_li is not None:
    print(f"hardware demod_r / offline lock-in ratio: "
          f"median={stat_hw_li['median']:.4f}, "
          f"[{stat_hw_li['min']:.4f}, {stat_hw_li['max']:.4f}] "
          f"(n={stat_hw_li['n']})")
else:
    print("hardware demod_r / offline lock-in ratio: <no overlap>")

# ---- 拟合汇总 ----
print("-" * 60)
print(f"拟合汇总 (模型: {FIT_MODEL})")
print("-" * 60)

# 单 Lorentzian (无论 FIT_MODEL 都跑, 作为对照)
print("\n[Single Lorentzian]  R(f)=A·hwhm²/((f-f0)²+hwhm²)  -- 对照")
for tag, fit in [("direct FFT", lorentzian_daq_fft),
                  ("offline lock-in", lorentzian_offline_lockin),
                  ("hardware demod_r", lorentzian_hardware_demod_r)]:
    if not fit.get("success"):
        print(f"  [{tag:<18}] 拟合未成功: {fit.get('message', '')}")
        continue
    print(f"  [{tag:<18}] f0 = {fit['f0_Hz']:.1f} ± {fit['f0_unc_Hz']:.1f} Hz, "
          f"FWHM = {fit['fwhm_Hz']:.1f} ± {fit['fwhm_unc_Hz']:.1f} Hz "
          f"(n={fit['n_points']}, "
          f"residual std = {fit['residual_std']:.3e})")

# 双 Lorentzian (主拟合)
print("\n[Double Lorentzian (Bloch steady-state)]  "
      "R(f)=C+P0·√(Γ²+f²)/√((Γ²-f²+Ω²)²+4Γ²f²)")
if FIT_MODEL == "double_lorentzian":
    for tag, fit in [("direct FFT", doublelorentzian_daq_fft),
                      ("offline lock-in", doublelorentzian_offline_lockin),
                      ("hardware demod_r", doublelorentzian_hardware_demod_r)]:
        if not fit.get("success"):
            print(f"  [{tag:<18}] 拟合未成功: {fit.get('message', '')}")
            continue
        line = (f"  [{tag:<18}] Γ = {fit['Gamma_Hz']:.3f} ± "
                f"{fit['Gamma_unc_Hz']:.3f} Hz, "
                f"Ω_Ctrl = {fit['Omega_Ctrl_Hz']:.2f} ± "
                f"{fit['Omega_unc_Hz']:.2f} Hz, "
                f"offset = {fit['offset_V']:.2e} ± "
                f"{fit['offset_unc_V']:.1e} V, "
                f"P0 = {fit['P0']:.3e}  "
                f"(n={fit['n_points']}, "
                f"residual std = {fit['residual_std']:.3e})")
        print(line)
        if LARMOR_FREQ_HZ is not None:
            dOmega = fit["Omega_Ctrl_Hz"] - LARMOR_FREQ_HZ
            print(f"    ↳ Ω_Ctrl − LARMOR_FREQ_HZ ({LARMOR_FREQ_HZ} Hz) = "
                  f"{dOmega:+.2f} Hz  (rel {dOmega / LARMOR_FREQ_HZ * 100:+.3f}%)")
else:
    print("  (FIT_MODEL 不是 double_lorentzian，跳过)")

# ---- 保存 YAML ----
analysis_path = results_dir / "analysis.yaml"
with open(analysis_path, "w", encoding="utf-8") as f:
    yaml.dump(analysis, f, default_flow_style=False, allow_unicode=True)
print(f"\n分析结果已保存: {analysis_path}")

# ---- 保存 JSON ----
analysis_json_path = results_dir / "analysis.json"
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


def _to_jsonable_friendly(fit_dict):
    """剔除 fit_curve_* 大数组、保留参数字段，供 YAML/JSON 序列化."""
    if not isinstance(fit_dict, dict):
        return {}
    skip = {"fit_curve_freqs", "fit_curve_values"}
    out = {}
    for k, v in fit_dict.items():
        if k in skip:
            continue
        out[k] = _to_jsonable(v)
    return out

with open(analysis_json_path, "w", encoding="utf-8") as f:
    json.dump(_to_jsonable(analysis), f, indent=2, ensure_ascii=False)
print(f"分析结果 (JSON) 已保存: {analysis_json_path}")

# ---- 保存 NPZ 频响数据 ----
save_npz = {"freq_Hz": np.array(freq_response["freq_Hz"])}
# 用户要求的新字段
save_npz["fft_amp_V"] = np.array(freq_response["fft_amp_V"])
save_npz["offline_lockin_r_V"] = np.array(freq_response["offline_lockin_r_V"])
save_npz["offline_lockin_x_V"] = np.array(freq_response["offline_lockin_x_V"])
save_npz["offline_lockin_y_V"] = np.array(freq_response["offline_lockin_y_V"])
save_npz["offline_lockin_phase_deg"] = np.array(freq_response["offline_lockin_phase_deg"])
save_npz["hardware_demod_r_vector_V"] = np.array(freq_response["hardware_demod_r_vector_V"])
save_npz["hardware_demod_r_scalar_mean_V"] = np.array(freq_response["hardware_demod_r_scalar_mean_V"])
save_npz["hardware_demod_r_x_mean_V"] = np.array(freq_response["hardware_demod_r_x_mean_V"])
save_npz["hardware_demod_r_y_mean_V"] = np.array(freq_response["hardware_demod_r_y_mean_V"])
save_npz["hardware_demod_r_phase_vector_deg"] = np.array(freq_response["hardware_demod_r_phase_vector_deg"])
save_npz["fft_vs_offline_lockin_ratio"] = np.array(freq_response["fft_vs_offline_lockin_ratio"])
save_npz["hardware_vector_vs_offline_lockin_ratio"] = np.array(freq_response["hardware_vector_vs_offline_lockin_ratio"])
save_npz["hardware_scalar_over_vector_ratio"] = np.array(freq_response["hardware_scalar_over_vector_ratio"])

# 兼容旧字段
if freq_response["amp_raw_V"]:
    save_npz["amp_raw_V"] = np.array(freq_response["amp_raw_V"])
    save_npz["amp_corrected_V"] = np.array(freq_response["amp_corrected_V"])
    save_npz["picked_freq_Hz"] = np.array(freq_response["picked_freq_Hz"])
    save_npz["freq_error_Hz"] = np.array(freq_response["freq_error_Hz"])
    save_npz["is_suspicious"] = np.array(freq_response["is_suspicious"], dtype=bool)
# 离线锁相
save_npz["offline_lockin_x_V"] = np.array(freq_response["offline_lockin_x_V"])
save_npz["offline_lockin_y_V"] = np.array(freq_response["offline_lockin_y_V"])
save_npz["offline_lockin_r_V"] = np.array(freq_response["offline_lockin_r_V"])
save_npz["offline_lockin_phase_deg"] = np.array(freq_response["offline_lockin_phase_deg"])
save_npz["offline_lockin_phase_rad"] = np.array(freq_response["offline_lockin_phase_rad"])
save_npz["offline_lockin_n_cycles_used"] = np.array(
    freq_response["offline_lockin_n_cycles_used"], dtype=int)
save_npz["offline_lockin_n_samples_used"] = np.array(
    freq_response["offline_lockin_n_samples_used"], dtype=int)
save_npz["offline_lockin_is_valid"] = np.array(
    freq_response["offline_lockin_is_valid"], dtype=bool)
save_npz["fft_vs_offline_lockin_ratio"] = np.array(
    freq_response["fft_vs_offline_lockin_ratio"])
save_npz["hardware_demod_r_vs_offline_lockin_ratio"] = np.array(
    freq_response["hardware_demod_r_vs_offline_lockin_ratio"])
if demod_r_response["freq_Hz"]:
    save_npz["hardware_demod_r_vector_V"] = np.array(demod_r_response["R_vector_V"])
    save_npz["hardware_demod_r_scalar_mean_V"] = np.array(demod_r_response["R_scalar_mean_V"])
    save_npz["hardware_demod_r_scalar_std_V"] = np.array(demod_r_response["R_scalar_std_V"])
    save_npz["hardware_demod_r_x_mean_V"] = np.array(demod_r_response["X_mean_V"])
    save_npz["hardware_demod_r_y_mean_V"] = np.array(demod_r_response["Y_mean_V"])
    save_npz["hardware_demod_r_phase_vector_deg"] = np.array(
        demod_r_response["phase_vector_deg"])
    save_npz["hardware_demod_r_phase_vector_rad"] = np.array(
        demod_r_response["phase_vector_rad"])
if np.isfinite(baseline_amp):
    save_npz["baseline_background_amp_V"] = np.float64(baseline_amp)
# 标记本次 run 用的取值方法（写入 npz 顶部属性）
save_npz["daq_response_method"] = np.array(DAQ_RESPONSE_METHOD)
save_npz["offline_lockin_use_integer_cycles"] = np.array(
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES)

# ---- 拟合参数 (npz 顶部属性) ----
save_npz["enable_lorentzian_fit"] = np.array(ENABLE_LORENTZIAN_FIT)
save_npz["fit_model"] = np.array(FIT_MODEL)
save_npz["LARMOR_FREQ_Hz_configured"] = np.array(LARMOR_FREQ_HZ) \
    if LARMOR_FREQ_HZ is not None else np.array(np.nan)

# 单 Lorentzian 参数
for tag, fit in [("daq_fft", lorentzian_daq_fft),
                  ("offline_lockin", lorentzian_offline_lockin),
                  ("hardware_demod_r", lorentzian_hardware_demod_r)]:
    save_npz[f"lorentzian_{tag}_success"] = np.array(
        bool(fit.get("success")))
    for scalar_key in ("A", "f0_Hz", "hwhm_Hz", "fwhm_Hz",
                       "A_unc", "f0_unc_Hz", "hwhm_unc_Hz", "fwhm_unc_Hz",
                       "residual_std", "residual_max", "n_points"):
        v = fit.get(scalar_key)
        save_npz[f"lorentzian_{tag}_{scalar_key}"] = (
            np.float64(v) if v is not None else np.float64(np.nan))

# 双 Lorentzian 参数 (Bloch steady-state)
for tag, fit in [("daq_fft", doublelorentzian_daq_fft),
                  ("offline_lockin", doublelorentzian_offline_lockin),
                  ("hardware_demod_r", doublelorentzian_hardware_demod_r)]:
    save_npz[f"doublelorentzian_{tag}_success"] = np.array(
        bool(fit.get("success")))
    for scalar_key in ("P0", "Gamma_Hz", "Omega_Ctrl_Hz", "offset_V",
                       "P0_unc", "Gamma_unc_Hz", "Omega_unc_Hz", "offset_unc_V",
                       "residual_std", "residual_max", "n_points"):
        v = fit.get(scalar_key)
        save_npz[f"doublelorentzian_{tag}_{scalar_key}"] = (
            np.float64(v) if v is not None else np.float64(np.nan))

npz_path = results_dir / "frequency_response.npz"
np.savez(npz_path, **save_npz)
print(f"频响数据已保存: {npz_path}")

print("\n[OK] 数据分析与可视化完成")
