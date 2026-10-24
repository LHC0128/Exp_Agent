"""按原 Z 带宽步骤提取响应、计算三路带宽并生成兼容数据。"""
import numpy as np
from .analysis_algorithms import (extract_drive_response, extract_baseline_amplitude, extract_offline_lockin_response, compute_bandwidth, _interp_at, _ratio_stats)

def extract_responses(point_data, params):
    acquisition_mode = params.acquisition_mode
    DAQ_RESPONSE_METHOD = params.daq_response_method
    LOCAL_PEAK_REL_WINDOW = params.local_peak_rel_window
    MAX_PICK_FREQ_ERROR_HZ = params.max_pick_freq_error_hz
    EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH = params.exclude_suspicious_for_bandwidth
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES = params.offline_lockin_use_integer_cycles
    freqs_daq = amps_raw = amps_corr = picked_freqs = freq_errors = is_suspicious_arr = freqs_dR = R_vec = R_scalar = R_scalar_std = X_mean = Y_mean = phase_vec_deg = phase_vec_rad = freqs_li = li_x = li_y = li_r = li_phase_deg = li_phase_rad = li_n_cycles = li_n_samples = li_valid = np.array([])
    baseline_pts = [p for p in point_data if p["is_baseline"]]
    signal_pts = [p for p in point_data if not p["is_baseline"]]
    records_daq = []  # 每个元素是一个 dict
    records_demod_r = []
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
                local_peak_rel_window=LOCAL_PEAK_REL_WINDOW, max_pick_freq_error_hz=params.max_pick_freq_error_hz,
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
        else:
            freqs_daq = np.array([])
            amps_raw = amps_corr = np.array([])
            picked_freqs = freq_errors = is_suspicious_arr = np.array([])

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
        else:
            freqs_dR = np.array([])
            R_vec = R_scalar = R_scalar_std = np.array([])
            X_mean = Y_mean = np.array([])
            phase_vec_deg = phase_vec_rad = np.array([])

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
    else:
        freqs_li = li_x = li_y = li_r = np.array([])
        li_phase_deg = li_phase_rad = np.array([])
        li_n_cycles = li_n_samples = np.array([], dtype=int)
        li_valid = np.array([], dtype=bool)


    return dict(records_daq=records_daq, records_demod_r=records_demod_r, records_offline_lockin=records_offline_lockin, baseline_amp=baseline_amp, freqs_daq=freqs_daq, amps_raw=amps_raw, amps_corr=amps_corr, picked_freqs=picked_freqs, freq_errors=freq_errors, is_suspicious_arr=is_suspicious_arr, freqs_dR=freqs_dR, R_vec=R_vec, R_scalar=R_scalar, R_scalar_std=R_scalar_std, X_mean=X_mean, Y_mean=Y_mean, phase_vec_deg=phase_vec_deg, phase_vec_rad=phase_vec_rad, freqs_li=freqs_li, li_x=li_x, li_y=li_y, li_r=li_r, li_phase_deg=li_phase_deg, li_phase_rad=li_phase_rad, li_n_cycles=li_n_cycles, li_n_samples=li_n_samples, li_valid=li_valid)

def calculate_bandwidth(responses, config, params, point_data, run_dir):
    acquisition_mode = params.acquisition_mode
    DAQ_RESPONSE_METHOD = params.daq_response_method
    LOCAL_PEAK_REL_WINDOW = params.local_peak_rel_window
    MAX_PICK_FREQ_ERROR_HZ = params.max_pick_freq_error_hz
    EXCLUDE_SUSPICIOUS_FOR_BANDWIDTH = params.exclude_suspicious_for_bandwidth
    OFFLINE_LOCKIN_USE_INTEGER_CYCLES = params.offline_lockin_use_integer_cycles
    records_daq = responses["records_daq"]
    records_demod_r = responses["records_demod_r"]
    records_offline_lockin = responses["records_offline_lockin"]
    baseline_amp = responses["baseline_amp"]
    freqs_daq = responses["freqs_daq"]
    amps_raw = responses["amps_raw"]
    amps_corr = responses["amps_corr"]
    picked_freqs = responses["picked_freqs"]
    freq_errors = responses["freq_errors"]
    is_suspicious_arr = responses["is_suspicious_arr"]
    freqs_dR = responses["freqs_dR"]
    R_vec = responses["R_vec"]
    R_scalar = responses["R_scalar"]
    R_scalar_std = responses["R_scalar_std"]
    X_mean = responses["X_mean"]
    Y_mean = responses["Y_mean"]
    phase_vec_deg = responses["phase_vec_deg"]
    phase_vec_rad = responses["phase_vec_rad"]
    freqs_li = responses["freqs_li"]
    li_x = responses["li_x"]
    li_y = responses["li_y"]
    li_r = responses["li_r"]
    li_phase_deg = responses["li_phase_deg"]
    li_phase_rad = responses["li_phase_rad"]
    li_n_cycles = responses["li_n_cycles"]
    li_n_samples = responses["li_n_samples"]
    li_valid = responses["li_valid"]
    EXPERIMENT_TYPE = "Z_Field_Bandwidth_Measurement"
    DATA_DIR = run_dir
    Z_DRIVE_AMPLITUDE = config.get("freq_sweep", {}).get("Z_DRIVE_AMPLITUDE_Vpp", params.z_drive_amplitude)
    baseline_pts = [p for p in point_data if p["is_baseline"]]
    signal_pts = [p for p in point_data if not p["is_baseline"]]
    common_freqs = common_hw = np.array([])
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




    stat_fft_li = _ratio_stats(ratio_fft_li, "FFT/LI")
    stat_hw_li = _ratio_stats(ratio_hw_li, "HW/LI")

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


    return dict(is_susp_daq=is_susp_daq, bandwidth_daq_raw=bandwidth_daq_raw, bandwidth_daq_corr=bandwidth_daq_corr, bandwidth_daq_fft=bandwidth_daq_fft, bandwidth_offline_lockin=bandwidth_offline_lockin, bandwidth_hardware_demod_r_vector=bandwidth_hardware_demod_r_vector, bandwidth_hardware_demod_r_scalar=bandwidth_hardware_demod_r_scalar, bandwidth_hardware_demod_r=bandwidth_hardware_demod_r, bandwidth_dR=bandwidth_dR, ratio_fft_li=ratio_fft_li, ratio_hw_li=ratio_hw_li, common_freqs=common_freqs, common_hw=common_hw, stat_fft_li=stat_fft_li, stat_hw_li=stat_hw_li, analysis=analysis, save_npz=save_npz)
