# %% [markdown] Cell 0
# # Z 方向磁场频率响应 — 数据分析与可视化
#
# 加载 Z_Field_Bandwidth_Measurement 采集数据，分析 Z 方向磁场频率响应曲线，
# 计算 -3 dB 带宽。
#
# 支持三种采集模式：
# - `daq_fft_y`：对 sample.y 时域数据做谱估计，提取驱动频率处幅值
# - `demod_r`：直接使用额外 Demod 3 读取的 R 响应
# - `both`：同时给出两种方法的频率响应曲线，便于交叉对比
#
# **da`q_fft_y` 取值方法（`DAQ_RESPONSE_METHOD`）**：
# - `"nearest_fft_bin"`（默认）：整段 1 s y_V 做直接 FFT，取最接近 drive_freq 的 bin，
#   避免在高频段 Welch 分辨率不足时被固定干扰线（7195.7 / 7420.6 Hz 等）误选。
# - `"welch_nearest_bin"`：Welch 谱，但只取最接近 drive_freq 的 bin（不做窗口搜索）。
# - `"welch_local_peak"`：Welch 谱 + 局部窗口取最大峰（仅用于 debug，默认不参与带宽）。
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

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from scipy import signal as scipy_signal

print("库导入完成")

# %% Cell 2
# ========== 选择数据目录 ==========
EXPERIMENT_TYPE = "Z_Field_Bandwidth_Measurement"

# True 自动选最新一组数据；False 时手动指定
USE_LATEST = True
DATA_DIR_OVERRIDE = None  # 例如 "data/Z_Field_Bandwidth_Measurement/0701_1039_z_bw"
# DATA_DIR_OVERRIDE = "data/Z_Field_Bandwidth_Measurement/0701_1039_z_bw"

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
    DATA_DIR = project_root / "data" / EXPERIMENT_TYPE / "MMDD_HHMM_z_bw"

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
print(f"采集模式: {acquisition_mode}")
print(f"daq_fft_y 取值方法: {DAQ_RESPONSE_METHOD}")

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
# - demod_r 模式：直接使用 demod_r_mean_V


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
    phase_deg = float(np.degrees(phase_rad))

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
records_daq = []  # 每个元素是一个 dict
records_demod_r = []
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
        print(f"  R_scalar / R_vec median: "
              f"{np.median(R_scalar / R_vec):.4f}")
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
    # 三组数据按 freq 排序后取交集
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

Z_DRIVE_AMPLITUDE = (config.get("freq_sweep", {})
                     .get("Z_DRIVE_AMPLITUDE_Vpp", 0.01))

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

    ax1.set_xlabel("Z field drive frequency (Hz)")
    ax1.set_ylabel("Amplitude at drive bin (V)")
    ax1.set_title("Z Field Frequency Response — daq_fft_y "
                  f"({DAQ_RESPONSE_METHOD}, Z_drive = {Z_DRIVE_AMPLITUDE} Vpp)")
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

    ax2.set_xlabel("Z field drive frequency (Hz)")
    ax2.set_ylabel("Normalized response (a.u.)")
    ax2.set_title("Frequency Response Comparison — direct FFT vs offline lock-in vs hardware demod_r")
    ax2.legend(fontsize=8, loc="best")
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
    ax2b.set_xlabel("Z field drive frequency (Hz)")
    ax2b.set_ylabel("Amplitude (V, log scale)")
    ax2b.set_title("Frequency Response — Absolute Amplitude Comparison")
    ax2b.legend(fontsize=8, loc="best")
    ax2b.grid(True, alpha=0.3, which="both")
    plt.tight_layout()
    fig2b.savefig(results_dir / "frequency_response_absolute.png",
                  dpi=150, bbox_inches="tight")
    print(f"图已保存: {results_dir / 'frequency_response_absolute.png'}")
    plt.show()
else:
    print("⚠ 跳过绝对幅值对比图 (无数据)")

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
    ax2c.set_xlabel("Z field drive frequency (Hz)")
    ax2c.set_ylabel("FFT amplitude / lock-in amplitude")
    ax2c.set_title("Direct FFT vs Offline Lock-in — Amplitude Ratio")
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
    ax2d.set_xlabel("Z field drive frequency (Hz)")
    ax2d.set_ylabel("hardware demod_r / lock-in amplitude")
    ax2d.set_title("Hardware Demod R vs Offline Lock-in — Amplitude Ratio")
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
    ax2e1.set_title("Offline Lock-in — Magnitude & Phase vs Drive Frequency")
    ax2e1.legend(fontsize=8, loc="best")
    ax2e1.grid(True, alpha=0.3)
    ax2e1.set_yscale("log")

    ax2e2.plot(freqs_li[valid], li_phase_deg[valid], "^-", color="C2",
               lw=1.0, ms=4, label="offline lock-in phase")
    if np.any(~li_valid):
        ax2e2.plot(freqs_li[~li_valid], li_phase_deg[~li_valid], "x",
                   color="gray", ms=6, mew=1.5, alpha=0.5,
                   label=f"invalid (n={int(np.sum(~li_valid))})")
    ax2e2.set_xlabel("Z field drive frequency (Hz)")
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
    ax2f.set_xlabel("Z field drive frequency (Hz)")
    ax2f.set_ylabel("Hardware Demod R (V, log scale)")
    ax2f.set_title("Hardware Demod — Vector Mean vs Scalar Mean")
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
    ax2g.set_xlabel("Z field drive frequency (Hz)")
    ax2g.set_ylabel("R_scalar / R_vec")
    ax2g.set_title("Hardware Demod — Scalar / Vector Ratio (positive bias of mean|R|)")
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
    ax2h.set_xlabel("Z field drive frequency (Hz)")
    ax2h.set_ylabel("Phase (deg)")
    ax2h.set_title("Hardware Demod R — Vector Phase vs Drive Frequency")
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
    nonsusp = ~is_susp_daq if is_susp_daq is not None else np.ones_like(freqs_daq, bool)
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
    ax3.set_xlabel("Z field drive frequency (Hz)")
    ax3.set_ylabel("picked_freq - drive_freq (Hz)")
    ax3.set_title("daq_fft_y — Picked Frequency Error Diagnostic")
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
        ax.set_title(f"Spectrum @ drive = {ef:.1f} Hz "
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
                freq_response[k].append(np.nan if isinstance(
                    freq_response[k][0] if freq_response[k] else 0.0, float
                ) else False if k == "offline_lockin_is_valid" else np.nan)

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
    "Z_DRIVE_AMPLITUDE_Vpp": Z_DRIVE_AMPLITUDE,
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
print(f"Z 驱动幅度:  {Z_DRIVE_AMPLITUDE} Vpp")
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

npz_path = results_dir / "frequency_response.npz"
np.savez(npz_path, **save_npz)
print(f"频响数据已保存: {npz_path}")

print("\n[OK] 数据分析与可视化完成")