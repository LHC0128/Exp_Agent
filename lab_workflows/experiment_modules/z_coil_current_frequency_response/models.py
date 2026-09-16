"""Z 线圈实际电流频率响应参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ...current_feedback import validate_sense_resistor
from ...experiment_params import parameter
from ..z_coil_inductance_frequency_response.models import (
    ZCoilInductanceFrequencyResponseParams,
)

# 频率轴固定为对数等间隔：扫频只描述连续频带，不再与任何目标 AW 网格绑定。
FREQUENCY_SPACING = "logarithmic"
# 单次运行最多允许的扫频点数，避免误填密度造成不可接受的采集时长。
MAXIMUM_FREQUENCY_POINTS = 2000
DEFAULT_FREQUENCY_START_HZ = 100.0
DEFAULT_FREQUENCY_STOP_HZ = 500000.0
DEFAULT_FREQUENCY_POINTS_PER_DECADE = 20


def frequency_points_for(
    start_hz: float, stop_hz: float, points_per_decade: int,
) -> int:
    """按对数跨度与密度返回覆盖整个区间的扫频点数。"""
    decades = math.log10(float(stop_hz) / float(start_hz))
    return int(math.ceil(decades * int(points_per_decade))) + 1


DEFAULT_FREQUENCY_POINTS = frequency_points_for(
    DEFAULT_FREQUENCY_START_HZ,
    DEFAULT_FREQUENCY_STOP_HZ,
    DEFAULT_FREQUENCY_POINTS_PER_DECADE,
)


@dataclass(slots=True)
class ZCoilCurrentFrequencyResponseParams(ZCoilInductanceFrequencyResponseParams):
    """沿对数频率轴相干扫描实际电流复响应，供闭环插值复用。"""

    schema_version = 4

    run_tag: str = parameter(
        default="z_coil_current_frequency_response",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    frequency_start_hz: float = parameter(
        default=DEFAULT_FREQUENCY_START_HZ,
        external_name="FREQUENCY_START_HZ",
        label="扫频起点",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    frequency_stop_hz: float = parameter(
        default=DEFAULT_FREQUENCY_STOP_HZ,
        external_name="FREQUENCY_STOP_HZ",
        label="扫频终点",
        unit="Hz",
        group="basic",
        minimum=0.001,
        maximum=5000000.0,
    )
    frequency_points_per_decade: int = parameter(
        default=DEFAULT_FREQUENCY_POINTS_PER_DECADE,
        external_name="FREQUENCY_POINTS_PER_DECADE",
        label="每十倍频程点数",
        group="basic",
        minimum=2,
        maximum=500,
        description="对数轴密度；实际点数由起点、终点与该密度按覆盖整个区间推算。",
    )
    frequency_spacing: str = parameter(
        default=FREQUENCY_SPACING,
        external_name="FREQUENCY_SPACING",
        label="频率轴刻度",
        visible=False,
    )
    frequency_points: int = parameter(
        default=DEFAULT_FREQUENCY_POINTS,
        external_name="FREQUENCY_POINTS",
        label="扫频点数",
        group="basic",
        minimum=0,
        maximum=MAXIMUM_FREQUENCY_POINTS,
        read_only=True,
        description="由扫频起点、终点和每十倍频程点数自动计算。",
    )
    frequency_settle_s: float = parameter(
        default=0.1,
        external_name="FREQUENCY_SETTLE_S",
        label="切频稳定等待",
        unit="s",
        group="advanced",
        minimum=0.0,
        description="Z 输出关闭后等待信号源完成切频；实机不稳定时可调大。",
    )
    drive_amplitude_vpp: float = parameter(
        default=1.0,
        external_name="DRIVE_AMPLITUDE_VPP",
        label="低幅度正弦驱动",
        unit="Vpp",
        group="basic",
        minimum=0.000001,
    )
    scope_repeats: int = parameter(
        default=3,
        external_name="SCOPE_REPEATS",
        label="每个频率重复次数",
        group="basic",
        minimum=1,
        maximum=100,
        description="相位标准差判据需要至少两次重复；单次重复时该判据退化为无效。",
    )
    scope_sample_rate_sa_s: float = parameter(
        default=10000000.0,
        external_name="SCOPE_SAMPLE_RATE_SA_S",
        label="示波器最高采样率",
        unit="Sa/s",
        group="basic",
        minimum=1.0,
        description="动态采样率的上限；实际每个频率点的采样率会按频率降低。",
    )
    scope_min_sample_rate_sa_s: float = parameter(
        default=100000.0,
        external_name="SCOPE_MIN_SAMPLE_RATE_SA_S",
        label="低频最低采样率",
        unit="Sa/s",
        group="advanced",
        minimum=1000.0,
        description="低频点使用的最低采样率；高频点按每周期采样点数递增。",
    )
    scope_samples_per_cycle: int = parameter(
        default=10,
        external_name="SCOPE_SAMPLES_PER_CYCLE",
        label="每周期采样点数",
        group="advanced",
        minimum=8,
        maximum=4096,
        description="动态采样率的目标值；拟合只需要覆盖被测正弦的少量采样点。",
    )
    scope_capture_guard_s: float = parameter(
        default=0.1,
        external_name="SCOPE_CAPTURE_GUARD_S",
        label="帧完成保护等待",
        unit="s",
        group="advanced",
        minimum=0.0,
        description="SDS 完成深存储帧后的保护等待；若实机出现空帧应调大。",
    )
    scope_centered_capture: bool = parameter(
        default=True,
        external_name="SCOPE_CENTERED_CAPTURE",
        label="居中触发双倍记录窗",
        visible=False,
        description="SDS 把触发点放在记录窗中央，因此记录时长必须取所需触发后窗口的两倍。",
    )
    scope_reference_channel: int = parameter(
        default=1,
        external_name="SCOPE_REFERENCE_CHANNEL",
        label="驱动参考通道",
        group="advanced",
        minimum=0,
        maximum=4,
        description=(
            "Z 通道输出经 T 型接头分一路到该通道，作为相位与幅度参考；"
            "0 表示不使用参考通道。"
        ),
    )
    scope_reference_scale_v_div: float = parameter(
        default=0.5,
        external_name="SCOPE_REFERENCE_SCALE",
        label="参考通道量程",
        unit="V/div",
        group="advanced",
        minimum=0.000001,
        description="驱动幅度已知，参考通道使用固定量程，不参与自动量程。",
    )
    capture_margin_cycles: int = parameter(
        default=1,
        external_name="CAPTURE_MARGIN_CYCLES",
        label="记录窗保护周期数",
        visible=False,
        minimum=0,
        maximum=20,
        description="触发沿不一定落在采样点上，多记录一个周期保证丢弃窗口和待分析窗口都完整。",
    )
    response_settle_cycles: int = parameter(
        default=10,
        external_name="RESPONSE_SETTLE_CYCLES",
        label="启动后丢弃周期数",
        group="advanced",
        minimum=0,
        description="相干启动后丢弃的完整周期数，只分析之后的完整周期。",
    )
    sense_resistor_ohm: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_OHM",
        label="采样电阻实测阻值",
        unit="ohm",
        group="basic",
        minimum=0.0,
        description="必须填写万用表实测值；0 会在预检阶段阻止运行。",
    )
    sense_resistor_tolerance_percent: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_TOLERANCE_PERCENT",
        label="采样电阻误差",
        unit="%",
        group="advanced",
        minimum=0.0,
    )
    sense_resistor_power_rating_w: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_POWER_RATING_W",
        label="采样电阻额定功率",
        unit="W",
        group="basic",
        minimum=0.0,
        description="必须填写器件额定功率；0 会在预检阶段阻止运行。",
    )
    sense_resistor_power_derating: float = parameter(
        default=0.5,
        external_name="SENSE_RESISTOR_POWER_DERATING",
        label="采样电阻功率降额系数",
        group="advanced",
        minimum=0.01,
        maximum=1.0,
    )
    maximum_current_a: float = parameter(
        default=0.0,
        external_name="MAXIMUM_CURRENT_A",
        label="线圈峰值电流安全上限",
        unit="A",
        group="basic",
        minimum=0.0,
        description="必须由线圈和接线额定值确定；0 会阻止运行。",
    )
    coherent_rearm: bool = parameter(
        default=True,
        external_name="COHERENT_REARM",
        label="每帧相干重触发",
        visible=False,
    )

    @classmethod
    def migrate_external(
        cls, values: dict[str, object], schema_version: int,
    ) -> dict[str, object]:
        """逐谐波网格时代的键在扫频协议中不再有意义，只在入口丢弃。"""
        migrated = super(ZCoilCurrentFrequencyResponseParams, cls).migrate_external(
            values, schema_version,
        )
        if schema_version < 2:
            migrated = {
                key: value for key, value in migrated.items()
                if key not in {"CONTROL_VERSION", "CONTROL_RESULTS_ROOT"}
            }
        if schema_version < 4:
            migrated = {
                key: value for key, value in migrated.items()
                if key not in {"TARGET_REPEAT_FREQUENCY_HZ", "AW_POINTS"}
            }
        return migrated

    @classmethod
    def derive_external(cls, values: dict[str, object]) -> dict[str, object]:
        """按当前对数跨度刷新只读扫频点数。"""
        try:
            start = float(values["FREQUENCY_START_HZ"])
            stop = float(values["FREQUENCY_STOP_HZ"])
            density = int(values["FREQUENCY_POINTS_PER_DECADE"])
        except (KeyError, TypeError, ValueError):
            return {}
        if not (math.isfinite(start) and math.isfinite(stop)) or start <= 0.0:
            return {"FREQUENCY_POINTS": 0}
        if stop <= start or density < 2:
            return {"FREQUENCY_POINTS": 0}
        return {
            "FREQUENCY_POINTS": frequency_points_for(start, stop, density),
        }

    def frequency_axis(self) -> np.ndarray:
        """返回对数等间隔扫频轴。

        点数始终按派生规则重新计算，不读取只读字段，避免历史默认 YAML
        里遗留的点数覆盖实际扫描轴。
        """
        points = frequency_points_for(
            self.frequency_start_hz,
            self.frequency_stop_hz,
            self.frequency_points_per_decade,
        )
        return np.logspace(
            math.log10(self.frequency_start_hz),
            math.log10(self.frequency_stop_hz),
            points,
            dtype=float,
        )

    def validate_model(self) -> list[str]:
        errors = ZCoilInductanceFrequencyResponseParams.validate_model(self)
        if self.scope_measured_channel != 3:
            errors.append("SCOPE_MEASURED_CHANNEL 必须固定为 CH3 采样电阻电压")
        if self.scope_trigger_channel != 4:
            errors.append("SCOPE_TRIGGER_CHANNEL 必须固定为 CH4 共同触发")
        try:
            validate_sense_resistor(
                self.sense_resistor_ohm,
                self.sense_resistor_power_rating_w,
                tolerance_percent=self.sense_resistor_tolerance_percent,
            )
        except ValueError as exc:
            errors.append(str(exc))
        if self.maximum_current_a <= 0.0:
            errors.append("MAXIMUM_CURRENT_A 必须填写线圈峰值电流安全上限")
        if self.scope_reference_channel not in (0, 1, 2, 3, 4):
            errors.append("SCOPE_REFERENCE_CHANNEL 必须是 0（禁用）或 1-4")
        elif self.scope_reference_channel and self.scope_reference_channel in {
            self.scope_measured_channel, self.scope_trigger_channel,
        }:
            errors.append("SCOPE_REFERENCE_CHANNEL 不能与测量通道或触发通道相同")
        if not self.coherent_rearm:
            errors.append("COHERENT_REARM 必须开启，复数传递函数需要相干重触发")
        points = frequency_points_for(
            self.frequency_start_hz,
            self.frequency_stop_hz,
            self.frequency_points_per_decade,
        )
        if points < 2:
            errors.append("对数扫频区间至少需要两个频率点")
        elif points > MAXIMUM_FREQUENCY_POINTS:
            errors.append(
                f"扫频点数 {points} 超过上限 {MAXIMUM_FREQUENCY_POINTS}，"
                "请降低每十倍频程点数或收窄频率范围"
            )
        elif self.frequency_points != points:
            errors.append(f"FREQUENCY_POINTS 应为当前对数区间的点数 {points}")
        if self.scope_sample_rate_sa_s <= 2.0 * self.frequency_stop_hz:
            errors.append("SCOPE_SAMPLE_RATE_SA_S 的 Nyquist 必须高于扫频终点")
        if self.scope_min_sample_rate_sa_s > self.scope_sample_rate_sa_s:
            errors.append(
                "SCOPE_MIN_SAMPLE_RATE_SA_S 不能高于 SCOPE_SAMPLE_RATE_SA_S"
            )
        required_peak_rate = self.scope_samples_per_cycle * self.frequency_stop_hz
        if self.scope_sample_rate_sa_s < required_peak_rate:
            errors.append(
                "SCOPE_SAMPLE_RATE_SA_S 低于终点频率按 "
                f"SCOPE_SAMPLES_PER_CYCLE={self.scope_samples_per_cycle} "
                f"计算的 {required_peak_rate:.6g} Sa/s"
            )
        return errors
