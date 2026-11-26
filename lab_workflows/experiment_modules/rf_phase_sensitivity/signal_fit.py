"""兼容入口：整周切线对齐与色散/频响拟合实现已迁至 ``z_chain_common.signal_fit``。

``analysis_steps``、``test_workflow`` 与历史分析脚本按本模块路径导入，这里原样再导出
全部公开名与两个内部斜率助手，行为与迁移前一致。
"""
from __future__ import annotations

from ..z_chain_common.signal_fit import (
    _linear_slope,
    _optional_linear_slope,
    best_tangent,
    eq222_y,
    fit_eq222_full_cycle,
    fit_eq222_local,
    fit_sqrt_lorentzian,
    local_slopes,
    wrap_deg,
)

__all__ = [
    "best_tangent",
    "eq222_y",
    "fit_eq222_full_cycle",
    "fit_eq222_local",
    "fit_sqrt_lorentzian",
    "local_slopes",
    "wrap_deg",
]
