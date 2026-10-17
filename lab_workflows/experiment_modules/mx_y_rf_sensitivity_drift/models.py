"""Mx Y RF 灵敏度长飘参数。"""

from __future__ import annotations
from typing import ClassVar

import math
from dataclasses import dataclass

from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import LinewidthMode, MxYRFParams


@dataclass(slots=True)
class MxYRFDriftParams(MxYRFParams):
    """复用 Mx Y RF 单轮测量参数并增加长飘调度参数。"""

    schema_version = 2

    system_settle_legacy_defaults: ClassVar[dict[str, float]] = {'RESPONSE_SETTLE_TIME_S': 0.3, 'FREQUENCY_SETTLE_TIME_S': 0.05, 'TEMP_SWITCH_OFF_LEAD_S': 0.1}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ('TEMPERATURE_TOLERANCE_C', 'TEMPERATURE_STABLE_READS', 'TEMPERATURE_POLL_INTERVAL_S', 'TEMPERATURE_TIMEOUT_S')
    system_settle_s: float = parameter(default=0.3, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", group="basic", minimum=0, description="配置和温控门控共同稳定的采集前等待；组内每次换点或换态后重新等待。")
    run_tag: str = parameter(
        default="mx_y_rf_drift",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    linewidth_mode: LinewidthMode = parameter(
        default="amplitude_equivalent",
        external_name="LINEWIDTH_MODE",
        label="线宽模式",
        visible=False,
    )
    # 长飘固定幅度等效模式；这些历史键不进入实际准备、采集或子分析。
    frequency_start_hz: float = parameter(default=8000.0, external_name="FREQUENCY_START_HZ", label="模式1扫频起点", unit="Hz", group="basic", minimum=0.001, visible=False)
    frequency_stop_hz: float = parameter(default=12000.0, external_name="FREQUENCY_STOP_HZ", label="模式1扫频终点", unit="Hz", group="basic", minimum=0.001, visible=False)
    frequency_points: int = parameter(default=201, external_name="FREQUENCY_POINTS", label="模式1扫频点数", group="basic", minimum=5, visible=False)
    frequency_duration_s: float = parameter(default=0.1, external_name="FREQUENCY_DURATION_S", label="模式1频点采样时间", unit="s", group="advanced", minimum=0.001, visible=False)
    frequency_rf_amplitude_vpp: float = parameter(default=0.01, external_name="FREQUENCY_RF_AMPLITUDE_VPP", label="Y RF 正弦初始幅度", unit="Vpp", group="basic", minimum=0, maximum=2.0, safety_key="rf_coil", description="用于准备正弦模式；长飘不执行模式1扫频。")
    drift_interval_s: float = parameter(
        default=1800.0,
        external_name="DRIFT_INTERVAL_S",
        label="长飘轮次间隔",
        unit="s",
        group="basic",
        minimum=1.0,
    )
    drift_duration_s: float = parameter(
        default=86400.0,
        external_name="DRIFT_DURATION_S",
        label="长飘运行时长",
        unit="s",
        group="basic",
        minimum=1.0,
    )
    drift_start_immediately: bool = parameter(
        default=True,
        external_name="DRIFT_START_IMMEDIATELY",
        label="立即执行首轮",
        group="basic",
    )

    def validate_model(self) -> list[str]:
        errors = MxYRFParams.validate_model(self)
        if self.linewidth_mode != "amplitude_equivalent":
            errors.append("LINEWIDTH_MODE 必须固定为 amplitude_equivalent")
        if not math.isfinite(self.drift_interval_s) or self.drift_interval_s <= 0:
            errors.append("DRIFT_INTERVAL_S 必须为正数")
        if not math.isfinite(self.drift_duration_s) or self.drift_duration_s <= 0:
            errors.append("DRIFT_DURATION_S 必须为正数")
        if self.y_rf_nt_per_vpp <= 0:
            errors.append("Y_RF_NT_PER_VPP 必须大于 0 才能输出磁场灵敏度")
        if self.planned_cycle_count <= 0:
            errors.append("当前长飘时长、间隔和首轮设置不会产生任何测量轮次")
        return errors

    @property
    def planned_cycle_count(self) -> int:
        """返回半开时间区间内的计划轮数。"""
        if not all(math.isfinite(value) and value > 0 for value in (self.drift_interval_s, self.drift_duration_s)):
            return 0
        offset = self.drift_interval_s if not self.drift_start_immediately else 0.0
        if offset >= self.drift_duration_s:
            return 0
        count = int(math.ceil((self.drift_duration_s - offset) / self.drift_interval_s))
        if offset + (count - 1) * self.drift_interval_s >= self.drift_duration_s:
            count -= 1
        return count
