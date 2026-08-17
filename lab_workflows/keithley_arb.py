"""Keithley 6221 任意波文件与频率标定的只读解析和换算。

从 GUI 前端组件下沉的纯计算逻辑：任意波 CSV/TXT 解析、6221 频率标定
YAML 解析，以及按标定把 Hz 序列反算为电流包络。不连接任何硬件，
供 GUI 接口和实验脚本共用。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import yaml

MAX_ARB_POINTS = 65_535
MIN_REPEAT_FREQUENCY_HZ = 0.001
MAX_REPEAT_FREQUENCY_HZ = 100_000.0
# 允许的标定实验 ID；历史别名可追加，避免标定实验改名后拒绝旧文件。
CALIBRATION_EXPERIMENT_IDS = frozenset({"mx-keithley-6221-main-field-calibration"})


@dataclass(slots=True)
class ArbitraryFileInfo:
    format: str = "frequency-series"
    value_label: str = "frequency_Hz"
    source_min: float = 0.0
    source_max: float = 0.0
    normalization_center: float = 0.0
    normalization_scale: float = 0.0
    inferred_frequency_hz: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ArbitrarySource:
    frequency_values_hz: list[float]
    info: ArbitraryFileInfo

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency_values_hz": self.frequency_values_hz,
            "info": self.info.to_dict(),
        }


@dataclass(slots=True)
class KeithleyCalibration:
    slope_hz_per_ma: float
    intercept_hz: float
    r_squared: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConvertedWaveform:
    points: list[float]
    amplitude_ma: float | None
    offset_ma: float | None
    minimum_ma: float | None
    maximum_ma: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_row(line: str) -> list[str]:
    for separator in (",", "\t", ";"):
        if separator in line:
            return [item.strip() for item in line.split(separator)]
    return [line.strip()]


def _is_numeric(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def _parse_finite_cell(value: str, line_number: int, column_name: str) -> float:
    if value == "":
        raise ValueError(f"第 {line_number} 行的{column_name}为空")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"第 {line_number} 行的{column_name}不是有限数值")
    return converted


def parse_arbitrary_text(text: str) -> ArbitrarySource:
    """解析 6221 Hz 任意波文本，支持单列或 time,frequency 两列。"""
    rows: list[tuple[int, str]] = [
        (index + 1, line.strip())
        for index, line in enumerate(text.lstrip("\ufeff").splitlines())
        if line.strip() and not line.strip().startswith("#")
    ]
    if not rows:
        raise ValueError("任意波文件没有有效数据")

    parsed_rows = [(line_number, _split_row(line)) for line_number, line in rows]
    header: list[str] | None = None
    if any(not _is_numeric(cell) for cell in parsed_rows[0][1]):
        header = parsed_rows[0][1]
        parsed_rows = parsed_rows[1:]
    if len(parsed_rows) < 2:
        raise ValueError("任意波至少需要 2 个数据点")
    if len(parsed_rows) > MAX_ARB_POINTS:
        raise ValueError(f"任意波点数不能超过 {MAX_ARB_POINTS:,}")

    column_count = len(parsed_rows[0][1])
    if column_count not in (1, 2):
        raise ValueError("任意波文件必须是单列 frequency_Hz 或 time_s,frequency_Hz 两列数据")
    for line_number, cells in parsed_rows:
        if len(cells) != column_count:
            raise ValueError(f"第 {line_number} 行的列数不一致")

    time_values: list[float] | None = None
    if column_count == 2:
        time_values = [
            _parse_finite_cell(cells[0], line_number, "时间值")
            for line_number, cells in parsed_rows
        ]
    frequency_values_hz = [
        _parse_finite_cell(cells[column_count - 1], line_number, "频率值")
        for line_number, cells in parsed_rows
    ]

    inferred_frequency_hz: float | None = None
    if time_values is not None:
        steps = [right - left for left, right in zip(time_values, time_values[1:])]
        if any(step <= 0 for step in steps):
            raise ValueError("任意波时间轴必须严格递增")
        sorted_steps = sorted(steps)
        middle = len(sorted_steps) // 2
        median_step = (
            sorted_steps[middle]
            if len(sorted_steps) % 2
            else (sorted_steps[middle - 1] + sorted_steps[middle]) / 2
        )
        tolerance = max(1e-15, abs(median_step) * 1e-6)
        if any(abs(step - median_step) > tolerance for step in steps):
            raise ValueError("任意波时间轴必须等间隔")
        inferred = 1.0 / (len(frequency_values_hz) * median_step)
        if (
            not math.isfinite(inferred)
            or not (MIN_REPEAT_FREQUENCY_HZ <= inferred <= MAX_REPEAT_FREQUENCY_HZ)
        ):
            raise ValueError("时间轴推导的重复频率超出 6221 的 0.001 到 100,000 Hz 范围")
        inferred_frequency_hz = inferred

    source_min = min(frequency_values_hz)
    source_max = max(frequency_values_hz)
    normalization_center = (source_min + source_max) / 2
    normalization_scale = (source_max - source_min) / 2
    if not math.isfinite(normalization_scale) or normalization_scale <= 0:
        raise ValueError("Hz 任意波不能是恒定值")
    return ArbitrarySource(
        frequency_values_hz=frequency_values_hz,
        info=ArbitraryFileInfo(
            value_label=header[column_count - 1] if header else "frequency_Hz",
            source_min=source_min,
            source_max=source_max,
            normalization_center=normalization_center,
            normalization_scale=normalization_scale,
            inferred_frequency_hz=inferred_frequency_hz,
        ),
    )


def parse_calibration_text(text: str) -> KeithleyCalibration:
    """解析 6221 主场频率标定 YAML/JSON 文本。"""
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError("标定文件必须是 YAML 或 JSON 对象")
    if payload.get("success") is False:
        raise ValueError("标定文件标记为 success: false")
    experiment_id = payload.get("experiment_id")
    if isinstance(experiment_id, str) and experiment_id not in CALIBRATION_EXPERIMENT_IDS:
        raise ValueError(f"标定实验 ID 不匹配: {experiment_id}")

    raw_slope = payload.get("K_f_Hz_per_mA")
    slope_hz_per_ma = float(raw_slope) if raw_slope is not None else float("nan")
    if not math.isfinite(slope_hz_per_ma) or slope_hz_per_ma <= 0:
        raise ValueError("标定文件的 K_f_Hz_per_mA 必须是正有限值")
    raw_intercept = payload.get("f_0mA_Hz")
    intercept_hz = float(raw_intercept) if raw_intercept is not None else float("nan")
    if not math.isfinite(intercept_hz):
        raise ValueError("标定文件的 f_0mA_Hz 必须是有限值")

    fit = payload.get("frequency_linear_fit")
    raw_r_squared = fit.get("r_squared") if isinstance(fit, dict) else None
    r_squared = float(raw_r_squared) if raw_r_squared is not None else None
    if r_squared is not None and not math.isfinite(r_squared):
        raise ValueError("标定文件的 frequency_linear_fit.r_squared 不是有限值")
    return KeithleyCalibration(
        slope_hz_per_ma=slope_hz_per_ma,
        intercept_hz=intercept_hz,
        r_squared=r_squared,
    )


def convert_frequency_source(
    source: ArbitrarySource,
    calibration: KeithleyCalibration | None,
) -> ConvertedWaveform:
    """把 Hz 任意波换算为归一化点；有标定时反算电流包络。"""
    if calibration is None:
        center = source.info.normalization_center
        scale = source.info.normalization_scale
        return ConvertedWaveform(
            points=[(value - center) / scale for value in source.frequency_values_hz],
            amplitude_ma=None,
            offset_ma=None,
            minimum_ma=None,
            maximum_ma=None,
        )
    current_ma = [
        (value - calibration.intercept_hz) / calibration.slope_hz_per_ma
        for value in source.frequency_values_hz
    ]
    minimum_ma = min(current_ma)
    maximum_ma = max(current_ma)
    offset_ma = (minimum_ma + maximum_ma) / 2
    amplitude_ma = (maximum_ma - minimum_ma) / 2
    if not math.isfinite(amplitude_ma) or amplitude_ma <= 0:
        raise ValueError("标定换算后的电流波形必须有正的峰值幅度")
    return ConvertedWaveform(
        points=[(value - offset_ma) / amplitude_ma for value in current_ma],
        amplitude_ma=amplitude_ma,
        offset_ma=offset_ma,
        minimum_ma=minimum_ma,
        maximum_ma=maximum_ma,
    )
