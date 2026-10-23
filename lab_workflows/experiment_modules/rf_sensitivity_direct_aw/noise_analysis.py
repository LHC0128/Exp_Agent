"""共享 X 噪声 Welch、逐记录质量审计和可逆平均；只写 results。"""
import csv
import json
import math
import numpy as np
from scipy.signal import welch
from ...analysis.noise_quality import inspect_noise_spectra
from ...experiment_runtime import check_cancelled


def noise_psd(directory, config, nperseg_config, noise_files):
    if isinstance(nperseg_config, bool) or int(nperseg_config) != nperseg_config or nperseg_config < 2:
        raise ValueError("Welch 段长必须是至少 2 的整数")
    nperseg_config = int(nperseg_config)
    parameters, options = config["parameters"], config.get("analysis_options", {})
    policy = options.get("noise_outlier_policy", parameters.get("NOISE_OUTLIER_POLICY", "none"))
    low = options.get("band_min_hz", parameters.get("SENSITIVITY_BAND_MIN_HZ", 3.0))
    high = options.get("band_max_hz", parameters.get("SENSITIVITY_BAND_MAX_HZ", 150.0))
    if policy not in ("none", "mad_high") or not (math.isfinite(low) and math.isfinite(high) and 0 < low < high):
        raise ValueError("异常谱策略或评分频带无效")
    spectra, rates, frequency = [], [], None
    for path in noise_files:
        check_cancelled()
        with np.load(path, allow_pickle=False) as data:
            waveform = np.asarray(data["noise_x_V"], dtype=float)
            rate = float(data["actual_rate_Sa_s"])
            if str(data["signal_path"]) != "sample.x" or int(data["demod_idx"]) != int(parameters.get("DEMOD3_IDX", 3)):
                raise ValueError("噪声记录必须来自运行配置指定的 RF 解调器 X")
        if waveform.ndim != 1 or len(waveform) < 2 or not np.all(np.isfinite(waveform)) or not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"无效噪声记录：{path.name}")
        segment = min(nperseg_config, len(waveform))
        f, psd = welch(waveform - waveform.mean(), fs=rate, nperseg=segment, noverlap=segment // 2, scaling="density")
        if frequency is None:
            frequency = f
        elif len(f) != len(frequency) or not np.allclose(f, frequency):
            # 保留已有频率轴对齐规则；评分另行检查所有记录的真实带宽。
            psd = np.interp(frequency, f, psd)
        spectra.append(psd)
        rates.append(rate)
    matrix = np.asarray(spectra)
    if matrix.ndim != 2 or not len(matrix) or not np.isfinite(matrix).all() or np.any(matrix < 0):
        raise ValueError("逐记录 PSD 无效或缺少噪声记录")
    band = (frequency >= low) & (frequency <= high)
    covered = frequency[-1] >= high and min(rates) / 2 >= high and band.sum() >= 2
    if covered:
        keep, scores, before, average, quality = inspect_noise_spectra(frequency, matrix, low, high, policy)
        status = ("disabled" if policy == "none" else "insufficient_records" if len(matrix) < 5
                  else "zero_baseline" if quality["median_record_asd_V_per_sqrtHz"] <= 0 else "screened")
    else:
        keep, scores = np.ones(len(matrix), dtype=bool), np.full(len(matrix), np.nan)
        before = average = matrix.mean(axis=0)
        quality = dict(policy=policy, minimum_records=5, candidate_indices=[], threshold_asd_V_per_sqrtHz=None)
        status = "disabled" if policy == "none" else "band_unavailable"
    reasons = {"disabled": "策略关闭，保留全部记录", "insufficient_records": "不足 5 条记录，保留全部记录",
               "zero_baseline": "缺少正评分基线，保留全部记录",
               "band_unavailable": "实际 PSD 未完整覆盖评分频带或频带内不足两个频点，未自动剔除", "screened": ""}
    filenames = [path.name for path in noise_files]
    quality.update(status=status, reason=reasons[status], band_Hz=[low, high], band_covered=bool(covered),
        n_records=len(matrix), n_accepted=int(keep.sum()), n_rejected=int((~keep).sum()),
        accepted_records=[name for name, accepted in zip(filenames, keep) if accepted],
        rejected_records=[name for name, accepted in zip(filenames, keep) if not accepted])
    check_cancelled()
    output = directory / "results"
    np.savez(output / "psd_avg.npz", freq_Hz=frequency, psd_avg_V2_per_Hz=average,
        unfiltered_psd_avg_V2_per_Hz=before, psd_matrix_V2_per_Hz=matrix,
        included_record_mask=keep, record_band_median_asd_V_per_sqrtHz=scores,
        record_filenames=np.asarray(filenames), n_avg=int(keep.sum()), n_records=len(matrix),
        actual_rate_Sa_s=float(np.median(rates)), actual_rates_Sa_s=rates, nperseg=nperseg_config)
    (output / "noise_rejection_summary.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    fields = ("record_index", "record_number", "filename", "accepted", "band_median_asd_V_per_sqrtHz", "threshold_asd_V_per_sqrtHz", "reason")
    with (output / "noise_record_quality.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for index, (name, accepted, score) in enumerate(zip(filenames, keep, scores)):
            writer.writerow(dict(record_index=index, record_number=index + 1, filename=name, accepted=bool(accepted),
                band_median_asd_V_per_sqrtHz=float(score) if math.isfinite(score) else None,
                threshold_asd_V_per_sqrtHz=quality["threshold_asd_V_per_sqrtHz"],
                reason=reasons[status] if accepted else "显著高值：同时超过组内中位数3倍与中位数加6个MAD稳健标准差"))
    return dict(n_avg=int(keep.sum()), actual_rates_Sa_s=rates, nperseg=nperseg_config, **quality)
