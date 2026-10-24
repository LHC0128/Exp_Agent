"""三路响应提取、带宽/拟合和原报告构建；不连接仪器。"""
import numpy as np
from .analysis_algorithms import (extract_drive_response, extract_baseline_amplitude, extract_offline_lockin_response, compute_bandwidth, fit_lorentzian, fit_doublelorentzian, _to_jsonable_friendly)

def extract_responses(point_data, params):
    acquisition_mode = params.acquisition_mode
    baseline_pts = [p for p in point_data if p["is_baseline"]]
    signal_pts = [p for p in point_data if not p["is_baseline"]]
    freqs_daq = np.array([])
    amps_raw = np.array([])
    amps_corr = np.array([])
    picked_freqs = np.array([])
    freq_errors = np.array([])
    is_suspicious_arr = np.array([])
    freqs_dR = np.array([])
    R_vec = np.array([])
    R_scalar = np.array([])
    R_scalar_std = np.array([])
    X_mean = np.array([])
    Y_mean = np.array([])
    phase_vec_deg = np.array([])
    phase_vec_rad = np.array([])
    freqs_li = np.array([])
    li_x = np.array([])
    li_y = np.array([])
    li_r = np.array([])
    li_phase_deg = np.array([])
    li_phase_rad = np.array([])
    li_n_cycles = np.array([])
    li_n_samples = np.array([])
    li_valid = np.array([])
    DAQ_RESPONSE_METHOD = params.daq_response_method
    LOCAL_PEAK_REL_WINDOW = params.local_peak_rel_window
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES = params.offline_lockin_use_integer_cycles
    # ---- 构建响应数组 ----
    records_daq = []      # 每个元素是一个 dict
    records_demod_r = []  # 每个元素是一个 dict
    baseline_amp = np.nan

    if acquisition_mode in ("daq_fft_y", "both"):
        # 先估计基线背景（与取值方法无关）
        for p in baseline_pts:
            d = p["data"]
            if "y_V" not in d:
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
            if "y_V" not in d:
                continue
            y = d["y_V"]
            fs = float(d.get("actual_rate_Sa_s", 0.0))
            if fs <= 0:
                continue
            res = extract_drive_response(
                y, fs, p["freq_Hz"],
                method=DAQ_RESPONSE_METHOD,
                local_peak_rel_window=LOCAL_PEAK_REL_WINDOW,
                max_pick_freq_error_hz=params.max_pick_freq_error_hz,
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
            if "demod_r_r_vector_mean_V" not in d:
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
        if "y_V" not in d:
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


    return {'records_daq': records_daq, 'records_demod_r': records_demod_r, 'baseline_amp': baseline_amp, 'freqs_daq': freqs_daq, 'amps_raw': amps_raw, 'amps_corr': amps_corr, 'picked_freqs': picked_freqs, 'freq_errors': freq_errors, 'is_suspicious_arr': is_suspicious_arr, 'freqs_dR': freqs_dR, 'R_vec': R_vec, 'R_scalar': R_scalar, 'R_scalar_std': R_scalar_std, 'X_mean': X_mean, 'Y_mean': Y_mean, 'phase_vec_deg': phase_vec_deg, 'phase_vec_rad': phase_vec_rad, 'records_offline_lockin': records_offline_lockin, 'freqs_li': freqs_li, 'li_x': li_x, 'li_y': li_y, 'li_r': li_r, 'li_phase_deg': li_phase_deg, 'li_phase_rad': li_phase_rad, 'li_n_cycles': li_n_cycles, 'li_n_samples': li_n_samples, 'li_valid': li_valid}


def fit_responses(responses, config, params, point_data, run_dir):
    acquisition_mode = params.acquisition_mode
    xy_const = config.get("xy_const_field", {})
    LARMOR_FREQ_HZ = xy_const.get("LARMOR_FREQ_Hz")
    EXPERIMENT_TYPE = config["experiment_type"]
    DATA_DIR = run_dir
    Z_RF_AMPLITUDE = config["freq_sweep"]["Z_RF_AMPLITUDE_Vpp"]
    baseline_pts = [p for p in point_data if p["is_baseline"]]
    signal_pts = [p for p in point_data if not p["is_baseline"]]
    DAQ_RESPONSE_METHOD = params.daq_response_method
    DLZ_GAMMA_MAX_HZ = params.dlz_gamma_max_hz
    DLZ_GAMMA_MIN_HZ = params.dlz_gamma_min_hz
    DLZ_INCLUDE_OFFSET = params.dlz_include_offset
    DLZ_MIN_POINTS = params.dlz_min_points
    DLZ_OFFSET_INIT_V = params.dlz_offset_init_v
    DLZ_OMEGA_INIT_HZ = params.dlz_omega_init_hz
    DLZ_OMEGA_MAX_HZ = params.dlz_omega_max_hz
    DLZ_OMEGA_MIN_HZ = params.dlz_omega_min_hz
    DLZ_USE_SUSPICIOUS = params.dlz_use_suspicious
    ENABLE_LORENTZIAN_FIT = params.enable_lorentzian_fit
    EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH = params.exclude_suspicious_for_bandwidth
    FIT_MODEL = params.fit_model
    LARMOR_FREQ_HZ = params.larmor_freq_hz
    LOCAL_PEAK_REL_WINDOW = params.local_peak_rel_window
    LORENTZIAN_FIT_MAX_HWHM_HZ = params.lorentzian_fit_max_hwhm_hz
    LORENTZIAN_FIT_MIN_POINTS = params.lorentzian_fit_min_points
    LORENTZIAN_FIT_USE_SUSPICIOUS = params.lorentzian_fit_use_suspicious
    MAX_PICK_FREQ_ERROR_HZ = params.max_pick_freq_error_hz
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES = params.offline_lockin_use_integer_cycles
    Z_RF_AMPLITUDE = params.z_rf_amplitude
    records_daq = responses['records_daq']
    records_demod_r = responses['records_demod_r']
    baseline_amp = responses['baseline_amp']
    freqs_daq = responses['freqs_daq']
    amps_raw = responses['amps_raw']
    amps_corr = responses['amps_corr']
    is_suspicious_arr = responses['is_suspicious_arr']
    freqs_dR = responses['freqs_dR']
    R_vec = responses['R_vec']
    R_scalar = responses['R_scalar']
    records_offline_lockin = responses['records_offline_lockin']
    freqs_li = responses['freqs_li']
    li_r = responses['li_r']
    li_valid = responses['li_valid']
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


    return {'is_susp_daq': is_susp_daq, 'bandwidth_daq_raw': bandwidth_daq_raw, 'bandwidth_daq_corr': bandwidth_daq_corr, 'bandwidth_daq_fft': bandwidth_daq_fft, 'bandwidth_offline_lockin': bandwidth_offline_lockin, 'bandwidth_hardware_demod_r_vector': bandwidth_hardware_demod_r_vector, 'bandwidth_hardware_demod_r_scalar': bandwidth_hardware_demod_r_scalar, 'bandwidth_hardware_demod_r': bandwidth_hardware_demod_r, 'bandwidth_dR': bandwidth_dR, 'lorentzian_daq_fft': lorentzian_daq_fft, 'lorentzian_offline_lockin': lorentzian_offline_lockin, 'lorentzian_hardware_demod_r': lorentzian_hardware_demod_r, 'doublelorentzian_daq_fft': doublelorentzian_daq_fft, 'doublelorentzian_offline_lockin': doublelorentzian_offline_lockin, 'doublelorentzian_hardware_demod_r': doublelorentzian_hardware_demod_r, 'ratio_fft_li': ratio_fft_li, 'ratio_hw_li': ratio_hw_li, 'common_freqs': common_freqs, 'common_hw': common_hw, 'stat_fft_li': stat_fft_li, 'stat_hw_li': stat_hw_li, 'analysis': analysis, 'save_npz': save_npz}
