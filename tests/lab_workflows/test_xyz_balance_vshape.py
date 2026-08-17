"""XYZ 平衡场 V 形拟合与异常点检测单元测试。"""

from __future__ import annotations

import numpy as np
import pytest

from lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.vshape import (
    detect_grid_outliers,
    fit_complex_linear_mod,
    fit_vshape_1d,
)


def test_fit_vshape_1d_recovers_zero_on_clean_data() -> None:
    s = np.linspace(-0.03, 0.03, 13)
    r = np.abs(2.0 * (s - 0.004)) + 0.01
    rng = np.random.default_rng(0)
    r = r + rng.normal(0.0, 0.0005, r.size)
    fit = fit_vshape_1d(s, r)
    assert fit["success"] is True
    assert fit["s0"] == pytest.approx(0.004, abs=0.002)
    assert fit["k"] == pytest.approx(2.0, rel=0.15)
    assert fit["e"] == pytest.approx(0.01, abs=0.005)
    assert fit["s0_inside_range"] is True
    assert fit["left_points"] >= 3
    assert fit["right_points"] >= 3


def test_fit_vshape_1d_rejects_isolated_spikes() -> None:
    s = np.linspace(-0.03, 0.03, 13)
    r = np.abs(2.0 * (s - 0.004)) + 0.01
    r[3] = 0.001  # 孤立异常低点
    r[9] = 0.001
    fit = fit_vshape_1d(s, r)
    assert fit["success"] is True
    assert fit["s0"] == pytest.approx(0.004, abs=0.002)
    assert fit["rejected_points"] >= 2


def test_fit_vshape_1d_reports_failure_for_few_points() -> None:
    fit = fit_vshape_1d(np.array([0.0, 0.1]), np.array([0.2, 0.3]))
    assert fit["success"] is False
    assert fit["failure_reason"] == "insufficient_points"


def test_fit_vshape_1d_reports_failure_for_non_finite() -> None:
    fit = fit_vshape_1d(
        np.array([0.0, 0.1, 0.2, 0.3]),
        np.array([0.2, np.nan, 0.4, 0.5]),
    )
    assert fit["success"] is False
    assert fit["failure_reason"] == "non_finite"


def test_detect_grid_outliers_marks_isolated_spikes() -> None:
    z_axis = np.array([-0.01, 0.0, 0.01])
    x_axis = np.linspace(-0.02, 0.02, 9)
    y_axis = np.linspace(-0.02, 0.02, 9)
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    r = np.abs(x_grid - 0.002) + np.abs(y_grid + 0.004) + 0.05
    r[1, 4, 4] = 0.0001  # 比邻域低 ~0.055 V
    mask = detect_grid_outliers(r, absolute_floor=0.02)
    assert bool(mask[1, 4, 4]) is True
    assert int(mask.sum()) == 1


def test_detect_grid_outliers_clean_grid_has_no_flags() -> None:
    z_axis = np.array([-0.01, 0.0, 0.01])
    x_axis = np.linspace(-0.02, 0.02, 9)
    y_axis = np.linspace(-0.02, 0.02, 9)
    z_grid, x_grid, y_grid = np.meshgrid(z_axis, x_axis, y_axis, indexing="ij")
    r = np.abs(x_grid) + np.abs(y_grid) + 0.01
    mask = detect_grid_outliers(r)
    assert not np.any(mask)


def test_detect_grid_outliers_single_point_axis_has_no_flags() -> None:
    mask = detect_grid_outliers(np.array([[[0.4]]]))
    assert not np.any(mask)


def test_fit_complex_linear_mod_recovers_zero() -> None:
    s = np.linspace(-0.03, 0.03, 13)
    # |(2+1.2i)(s-0.004)| 的模
    r = np.abs((2.0 + 1.2j) * (s - 0.004))
    rng = np.random.default_rng(0)
    r = r + rng.normal(0.0, 0.0005, r.size)
    fit = fit_complex_linear_mod(s, r)
    assert fit["success"] is True
    assert fit["mode"] == "complex_linear_mod"
    assert fit["s0"] == pytest.approx(0.004, abs=0.001)
    assert fit["k"] == pytest.approx(np.hypot(2.0, 1.2), rel=0.1)
    assert fit["e"] == pytest.approx(0.0, abs=0.003)


def test_fit_complex_linear_mod_recovers_offset_zero_with_background() -> None:
    s = np.linspace(-0.03, 0.03, 13)
    # |Jy*(s - s0) + C|：C 与 Jy 有相位差 -> 谷底非零且零点偏移
    jy = 1.5 + 0.9j
    s_true = 0.006
    c = 0.02 - 0.015j
    r = np.abs(jy * (s - s_true) + c)
    # 理论顶点（|Jy*s + C'| 的模平方最小点）
    c_prime = c - jy * s_true
    s_expected = -np.real(np.conj(jy) * c_prime) / abs(jy) ** 2
    e_expected = abs(jy * (s_expected - s_true) + c)
    fit = fit_complex_linear_mod(s, r)
    assert fit["success"] is True
    assert fit["s0"] == pytest.approx(s_expected, abs=0.001)
    assert fit["e"] == pytest.approx(e_expected, abs=0.002)


def test_fit_complex_linear_mod_rejects_spikes() -> None:
    s = np.linspace(-0.03, 0.03, 13)
    r = np.abs((2.0 + 1.2j) * (s - 0.004))
    r[3] = 0.001
    r[9] = 0.001
    fit = fit_complex_linear_mod(s, r)
    assert fit["success"] is True
    assert fit["s0"] == pytest.approx(0.004, abs=0.001)
    assert fit["rejected_points"] >= 2


def test_fit_complex_linear_mod_fails_for_few_points() -> None:
    fit = fit_complex_linear_mod(np.array([0.0, 0.1, 0.2]), np.array([0.2, 0.3, 0.4]))
    assert fit["success"] is False
