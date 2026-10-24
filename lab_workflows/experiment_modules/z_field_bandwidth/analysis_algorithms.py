"""原 Z 带宽 FFT、软件锁相、带宽及比值算法；不连接仪器。"""
import numpy as np
from scipy import signal as scipy_signal

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
                            local_peak_rel_window: float = 0.01, max_pick_freq_error_hz: float = 20.0):
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
    out["is_suspicious"] = bool(abs(out["freq_error_Hz"]) > max_pick_freq_error_hz)
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


def _interp_at(x_src, y_src, x_query):
    """对 x_query 在 (x_src, y_src) 上做线性插值，要求 x_src 单调."""
    return np.interp(x_query, x_src, y_src, left=np.nan, right=np.nan)


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
