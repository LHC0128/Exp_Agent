"""只读原始记录，按零 RF 工作点和公共频带生成可追溯的论文对比。"""

from pathlib import Path
import csv
import hashlib
import json
import platform

import numpy as np
import scipy
from scipy.optimize import curve_fit
from scipy.signal import welch
import yaml

from paper_comparison_core import analysis_block_reason, dispersive, fit_bandwidth, fit_response_curve, load_inputs


def slope_at_field(parameters, field=0.0):
    """返回局部导数及其对完整参数向量的梯度，用于协方差传播。"""
    amplitude, gamma, center, _ = parameters
    delta = field - center
    denominator = delta**2 + gamma**2
    slope = amplitude * (gamma**2 - delta**2) / denominator**2
    gradient = np.array([
        (gamma**2 - delta**2) / denominator**2,
        2 * amplitude * gamma * (3 * delta**2 - gamma**2) / denominator**3,
        2 * amplitude * delta * (3 * gamma**2 - delta**2) / denominator**3,
        0.0,
    ])
    return float(slope), gradient


def _load_run(directory):
    directory = Path(directory).resolve()
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if reason := analysis_block_reason(config):
        raise ValueError(f"{directory.name}：{reason}")
    if config.get("response_component") != "X" or config.get("noise_component") != "X":
        raise ValueError("论文对比要求同一 RF 解调器的 X 标定和 X 噪声")
    if config.get("noise_output_snapshot", {}).get("z", {}).get("output") is not False:
        raise ValueError("缺少零 RF 噪声工作点的关闭输出快照")
    response, calibration, noise_files = load_inputs(directory, config)
    field, signal = response["amplitudes_nT"], response["demod3_x_V"]
    if not field[0] < 0 < field[-1]:
        raise ValueError("响应扫描必须覆盖零 RF 工作点")
    fit = fit_response_curve(field, signal)
    if not fit["success"]:
        raise ValueError("论文对比要求有效的色散拟合，不用最大数值梯度代替")
    # 复用现有模型与初值，保留完整协方差，不能把相关参数的误差直接相加。
    parameters, covariance = curve_fit(dispersive, field, signal, p0=fit["popt"], maxfev=20000)
    slope, gradient = slope_at_field(parameters)
    if not np.all(np.isfinite(covariance)) or not np.isfinite(slope) or slope <= 0:
        raise ValueError("零场响应斜率或拟合协方差无效")
    slope_se = float(np.sqrt(max(0.0, gradient @ covariance @ gradient)))
    with np.load(directory / "raw/response_data.npz", allow_pickle=False) as data:
        signal_sd = np.asarray(data["demod3_x_std_V"], dtype=float)
    if signal_sd.shape != signal.shape or not np.all(np.isfinite(signal_sd)) or np.any(signal_sd < 0):
        raise ValueError("响应记录的 X 标准差无效")
    bandwidth = fit_bandwidth(directory, config)
    if bandwidth is None:
        raise ValueError("论文对比要求实测扫频线宽，不使用幅度域等效线宽")
    frequency, spectra, rates = None, [], []
    nperseg = int(config["parameters"]["NOISE_NPERSEG"])
    for path in noise_files:
        with np.load(path, allow_pickle=False) as data:
            values = np.asarray(data["noise_x_V"], dtype=float)
            rate = float(data["actual_rate_Sa_s"])
            if str(data["signal_path"]) != "sample.x" or int(data["demod_idx"]) != int(config["parameters"]["DEMOD3_IDX"]):
                raise ValueError(f"{path.name} 的噪声来源与响应标定不一致")
        if values.ndim != 1 or len(values) < nperseg or not np.all(np.isfinite(values)) or not np.isfinite(rate) or rate <= 0:
            raise ValueError(f"{path.name} 的时序、实际速率或 Welch 段长无效")
        axis, psd = welch(values - values.mean(), fs=rate, window="hann", nperseg=nperseg,
                          noverlap=nperseg // 2, detrend="constant", return_onesided=True, scaling="density")
        if frequency is None:
            frequency = axis
        elif axis.shape != frequency.shape or not np.allclose(axis, frequency, rtol=1e-10, atol=1e-10):
            raise ValueError("噪声记录的频率网格不一致；论文对比不自动插值")
        spectra.append(psd)
        rates.append(rate)
    matrix = np.asarray(spectra)
    mean_psd = matrix.mean(axis=0)
    raw_sensitivity = np.sqrt(mean_psd) / slope * 1e6
    correction = np.sqrt(1 + (frequency / bandwidth["hwhm_Hz"])**2)
    with np.load(directory / "raw/xy_aw_waveforms.npz", allow_pickle=False) as data:
        envelope = np.asarray(data["omega_ctrl_Hz"], dtype=float)
    if envelope.ndim != 1 or not envelope.size or not np.all(np.isfinite(envelope)):
        raise ValueError("控制包络快照无效")
    source_files = [config_path, directory / "raw/response_data.npz", directory / "raw/bandwidth_data.npz",
                    directory / "raw/xy_aw_waveforms.npz", *noise_files]
    return dict(directory=directory, config=config, field=field, signal=signal, signal_sd=signal_sd,
                parameters=parameters, covariance=covariance, slope=slope, slope_se=slope_se,
                center_slope=abs(float(parameters[0] / parameters[1]**2)), fit_r_squared=fit["r_squared"],
                bandwidth=bandwidth, frequency=frequency, spectra=matrix, mean_psd=mean_psd,
                raw_sensitivity=raw_sensitivity, sensitivity=raw_sensitivity * correction,
                correction=correction, rates=rates, nperseg=nperseg, calibration=calibration,
                envelope=envelope, source_files=source_files)


def band_statistics(run, band, rng, *, resamples=2000):
    """整条记录重采样；先平均 PSD 再开方，频率点不作为独立重复。"""
    mask = (run["frequency"] >= band[0]) & (run["frequency"] <= band[1])
    if mask.sum() < 3 or band[0] <= 0 or band[1] > run["bandwidth"]["hwhm_Hz"]:
        raise ValueError("公共统计频带必须为正、至少含三个频点，且不超过两次实测 HWHM")
    spectra = run["spectra"][:, mask]
    count = len(spectra)
    if count < 2:
        raise ValueError("至少需要两条噪声记录估计记录间不确定度")
    weights = rng.multinomial(count, np.full(count, 1 / count), size=resamples) / count
    bootstrap_psd = weights @ spectra
    bootstrap_sensitivity = np.sqrt(bootstrap_psd) * run["correction"][mask] / run["slope"] * 1e6
    medians = np.median(bootstrap_sensitivity, axis=1)
    summary = dict(
        frequency_first_Hz=float(run["frequency"][mask][0]),
        frequency_last_Hz=float(run["frequency"][mask][-1]), n_bins=int(mask.sum()),
        corrected_median_fT_per_sqrtHz=float(np.median(run["sensitivity"][mask])),
        corrected_median_noise_only_ci95=np.quantile(medians, [0.025, 0.975]).tolist(),
        raw_median_fT_per_sqrtHz=float(np.median(run["raw_sensitivity"][mask])),
        voltage_asd_median_V_per_sqrtHz=float(np.median(np.sqrt(run["mean_psd"][mask]))),
    )
    return summary, medians


def compare_for_paper(constant_run, optimized_run, output_dir, *, band=(3.0, 150.0)):
    """生成独立结果目录；原运行配置、状态和结果均只读。"""
    inputs = [Path(constant_run).resolve(), Path(optimized_run).resolve()]
    output = Path(output_dir).resolve()
    if inputs[0] == inputs[1]:
        raise ValueError("常数与优化控制必须来自不同运行")
    if any(output == source or source in output.parents or output in source.parents for source in inputs):
        raise ValueError("输出目录不能与源运行目录重叠")
    if output.exists():
        raise ValueError("输出目录已存在，请使用新目录以保留已有分析")
    band = tuple(map(float, band))
    if len(band) != 2 or not np.all(np.isfinite(band)) or not 0 < band[0] < band[1]:
        raise ValueError("公共频带必须满足 0 < 下限 < 上限")
    runs = [_load_run(source) for source in inputs]
    a, b = (run["config"]["parameters"] for run in runs)
    differences = {key: [a.get(key), b.get(key)] for key in a.keys() | b.keys()
                   if a.get(key) != b.get(key)}
    if differences.keys() - {"RUN_TAG", "ARB_WAVEFORM_FILE"}:
        raise ValueError(f"控制文件之外的实验参数不一致，需先审查：{differences}")
    if not np.allclose(runs[0]["envelope"], runs[0]["envelope"][0]):
        raise ValueError("第一个运行的控制包络不是常数")
    if np.allclose(runs[1]["envelope"], runs[1]["envelope"][0]):
        raise ValueError("第二个运行的控制包络仍是常数")
    rng = np.random.default_rng(20261017)
    statistics = [band_statistics(run, band, rng) for run in runs]
    summaries = []
    for label, run, (stats, _) in zip(("Constant control", "Optimized control"), runs, statistics):
        summaries.append(dict(
            label=label, run_id=run["directory"].name, run_dir=str(run["directory"]),
            field_calibration_nT_per_V=run["calibration"], field_amplitude_convention="RMS",
            scan_range_nT=[float(run["field"][0]), float(run["field"][-1])], n_response_points=len(run["field"]),
            rf_frequency_Hz=float(run["config"]["parameters"]["Z_RF_FREQ"]),
            fit_parameters=run["parameters"].tolist(), fit_covariance=run["covariance"].tolist(),
            fit_r_squared=run["fit_r_squared"], slope_at_zero_V_per_nT=run["slope"],
            slope_at_zero_se_V_per_nT=run["slope_se"], slope_at_fit_center_V_per_nT=run["center_slope"],
            bandwidth={k: v for k, v in run["bandwidth"].items() if not k.startswith("fit_")},
            n_noise_records=len(run["spectra"]), actual_sample_rates_Sa_s=run["rates"],
            envelope_min_Hz=float(run["envelope"].min()), envelope_max_Hz=float(run["envelope"].max()),
            **stats,
        ))
    slope_ratio = runs[1]["slope"] / runs[0]["slope"]
    sensitivity_ratio = summaries[1]["corrected_median_fT_per_sqrtHz"] / summaries[0]["corrected_median_fT_per_sqrtHz"]
    result = dict(
        experiment_id="rf-sensitivity-aw", response_component="X", noise_component="X",
        field_amplitude_convention="RMS, confirmed by the experiment owner; no additional amplitude conversion",
        noise_working_point_nT=0.0, common_band_Hz=list(band), runs=summaries,
        parameter_differences=differences,
        processing=dict(window="periodic Hann", nperseg=runs[0]["nperseg"], noverlap=runs[0]["nperseg"] // 2,
                        detrend="record mean removal followed by segment constant detrend",
                        spectrum="one-sided density; equal-weight PSD average before square root",
                        slope="absolute derivative of a*(B-B0)/((B-B0)^2+gamma^2)+offset at B=0",
                        slope_uncertainty="1 SE from full unweighted-fit covariance; residual-based model uncertainty",
                        correction="sqrt(1+(f_a/HWHM)^2), HWHM from the recorded R frequency sweep",
                        bootstrap="2000 whole-record resamples per run, independent between runs; seed 20261017",
                        confidence_scope="noise record sampling only; slope and HWHM held fixed; no calibration or drift systematics",
                        spectral_smoothing="none"),
        comparison=dict(slope_ratio=slope_ratio, slope_increase_percent=100 * (slope_ratio - 1),
                        slope_ratio_se=slope_ratio * float(np.hypot(runs[0]["slope_se"] / runs[0]["slope"], runs[1]["slope_se"] / runs[1]["slope"])),
                        sensitivity_ratio=sensitivity_ratio, sensitivity_reduction_percent=100 * (1 - sensitivity_ratio),
                        sensitivity_ratio_noise_only_ci95=np.quantile(statistics[1][1] / statistics[0][1], [0.025, 0.975]).tolist()),
        limitations=[
            "One experiment per control condition; repeated noise records do not establish run-to-run reproducibility.",
            "Finite-frequency field referral assumes a single-pole response inferred from R sweep width; the complete X amplitude-modulation transfer is not independently calibrated.",
            "Noise is recorded at B=0; the zero-field derivative, rather than the shifted fit-center maximum, is used.",
            "RMS signal-field calibration is retained without a peak/RMS/peak-to-peak conversion.",
        ],
        software=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, plotting_profile="paper"),
        input_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for run in runs for path in run["source_files"]},
    )
    output.mkdir(parents=True, exist_ok=False)
    arrays = {}
    for prefix, run in zip(("constant", "optimized"), runs):
        for key in ("field", "signal", "signal_sd", "parameters", "covariance", "frequency", "spectra", "mean_psd", "raw_sensitivity", "sensitivity", "correction"):
            arrays[f"{prefix}_{key}"] = run[key]
    np.savez_compressed(output / "comparison_source_data.npz", **arrays)
    from paper_comparison_plot import save_comparison_figures
    save_comparison_figures(runs, result, output)
    from lab_workflows.plotting import poster_style
    directory = Path(__file__).resolve().parent
    code_files = [Path(__file__), Path(__file__).with_name("paper_comparison_plot.py"),
                  directory / "paper_comparison_core.py", directory / "RF_Field_Sensitivity_AW_compare.py",
                  Path(poster_style.__file__)]
    result["code_sha256"] = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in code_files}
    (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    columns = ["label", "run_id", "slope_at_zero_V_per_nT", "slope_at_zero_se_V_per_nT", "slope_at_fit_center_V_per_nT", "corrected_median_fT_per_sqrtHz", "raw_median_fT_per_sqrtHz", "n_noise_records"]
    with (output / "comparison_metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)
    return result
