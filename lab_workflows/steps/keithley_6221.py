"""Keithley 6221 固定电流档位与包络校验。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA: tuple[tuple[float, str], ...] = (
    (0.000002, "2 nA"),
    (0.00002, "20 nA"),
    (0.0002, "200 nA"),
    (0.002, "2 uA"),
    (0.02, "20 uA"),
    (0.2, "200 uA"),
    (2.0, "2 mA"),
    (20.0, "20 mA"),
    (100.0, "100 mA"),
)


@dataclass(frozen=True, slots=True)
class Keithley6221CurrentRangeCheck:
    """固定档位对完整电流包络的覆盖结果。"""

    selected_range_ma: float
    minimum_current_ma: float
    maximum_current_ma: float
    required_peak_ma: float
    margin_ma: float
    utilization_fraction: float
    satisfies: bool
    recommended_range_ma: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_keithley_6221_current_range(
    selected_range_ma: float,
    minimum_current_ma: float,
    maximum_current_ma: float,
) -> Keithley6221CurrentRangeCheck:
    """计算所选固定档位是否覆盖完整电流包络。"""
    selected = float(selected_range_ma)
    minimum = float(minimum_current_ma)
    maximum = float(maximum_current_ma)
    if not all(math.isfinite(value) for value in (selected, minimum, maximum)):
        raise ValueError("6221 电流档位和控制包络必须是有限数值")
    if minimum > maximum:
        raise ValueError("6221 控制包络下限不能大于上限")

    supported = tuple(value for value, _ in KEITHLEY_6221_CURRENT_RANGE_OPTIONS_MA)
    if not any(math.isclose(selected, value, rel_tol=0.0, abs_tol=1e-15) for value in supported):
        raise ValueError("KEITHLEY_CURRENT_RANGE_MA 必须选择 Keithley 6221 标准电流档位")

    required = max(abs(minimum), abs(maximum))
    tolerance = max(1e-12, selected * 1e-12)
    satisfies = required <= selected + tolerance
    recommended = next(
        (value for value in supported if required <= value + max(1e-12, value * 1e-12)),
        None,
    )
    return Keithley6221CurrentRangeCheck(
        selected_range_ma=selected,
        minimum_current_ma=minimum,
        maximum_current_ma=maximum,
        required_peak_ma=required,
        margin_ma=selected - required,
        utilization_fraction=required / selected,
        satisfies=satisfies,
        recommended_range_ma=recommended,
    )


def require_keithley_6221_current_range(
    selected_range_ma: float,
    minimum_current_ma: float,
    maximum_current_ma: float,
) -> Keithley6221CurrentRangeCheck:
    """返回档位覆盖结果；档位不足时给出完整包络和推荐档位。"""
    result = assess_keithley_6221_current_range(
        selected_range_ma,
        minimum_current_ma,
        maximum_current_ma,
    )
    if result.satisfies:
        return result
    recommendation = (
        f"，请选择至少 {result.recommended_range_ma:g} mA 档"
        if result.recommended_range_ma is not None
        else "，没有可覆盖该包络的标准档位"
    )
    raise ValueError(
        "KEITHLEY_CURRENT_RANGE_MA 不满足控制电流包络要求："
        f"所选 {result.selected_range_ma:g} mA，"
        f"控制范围 {result.minimum_current_ma:.9g} 到 "
        f"{result.maximum_current_ma:.9g} mA，"
        f"需要至少 {result.required_peak_ma:.9g} mA"
        f"{recommendation}"
    )
