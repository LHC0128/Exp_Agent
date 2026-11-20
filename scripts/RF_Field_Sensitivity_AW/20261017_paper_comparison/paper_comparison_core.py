"""冻结本地对比所需的格式检查和拟合算法，不依赖实验工作流或 GUI。"""

import math

import numpy as np
from scipy.optimize import curve_fit


def analysis_block_reason(config):
    if not (isinstance(config, dict) and config.get("experiment_id") == "rf-sensitivity-aw"
            and config.get("schema_version") in (2, 3)
            and config.get("execution_mode") == "typed_workflow"
            and config.get("acquisition_method") == "direct_aw_x"
            and isinstance(config.get("parameters"), dict)):
        return "仅支持版本 2、3 的 DirectAW 新格式运行，旧数据不支持分析"
    if config.get("completion_status") != "completed":
        return "采集尚未完整完成，仅可查看诊断"
    phase = config.get("xy_phase_calibration")
    if config.get("phase_calibration_status") != "completed" or not isinstance(phase, dict) or phase.get("converged") is not True:
        return "校相未通过，仅可查看诊断"
    return ""


def dispersive(x, amplitude, gamma, x0, offset):
    return amplitude * (x - x0) / ((x - x0) ** 2 + gamma ** 2) + offset


def fit_response_curve(x, y):
    """保留原 X 色散拟合的初值和求解方法；论文对比不使用数值梯度回退。"""
    if len(x) < 5:
        return {"success": False}
    gamma = float(np.ptp(x)) / 4
    try:
        popt, _ = curve_fit(dispersive, x, y,
                            p0=[float(np.ptp(y)) * gamma, gamma, 0.0, float(np.median(y))], maxfev=20000)
        slope = abs(float(popt[0])) / float(popt[1]) ** 2
        if not math.isfinite(slope) or slope <= 0:
            return {"success": False}
        residuals = y - dispersive(x, *popt)
        total = float(np.sum((y - float(np.mean(y))) ** 2))
        r_squared = 1.0 - float(np.sum(residuals**2)) / total if total > 0 else float("nan")
        return {"success": True, "popt": popt, "r_squared": r_squared}
    except (RuntimeError, ValueError, FloatingPointError, ZeroDivisionError):
        return {"success": False}


def load_inputs(directory, config):
    parameters = config["parameters"]
    expected_points = int(parameters["RF_AMP_POINTS"])
    expected_noise = int(parameters["NOISE_N_AVG"])
    nperseg = int(parameters["NOISE_NPERSEG"])
    if expected_points < 2 or expected_noise < 1 or nperseg < 2:
        raise ValueError("运行参数中的响应点数、噪声次数或 Welch 段长无效")
    with np.load(directory / "raw/response_data.npz", allow_pickle=False) as data:
        if "demod_idx" in data and int(data["demod_idx"]) != int(parameters.get("DEMOD3_IDX", 3)):
            raise ValueError("响应记录的 RF 解调器索引与运行配置不一致")
        response = {key: np.asarray(data[key], dtype=float) for key in
                    ("amplitudes_V", "amplitudes_nT", "demod3_x_V", "demod3_y_V", "demod3_r_V")}
        calibration = float(data["z_v_to_nT"])
    if any(value.shape != (expected_points,) or not np.all(np.isfinite(value)) for value in response.values()):
        raise ValueError("响应原始数据不完整或含非有限值")
    field = response["amplitudes_nT"]
    if np.any(np.diff(field) <= 0) or not math.isfinite(calibration) or calibration <= 0 or not np.allclose(field, response["amplitudes_V"] * calibration):
        raise ValueError("响应磁场轴或电压到磁场标定无效")
    noise_files = sorted((directory / "raw").glob("noise_D3_*.npz"))
    if [path.name for path in noise_files] != [f"noise_D3_{index:04d}.npz" for index in range(expected_noise)]:
        raise ValueError("噪声原始记录不完整")
    return response, calibration, noise_files


