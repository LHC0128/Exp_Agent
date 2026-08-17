"""Keithley 6221 固定电流档位、包络校验与最优控制任意波步骤。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from ..common import validate_safety_limit


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


def configure_keithley_6221_optimal_control(
    source: Any,
    theory: Any,
    applied: Any,
    *,
    current_range_ma: float,
    compliance_v: float,
) -> float:
    """按理论波形配置 6221 ARB0 外触发最优控制，返回触发间隔归一化不活跃值。

    ``theory`` 需要 ``repeat_frequency_hz``，``applied`` 需要归一化波形及
    ``amplitude_peak_ma`` / ``offset_ma`` / ``minimum_ma`` / ``maximum_ma``。
    """
    validate_safety_limit("keithley_6221_main_field", applied.minimum_ma)
    validate_safety_limit("keithley_6221_main_field", applied.maximum_ma)
    require_keithley_6221_current_range(
        current_range_ma,
        applied.minimum_ma,
        applied.maximum_ma,
    )
    source.abort_waveform()
    source.set_output(False)
    source.set_current(0.0)
    source.set_autorange(False)
    source.set_current_range(current_range_ma / 1000.0)
    source.set_output_response("FAST")
    source.set_analog_filter(False)
    source.set_compliance(compliance_v)
    source.set_compliance_test(True)
    source.set_waveform_function("ARB0")
    source.set_waveform_frequency(theory.repeat_frequency_hz)
    source.set_waveform_amplitude(applied.amplitude_peak_ma / 1000.0)
    source.set_waveform_offset(applied.offset_ma / 1000.0)
    source.set_waveform_ranging("FIXED")
    source.set_waveform_duration("CYCLES", 1.0)
    source.upload_arbitrary(applied.normalized)
    source.set_external_trigger(True)
    source.set_external_trigger_line(1)
    source.set_external_trigger_ignore(False)
    inactive_normalized = float(-applied.offset_ma / applied.amplitude_peak_ma)
    if not -1.0 <= inactive_normalized <= 1.0:
        raise ValueError(
            "6221 任意波包络不包含 0 mA，无法将触发间隔安全设置为 0 mA"
        )
    source.set_external_trigger_inactive_value(inactive_normalized)
    source.wait_for_operation_complete()
    source.raise_for_errors()
    check_keithley_6221_compliance(source, "外部触发启动前")
    source.arm_waveform()
    source.start_waveform()
    return inactive_normalized


def shutdown_keithley_6221(source: Any) -> None:
    """中止波形、关闭输出、归零并回读确认；任何一步失败都收集后统一抛出。"""
    errors: list[str] = []

    def verify() -> None:
        current_a = float(source.get_current())
        output_on = bool(source.get_output())
        if abs(current_a) > 1e-12 or output_on:
            raise RuntimeError(
                f"回读为 {current_a:.9g} A、输出 {'ON' if output_on else 'OFF'}"
            )

    for label, action in (
        ("ABORT", source.abort_waveform),
        ("关闭输出", lambda: source.set_output(False)),
        ("归零", lambda: source.set_current(0.0)),
        ("回读确认", verify),
    ):
        try:
            action()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise RuntimeError("6221 关断失败: " + "；".join(errors))


def check_keithley_6221_compliance(source: Any, context: str) -> None:
    """检查 Compliance；命中或通信失败时尽力完成安全关断。"""
    try:
        failed = bool(source.is_in_compliance())
    except Exception as exc:
        try:
            shutdown_keithley_6221(source)
        except Exception as shutdown_exc:
            raise RuntimeError(
                f"6221 在{context}检查 Compliance 失败: {exc}；"
                f"关断同时失败: {shutdown_exc}"
            ) from exc
        raise RuntimeError(f"6221 在{context}检查 Compliance 失败: {exc}，已关断") from exc
    if failed:
        try:
            shutdown_keithley_6221(source)
        except Exception as shutdown_exc:
            raise RuntimeError(
                f"6221 在{context}进入 Compliance；关断同时失败: {shutdown_exc}"
            ) from shutdown_exc
        raise RuntimeError(f"6221 在{context}进入 Compliance，实验已安全终止")
