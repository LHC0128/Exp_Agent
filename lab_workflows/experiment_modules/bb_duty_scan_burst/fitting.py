"""原占空比扫描的 Hilbert、建立与全振荡暗衰减拟合；保持原边界和误差规则。"""
import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import hilbert


def make_env_build_up(t0):
    """包络 build-up: S_env(t) = offset + A * (1 - exp(-(t-t0)/tau))"""
    def model(t_abs, offset, A_inf, tau_BB):
        t_eff = np.maximum(0, t_abs - t0)
        return offset + A_inf * (1.0 - np.exp(-t_eff / tau_BB))
    return model


def make_bb_decay(t1):
    """返回 Bell-Bloom dark decay 模型: S(t) = offset + A_0·e^(-(t-t1)/tau)·sin(omega·t+phi)"""
    def model(t_abs, offset, A_0, tau_dark, omega, phi):
        t_eff = np.maximum(0, t_abs - t1)
        envelope = np.exp(-t_eff / tau_dark)
        return offset + A_0 * envelope * np.sin(omega * t_abs + phi)
    return model


TAU_BB_BOUNDS   = (1e-5,  0.05)


TAU_DARK_BOUNDS = (1e-5,  0.05)


LARMOR_OMEGA_BOUNDS = (2*np.pi*85e3, 2*np.pi*95e3)


PHASE_BOUNDS = (0.0, 2*np.pi)


def _try_fit(model, t, y, p0, bounds, maxfev=20000):
    """统一的拟合封装, 返回 popt / pcov 或 (None, None) 表示失败."""
    try:
        popt, pcov = curve_fit(model, t, y, p0=p0, bounds=bounds, maxfev=maxfev)
        return popt, pcov
    except Exception as e:
        print(f"    拟合失败: {e}")
        return None, None


def compute_envelope(signal, larmor_samples_hint=None):
    """用 Hilbert 变换求信号包络。"""
    analytic = hilbert(signal)
    return np.abs(analytic)


def fit_one_shot(npz_path, duty_cycle, *, post_dark_time_s=0.020):
    """对单个 shot npz 文件做完整分析：分段 + 拟合 + 汇总。

    自动检测活跃窗口:
      - build-up: 从 burst 开始到包络饱和 (达到峰值 90%), 避免拟合饱和平顶
      - decay:   从 burst 结束到包络衰减至 baseline×1.5, 避免拟合纯噪声段
    """
    with np.load(npz_path, allow_pickle=False) as archive:
        data = {key: archive[key] for key in archive.files}
    time_s = data["time_s"]
    signal = data["signal"]
    t_burst_start = float(data["t_burst_start_s"])
    t_burst_end = float(data["t_burst_end_s"])

    # ---- 包络 (用于窗口检测 / 初始猜测 / 可视化, 拟合走原始 signal) ----
    envelope = compute_envelope(signal)

    # ---- baseline: pre-burst 段的中位数 ----
    pre_mask = time_s < t_burst_start
    if "baseline_offset" in data:
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
        if pcov_build is not None and pcov_build[2, 2] > 0 and np.isfinite(pcov_build[2, 2]):
            tau_BB_err = float(np.sqrt(pcov_build[2, 2]))
        else:
            tau_BB_err = np.inf
        # [经验] pcov 在快时间常数上可能数值病态，对 tau_err 做相对截断
        #       若 tau_err > 10×tau，说明协方差矩阵伪逆不可靠，截断到 10×tau 并标记不可靠
        if tau_BB > 1e-5 and tau_BB_err > tau_BB * 10.0:
                print(f"    [build-up] tau_err={tau_BB_err*1e3:.2f}ms >> tau={tau_BB*1e3:.2f}ms, "
                      f"截断到 10×tau")
                tau_BB_err = tau_BB * 10.0
        fit_reliable_BB = (tau_BB > 1e-5) and (tau_BB_err < tau_BB * 5) and (tau_BB < 0.045)
    else:
        fit_reliable_BB = False

    # ================================================================
    # Decay 活跃窗口: 从包络梯度检测
    # ================================================================
    dm = (time_s >= t_burst_end) & (time_s <= t_burst_end + post_dark_time_s)
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
        if pcov_decay is not None and pcov_decay[2, 2] > 0 and np.isfinite(pcov_decay[2, 2]):
            tau_dark_err = float(np.sqrt(pcov_decay[2, 2]))
        else:
            tau_dark_err = np.inf
        # [经验] pcov 在快时间常数上可能数值病态，对 tau_err 做相对截断
        if tau_dark > 1e-5 and tau_dark_err > tau_dark * 10.0:
            print(f"    [decay] tau_err={tau_dark_err*1e3:.2f}ms >> tau={tau_dark*1e3:.2f}ms, "
                  f"截断到 10×tau")
            tau_dark_err = tau_dark * 10.0
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
        "duty_cycle": duty_cycle,
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
