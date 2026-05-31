"""色散线形拟合与质量评估."""

from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import curve_fit


@dataclass
class DispersionFitResult:
    """色散拟合结果，包含拟合参数与质量指标."""

    # 拟合参数
    A: float
    gamma_V: float
    V0: float
    C: float
    popt: np.ndarray
    pcov: np.ndarray | None = None

    # 派生量
    slope_VV: float = 0.0           # dY/dV at zero-crossing = |A| / gamma^2
    slope_V_per_fT: float = 0.0     # dY/dB in V/fT
    B0_fT: float = 0.0              # 零交叉点磁场 (fT)
    gamma_nT: float = 0.0           # HWHM in nT
    gamma_fT: float = 0.0           # HWHM in fT
    f_larmor_Hz: float = 0.0        # HWHM in Larmor frequency (Hz)

    # 拟合质量
    r_squared: float = 0.0
    rmse: float = 0.0
    gamma_uncertainty: float = float("nan")
    relative_gamma_uncertainty: float = float("nan")
    n_residual_sign_changes: int = 0
    residual_sign_change_ratio: float = 0.0

    # 质量判定
    is_valid: bool = True
    rejection_reasons: list[str] = field(default_factory=list)


def _dispersive(x, A, gamma, x0, C):
    """色散线形模型: A*(x-x0)/((x-x0)^2 + gamma^2) + C."""
    return A * (x - x0) / ((x - x0) ** 2 + gamma**2) + C


# Larmor 频率常数: f = mu_B * B / (4 * pi * hbar) ~ 7 Hz/nT
MU_B = 9.2740100783e-24
HBAR = 1.054571817e-34
LARMOR_PER_NT = MU_B / (4 * np.pi * HBAR) * 1e-9       # Hz/nT


def _compute_residual_sign_change_ratio(V_sorted, Y_obs, Y_pred):
    """计算残差符号变化率 — 检验残差是否呈随机分布.

    按 V 排序后统计残差符号翻转次数，除以 (N-1) 归一化。
    理想白噪声残差约为 0.5；系统偏差时显著低于 0.25。
    """
    residuals = Y_obs - Y_pred
    signs = np.sign(residuals)
    sign_changes = np.sum(np.diff(signs) != 0)
    n = len(residuals)
    if n > 1:
        return int(sign_changes), sign_changes / (n - 1)
    return 0, 0.0


def _initial_guess(V, Y):
    """对色散线形给出稳定的初值估计."""
    y_range = float(np.max(Y) - np.min(Y))
    # gamma 初值: 使用振幅半高宽估计
    mid = (np.max(Y) + np.min(Y)) / 2
    above_mid = np.where(Y > mid)[0]
    if len(above_mid) > 1:
        gamma_guess = (V[above_mid[-1]] - V[above_mid[0]]) / 2
        gamma_guess = max(gamma_guess, 1e-6)
    else:
        gamma_guess = 0.1 * (np.max(V) - np.min(V))
    V_mid = float(np.mean(V))
    return [y_range, gamma_guess, V_mid, 0.0]


_DEFAULT_QUALITY_THRESHOLDS = {
    "r_squared_min": 0.85,
    "relative_gamma_uncertainty_max": 0.5,
    "residual_sign_change_ratio_min": 0.0,  # 实测色散残差有平滑趋势，宽松门控
    "gamma_min_V": 1e-9,
    "slope_min_V_per_fT": 1e-15,
}


