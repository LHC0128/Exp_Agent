"""原 ConstXY 科学算法，保持公式、有效掩码及优化器设置。"""
import numpy as np
from scipy import signal as scipy_signal, optimize as scipy_optimize

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
    perr = np.sqrt(np.diag(best_pcov)) if best_pcov is not None else np.full(4 if include_offset else 3, np.nan)
    if not include_offset:
        perr = np.append(perr, 0.0)
    P0_unc, Gamma_unc, Omega_unc, offset_unc = (
        float(perr[0]), float(perr[1]),
        float(perr[2]), float(perr[3]))
    residual = r_v - (model(f_v, *popt) if include_offset
                      else model_no_offset(f_v, *popt[:3]))

    fit_grid = np.linspace(f_min_data, f_max_data, 400)
    fit_vals = (model(fit_grid, *popt) if include_offset
                else model_no_offset(fit_grid, *popt[:3]))

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


