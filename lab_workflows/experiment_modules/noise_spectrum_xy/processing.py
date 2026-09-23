"""XY 噪声谱：移动脊线定位、标定验收和有约束的局部噪声分离。"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter, median_filter, maximum_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks

from ...analysis.noise_spectrum_separation import lorentzian_vs_control
from ...experiment_runtime import check_cancelled
from .calibration import fit_robust_linear_calibration


def fixed_spurs(matrix, frequency):
    """用跨控制点持续存在的窄线识别杂散，不硬编码 25 kHz。"""
    df = float(np.median(np.diff(frequency)))
    spectrum = np.median(np.log(np.maximum(matrix, 1e-30)), axis=0)
    width = max(5, int(800 / df) | 1)
    excess = spectrum - median_filter(spectrum, size=width, mode="nearest")
    seeds = excess > np.log(5.0)
    # 为窄线及 Welch 主瓣留出保护带；完整原始 PSD 始终保留。
    return maximum_filter1d(seeds.astype(int), size=2 * max(2, int(100 / df)) + 1) > 0


def locate_ridge(matrix, frequency, voltage):
    """归一化仅用于定位；候选线须获得跨电压的独立峰支持。"""
    matrix = np.asarray(matrix, float)
    frequency, voltage = np.asarray(frequency, float), np.asarray(voltage, float)
    if matrix.shape != (voltage.size, frequency.size) or min(matrix.shape) < 20:
        raise ValueError("脊线识别至少需要 20 个有效控制点和频率点")
    if (not np.isfinite(matrix).all() or not np.isfinite(frequency).all() or not np.isfinite(voltage).all() or np.any(matrix <= 0)
            or np.any(np.diff(voltage) <= 0) or np.any(np.diff(frequency) <= 0)):
        raise ValueError("PSD 必须有限且为正，电压与频率轴必须严格递增")
    df = float(np.median(np.diff(frequency)))
    spur = fixed_spurs(matrix, frequency)
    log_psd = np.log(matrix)
    contrast = log_psd - np.median(log_psd, axis=0)
    # 在定位副本中填补窄线，不对用于拟合的 PSD 插值或平滑。
    clean = ~spur
    if clean.sum() < 20:
        raise ValueError("排除固定窄线后频率点不足")
    for row in contrast:
        row[spur] = np.interp(frequency[spur], frequency[clean], row[clean])
    contrast = gaussian_filter(contrast, (1.0, max(1.0, 120.0 / df)))
    contrast -= np.median(contrast, axis=1)[:, None]
    candidates = []
    for i, row in enumerate(contrast):
        peaks, props = find_peaks(row, prominence=0.3, distance=max(1, int(200 / df)))
        good = ~spur[peaks] & (row[peaks] > np.log(1.25))
        peaks, prominence = peaks[good], props["prominences"][good]
        for j in np.argsort(prominence)[-3:]:
            candidates.append((i, frequency[peaks[j]], prominence[j]))
    if len(candidates) < 20:
        raise ValueError("无足够显著的移动峰，拒绝生成控制轴")
    points = np.asarray(candidates)
    rows = points[:, 0].astype(int)
    x, y = voltage[rows], points[:, 1]
    tolerance = max(200.0, 4 * df, 0.006 * np.ptp(frequency))
    rng = np.random.default_rng(20260921)
    hypotheses = []
    for first, second in rng.integers(0, len(points), (2500, 2)):
        if abs(x[second] - x[first]) < 0.35 * np.ptp(voltage):
            continue
        k = (y[second] - y[first]) / (x[second] - x[first])
        b = y[first] - k * x[first]
        if k <= 0 or k * np.ptp(voltage) < 8 * tolerance:
            continue
        residual = abs(y - (k * x + b))
        weights = np.zeros(voltage.size)
        np.maximum.at(weights, rows, np.where(residual < tolerance, np.minimum(points[:, 2], 2), 0))
        hypotheses.append((float(weights.sum()), k, b))
    if not hypotheses:
        raise ValueError("未找到跨越足够频率范围的正斜率移动轨迹")
    hypotheses.sort(reverse=True)
    _, k, b = hypotheses[0]
    # 两条强度接近且明显分离的候选轨迹不得静默择一。
    for score, other_k, other_b in hypotheses[1:]:
        separation = np.median(abs((other_k-k)*voltage + other_b-b))
        if score > 0.85 * hypotheses[0][0] and separation > 4 * tolerance:
            raise ValueError("检测到强度接近的多条移动轨迹，请缩小分析频带后复核")
    chosen = []
    for i in range(voltage.size):
        indices = np.flatnonzero((rows == i) & (abs(y - (k*x+b)) < 2*tolerance))
        if indices.size:
            chosen.append(indices[np.argmin(abs(y[indices]-(k*x[indices]+b)))])
    chosen = np.asarray(chosen, int)
    fit = fit_robust_linear_calibration(x[chosen], y[chosen], min_inliers=20)
    kept = fit.inlier_mask & (abs(y[chosen] - (fit.slope_hz_per_v*x[chosen]+fit.intercept_hz)) < tolerance)
    if kept.sum() < max(20, int(0.45 * voltage.size)):
        raise ValueError("移动峰支持点不足 45%，拒绝标定")
    fit = fit_robust_linear_calibration(x[chosen][kept], y[chosen][kept], min_inliers=20)
    support = chosen[kept][fit.inlier_mask]
    k, b = fit.slope_hz_per_v, fit.intercept_hz
    residual = y[support] - (k*x[support]+b)
    coverage = np.ptp(x[support]) / np.ptp(voltage)
    # 验收阈值来自频率格和扫描跨度，不能随着随机点云的 MAD 无限放宽。
    if coverage < 0.6 or np.std(residual) > tolerance or np.ptp(y[support]) < 8*tolerance:
        raise ValueError("脊线覆盖或残差验收失败，拒绝传播错误控制轴")
    half_slopes = []
    for half in (x[support] <= np.median(x[support]), x[support] > np.median(x[support])):
        half_slopes.append(float(np.polyfit(x[support][half], y[support][half], 1)[0]))
    if abs(half_slopes[0]-half_slopes[1]) / k > 0.2:
        raise ValueError("前后半段斜率相差超过 20%，线性控制轴不适用")
    return dict(k=k, b=b, omega_ctrl=k*voltage+b, contrast=contrast, spur_mask=spur,
                V_candidates=x, f_candidates=y, candidate_rows=rows,
                calibration_inlier_mask=np.isin(np.arange(len(points)), support),
                V_cal=x[support], f_cal=y[support], support_rows=rows[support],
                residual_sigma_hz=fit.residual_sigma_hz, residual_std_hz=float(np.std(residual)),
                coverage=float(coverage), half_slopes=np.asarray(half_slopes))


def fit_local_spectra(matrix, frequency, control, spur_mask, half_width_hz, support_range,
                      *, full_range=False):
    """正值模型与留出验证；全范围模式独立验收峰参数和背景可辨识性。"""
    matrix, frequency, control = (np.asarray(value, float) for value in (matrix, frequency, control))
    spur_mask = np.asarray(spur_mask, bool)
    if (matrix.shape != (control.size, frequency.size) or frequency.size < 2
            or control.size < 12 or spur_mask.shape != frequency.shape
            or not np.isfinite(matrix).all() or np.any(matrix <= 0)
            or not np.isfinite(control).all() or not np.isfinite(frequency).all()
            or np.any(np.diff(control) <= 0) or np.any(np.diff(frequency) <= 0)
            or not np.isfinite(half_width_hz) or half_width_hz <= 0
            or not np.isfinite(support_range).all() or support_range[0] >= support_range[1]):
        raise ValueError("拟合需要正值 PSD、匹配矩阵及递增有限坐标和有效支持范围")
    n = len(frequency)
    parameters = np.full((n, 4), np.nan)
    uncertainties = np.full_like(parameters, np.nan)
    valid = np.zeros(n, bool)
    background_valid = np.zeros(n, bool)
    background_reasons = np.full(n, "peak_fit_rejected", dtype="U48")
    tail_counts = np.zeros(n, int)
    tail_score = np.full(n, np.nan)
    reasons = np.full(n, "outside_ridge_support", dtype="U48")
    residual_score, cv_score = np.full(n, np.nan), np.full(n, np.nan)
    model = np.full_like(matrix, np.nan)
    step = float(np.median(np.diff(control)))
    gamma_min = max(0.5*step, float(np.median(np.diff(frequency))))
    if half_width_hz <= 4 * gamma_min:
        raise ValueError("局部拟合窗口相对控制轴步长过窄")
    for j, f in enumerate(frequency):
        if j % 50 == 0:
            check_cancelled()
        if spur_mask[j]:
            reasons[j] = "fixed_spur"
            continue
        margin = 0 if full_range else half_width_hz
        if not support_range[0] + margin <= f <= support_range[1] - margin:
            continue
        selected = ((control >= support_range[0]) & (control <= support_range[1])
                    if full_range else abs(control-f) <= half_width_hz)
        x, y = control[selected], matrix[selected, j]
        peak_region = abs(x-f) <= half_width_hz
        if peak_region.sum() < 12 or min(np.sum(peak_region & (x < f)), np.sum(peak_region & (x > f))) < 4:
            reasons[j] = "insufficient_two_sided_points"
            continue
        scale = np.median(y)
        y = y/scale
        if np.max(y) < 1.4 * np.median(y):
            reasons[j] = "weak_peak"
            continue
        bounds = ([gamma_min, 0, 1e-12 if full_range else 1e-6, -half_width_hz/2],
                  [half_width_hz/2, np.inf, np.inf, half_width_hz/2])

        def evaluate(p, axis):
            # 用峰高而非极大 A 做数值优化，最终换回原模型的 A。
            gamma, height, baseline, offset = p
            return lorentzian_vs_control(axis, gamma, height*4*gamma**2, baseline, offset, f)

        def solve(xx, yy, start):
            return least_squares(lambda p: np.log(evaluate(p, xx))-np.log(yy), start,
                                 bounds=bounds, x_scale="jac", loss="soft_l1", f_scale=0.2,
                                 max_nfev=250)

        gamma0 = np.clip(400, 1.2*gamma_min, .4*half_width_hz)
        start = [gamma0, max(np.max(y)-np.median(y), .1), max(np.median(y), .01), 0]
        try:
            fit = solve(x, y, start)
            p = fit.x
            prediction = evaluate(p, x)
            residual_score[j] = np.median(abs(y-prediction)/prediction)
            peak_error = np.median(abs(y[peak_region]-prediction[peak_region])/prediction[peak_region])
            gamma, height, baseline, offset = p
            parameters[j] = [gamma, height*4*gamma**2*scale, baseline*scale, offset]
            model[selected, j] = prediction*scale
            if not fit.success or gamma <= 1.05*gamma_min or gamma >= .49*half_width_hz or abs(offset) >= .48*half_width_hz:
                reasons[j] = "optimizer_or_bound"
                continue
            if max(residual_score[j], peak_error) > .3:
                reasons[j] = "large_residual"
                continue
            # 连同响应参数重新拟合留出训练子集，不以全数据响应冒充独立验证。
            errors, amplitudes, backgrounds = [], [], []
            for parity in (0, 1):
                train = np.arange(len(x)) % 2 == parity
                held = solve(x[train], y[train], p)
                pred = evaluate(held.x, x[~train])
                error = abs(y[~train]-pred)/pred
                errors.append(max(np.median(error), np.median(error[peak_region[~train]]))
                              if held.success else np.inf)
                amplitudes.append(held.x[1]*4*held.x[0]**2)
                backgrounds.append(held.x[2])
            cv_score[j] = max(errors)
            if cv_score[j] > .4 or abs(amplitudes[0]-amplitudes[1])/max(height*4*gamma**2,1e-30) > .6:
                reasons[j] = "unstable_holdout"
                continue
            background_unstable = abs(backgrounds[0]-backgrounds[1])/max(baseline,1e-30) > .5
            if not full_range and (baseline < .1*np.percentile(y,20) or background_unstable):
                reasons[j] = "background_unresolved"
                continue
            if height <= .2*baseline:
                reasons[j] = "weak_peak"
                continue
            if full_range:
                # 先归一化雅可比列，避免强峰/微弱底座的量纲差令伪逆丢弃参数方向。
                norms = np.linalg.norm(fit.jac, axis=0)
                if np.any(norms == 0):
                    reasons[j] = "singular_parameters"
                    continue
                normalized = fit.jac / norms
                if np.linalg.matrix_rank(normalized) < 4:
                    reasons[j] = "singular_parameters"
                    continue
                covariance = np.linalg.pinv(normalized.T@normalized) / np.outer(norms, norms)
                covariance *= np.sum(fit.fun**2)/max(1,len(x)-4)
            else:
                covariance = np.linalg.pinv(fit.jac.T@fit.jac) * np.sum(fit.fun**2)/max(1,len(x)-4)
            transform = np.array([[1,0,0,0],[8*height*gamma*scale,4*gamma**2*scale,0,0],
                                  [0,0,scale,0],[0,0,0,1]])
            uncertainties[j] = np.sqrt(np.maximum(np.diag(transform@covariance@transform.T),0))
            if (uncertainties[j,1] > .5*parameters[j,1]
                    or (not full_range and uncertainties[j,2] > .5*parameters[j,2])):
                reasons[j] = "large_parameter_uncertainty"
                continue
            valid[j], reasons[j] = True, "accepted"
            background_valid[j], background_reasons[j] = True, "accepted"
            if full_range:
                # 尾部至少有 8 点由底座主导；强磁尾巴尚未下降到本底时只报告峰参数。
                tail = prediction-baseline <= baseline
                tail_counts[j] = int(tail.sum())
                if tail_counts[j] < 8:
                    background_valid[j], background_reasons[j] = False, "no_background_anchor"
                elif background_unstable or uncertainties[j,2] > .5*parameters[j,2]:
                    background_valid[j], background_reasons[j] = False, "background_unresolved"
                else:
                    # 整块远端留出：防止交错留出遗漏平滑的尾部模型误差。
                    ordered = np.argsort(abs(x-f))
                    held_out = np.zeros(len(x), bool)
                    held_out[ordered[-max(8, len(x)//5):]] = True
                    held = solve(x[~held_out], y[~held_out], p)
                    pred = evaluate(held.x, x[held_out])
                    tail_score[j] = np.median(abs(y[held_out]-pred)/pred) if held.success else np.inf
                    if tail_score[j] > .4:
                        background_valid[j], background_reasons[j] = False, "unstable_tail_holdout"
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            valid[j], background_valid[j] = False, False
            background_reasons[j] = "numerical_failure"
            reasons[j] = "numerical_failure"
    return dict(popt=parameters, perr=uncertainties, fit_mask=valid,
                background_fit_mask=background_valid, background_rejection_reason=background_reasons,
                background_anchor_points=tail_counts, tail_holdout_relative_error=tail_score,
                interpolated_mask=np.zeros(n, bool), rejection_reason=reasons,
                median_relative_residual=residual_score, holdout_relative_error=cv_score,
                model_matrix=model,
                S_beta=np.where(valid, parameters[:,1], np.nan),
                N_S1=np.where(background_valid, parameters[:,2], np.nan))