def lorentzian_response(x, amplitude, gamma, center, offset):
    return offset + amplitude * gamma**2 / ((x - center) ** 2 + gamma**2)


def fit_bandwidth(directory, config):
    """保留 R 扫频的初值、边界和质量门槛，固定本次论文的线宽算法。"""
    if config["parameters"].get("LINEWIDTH_MODE", "dispersive_equivalent") != "frequency_sweep":
        return None
    path = directory / "raw/bandwidth_data.npz"
    if not path.is_file():
        raise ValueError("线宽模式为扫频测线宽，但缺少 raw/bandwidth_data.npz")
    with np.load(path, allow_pickle=False) as data:
        frequency = np.asarray(data["frequency_Hz"], dtype=float)
        if "demod3_r_V" not in data:
            raise ValueError("带宽扫频原始数据缺少 R 均值")
        response = np.asarray(data["demod3_r_V"], dtype=float)
        amplitude = float(data["z_rf_amplitude_vpp"])
        if "demod_idx" in data and int(data["demod_idx"]) != int(config["parameters"].get("DEMOD3_IDX", 3)):
            raise ValueError("带宽扫频记录的 RF 解调器索引与运行配置不一致")
    if frequency.ndim != 1 or frequency.size < 5 or frequency.size != int(config["parameters"].get("BW_FREQ_POINTS", frequency.size)) \
            or response.shape != frequency.shape or not np.all(np.isfinite(frequency)) \
            or not np.all(np.isfinite(response)) or np.any(np.diff(frequency) <= 0) or not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("带宽扫频原始数据不完整或含非有限值")
    span = float(np.ptp(frequency))
    step = float(np.median(np.diff(np.sort(frequency))))
    edge_count = max(1, frequency.size // 10)
    baseline = float(np.median(np.r_[response[:edge_count], response[-edge_count:]]))
    peak = int(np.argmax(np.abs(response - baseline)))
    initial = (float(response[peak] - baseline), max(span / 10.0, step), float(frequency[peak]), baseline)
    lower = (-np.inf, max(step * 0.05, np.finfo(float).eps), float(np.min(frequency)), -np.inf)
    upper = (np.inf, span, float(np.max(frequency)), np.inf)
    try:
        parameters, covariance = curve_fit(lorentzian_response, frequency, response,
                                            p0=initial, bounds=(lower, upper), maxfev=30000)
    except Exception as exc:
        raise ValueError(f"带宽扫频洛伦兹拟合质量不合格：拟合异常：{exc}") from exc
    residual = response - lorentzian_response(frequency, *parameters)
    total = float(np.sum((response - np.mean(response)) ** 2))
    r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 1e-30 else 0.0
    uncertainties = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    gamma, center = float(parameters[1]), float(parameters[2])
    reasons = []
    if not np.all(np.isfinite(covariance)):
        reasons.append("拟合协方差无效")
    if r_squared < 0.85:
        reasons.append("R² 小于 0.85")
    if gamma <= step or gamma >= span / 2:
        reasons.append("HWHM 超出允许范围")
    if uncertainties[1] / gamma > 0.5:
        reasons.append("HWHM 相对不确定度大于 0.5")
    if not float(np.min(frequency)) < center < float(np.max(frequency)):
        reasons.append("拟合中心不在扫描范围内")
    if reasons:
        raise ValueError("带宽扫频洛伦兹拟合质量不合格：" + "；".join(reasons))
    fit_frequency = np.linspace(float(frequency.min()), float(frequency.max()), 400)
    return {"center_Hz": center, "hwhm_Hz": gamma, "amplitude_V": float(parameters[0]),
            "offset_V": float(parameters[3]), "r_squared": r_squared, "gamma_uncertainty_Hz": float(uncertainties[1]),
            "n_points": int(frequency.size), "frequency_min_Hz": float(frequency.min()), "frequency_max_Hz": float(frequency.max()),
            "rf_amplitude_vpp": amplitude, "response_component": "R", "fit_freq_Hz": fit_frequency,
            "fit_r_V": lorentzian_response(fit_frequency, *parameters)}