def fit_dispersive(
    V: np.ndarray,
    Y: np.ndarray,
    Z_V_TO_NT: float,
    Z_V_TO_FT: float,
    quality_thresholds: dict | None = None,
) -> DispersionFitResult:
    """对色散曲线 Y(V) 进行拟合，返回 DispersionFitResult.

    Parameters
    ----------
    V : 电压数组 (扫场电压)
    Y : 锁相 Y 信号数组
    Z_V_TO_NT : 电压→nT 转换系数
    Z_V_TO_FT : 电压→fT 转换系数
    quality_thresholds : 质量门控阈值 dict, None 使用默认值

    Returns
    -------
    DispersionFitResult (即使拟合失败也会返回, is_valid=False)
    """
    thresholds = {**_DEFAULT_QUALITY_THRESHOLDS}
    if quality_thresholds:
        thresholds.update(quality_thresholds)

    V_range = float(np.max(V) - np.min(V))

    # 步骤 1: 拟合
    try:
        p0 = _initial_guess(V, Y)
        popt, pcov = curve_fit(
            _dispersive, V, Y, p0=p0, maxfev=5000,
        )
    except Exception as e:
        return DispersionFitResult(
            A=0, gamma_V=0, V0=float(np.mean(V)), C=0,
            popt=np.array([0, 0, float(np.mean(V)), 0]),
            is_valid=False,
            rejection_reasons=[f"拟合异常: {e}"],
        )

    A, gamma, V0, C = popt
    A, gamma, V0, C = float(A), float(abs(gamma)), float(V0), float(C)
    # gamma 取绝对值——dispersive 分母为 gamma², 正负数学等价

    # 步骤 2: 计算 R^2 和 RMSE
    Y_pred = _dispersive(V, *popt)
    ss_res = float(np.sum((Y - Y_pred) ** 2))
    ss_tot = float(np.sum((Y - np.mean(Y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-30 else 0.0
    rmse = float(np.sqrt(ss_res / len(Y)))

    # 步骤 3: gamma 不确定度
    gamma_uncertainty = float("nan")
    relative_gamma_uncertainty = float("nan")
    try:
        if pcov is not None and np.all(np.isfinite(pcov)):
            gamma_uncertainty = float(np.sqrt(max(pcov[1, 1], 0)))
            if gamma > 1e-30:
                relative_gamma_uncertainty = gamma_uncertainty / gamma
    except (IndexError, ValueError):
        pass

    # 步骤 4: 残差符号变化率
    sort_idx = np.argsort(V)
    n_sc, ratio_sc = _compute_residual_sign_change_ratio(
        V[sort_idx], Y[sort_idx], Y_pred[sort_idx],
    )

    # 步骤 5: 派生量
    slope_VV = abs(A) / (gamma**2) if gamma > 1e-30 else 0.0
    slope_V_per_fT = slope_VV / Z_V_TO_FT if Z_V_TO_FT > 0 else 0.0
    B0_fT = V0 * Z_V_TO_FT
    gamma_nT = gamma * Z_V_TO_NT
    gamma_fT = gamma * Z_V_TO_FT
    f_larmor_Hz = gamma_nT * LARMOR_PER_NT

    # 步骤 6: 质量门控
    rejection_reasons = []

    if r_squared < thresholds["r_squared_min"]:
        rejection_reasons.append(
            f"R^2={r_squared:.3f} < {thresholds['r_squared_min']}"
        )
    if gamma <= thresholds["gamma_min_V"]:
        rejection_reasons.append(
            f"gamma={gamma:.2e} <= {thresholds['gamma_min_V']}"
        )
    if not np.isnan(relative_gamma_uncertainty):
        if relative_gamma_uncertainty > thresholds["relative_gamma_uncertainty_max"]:
            rejection_reasons.append(
                f"gamma 相对不确定度={relative_gamma_uncertainty:.1%} > {thresholds['relative_gamma_uncertainty_max']:.0%}"
            )
    if ratio_sc < thresholds["residual_sign_change_ratio_min"]:
        rejection_reasons.append(
            f"残差符号变化率={ratio_sc:.2f} < {thresholds['residual_sign_change_ratio_min']}"
        )
    if slope_V_per_fT < thresholds["slope_min_V_per_fT"]:
        rejection_reasons.append(
            f"斜率={slope_V_per_fT:.2e} < {thresholds['slope_min_V_per_fT']:.1e} V/fT"
        )

    is_valid = len(rejection_reasons) == 0

    return DispersionFitResult(
        A=A, gamma_V=gamma, V0=V0, C=C,
        popt=popt, pcov=pcov,
        slope_VV=slope_VV, slope_V_per_fT=slope_V_per_fT,
        B0_fT=B0_fT,
        gamma_nT=gamma_nT, gamma_fT=gamma_fT,
        f_larmor_Hz=f_larmor_Hz,
        r_squared=r_squared, rmse=rmse,
        gamma_uncertainty=gamma_uncertainty,
        relative_gamma_uncertainty=relative_gamma_uncertainty,
        n_residual_sign_changes=n_sc,
        residual_sign_change_ratio=ratio_sc,
        is_valid=is_valid,
        rejection_reasons=rejection_reasons,
    )


def check_fit_quality(
    fit_result: DispersionFitResult,
    quality_thresholds: dict | None = None,
) -> list[str]:
    """对已有的 DispersionFitResult 重新执行质量检查, 返回 rejection_reasons."""
    thresholds = {**_DEFAULT_QUALITY_THRESHOLDS}
    if quality_thresholds:
        thresholds.update(quality_thresholds)

    reasons = []
    if fit_result.r_squared < thresholds["r_squared_min"]:
        reasons.append(f"R^2={fit_result.r_squared:.3f} < {thresholds['r_squared_min']}")
    if fit_result.gamma_V <= thresholds["gamma_min_V"]:
        reasons.append(f"gamma={fit_result.gamma_V:.2e} <= {thresholds['gamma_min_V']}")
    if not np.isnan(fit_result.relative_gamma_uncertainty):
        if fit_result.relative_gamma_uncertainty > thresholds["relative_gamma_uncertainty_max"]:
            reasons.append(
                f"gamma 相对不确定度={fit_result.relative_gamma_uncertainty:.1%} > {thresholds['relative_gamma_uncertainty_max']:.0%}"
            )
    if fit_result.residual_sign_change_ratio < thresholds["residual_sign_change_ratio_min"]:
        reasons.append(
            f"残差符号变化率={fit_result.residual_sign_change_ratio:.2f} < {thresholds['residual_sign_change_ratio_min']}"
        )
    if fit_result.slope_V_per_fT < thresholds["slope_min_V_per_fT"]:
        reasons.append(
            f"斜率={fit_result.slope_V_per_fT:.2e} < {thresholds['slope_min_V_per_fT']:.1e} V/fT"
        )
    return reasons
