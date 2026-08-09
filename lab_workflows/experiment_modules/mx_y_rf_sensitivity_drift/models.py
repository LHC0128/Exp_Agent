"""Mx Y RF 灵敏度长飘参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...experiment_params import parameter
from ..mx_y_rf_sensitivity.models import LinewidthMode, MxYRFParams


@dataclass(slots=True)
class MxYRFDriftParams(MxYRFParams):
    """复用 Mx Y RF 单轮测量参数并增加长飘调度参数。"""

    schema_version = 1

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
        offset = self.drift_interval_s if not self.drift_start_immediately else 0.0
        if offset >= self.drift_duration_s:
            return 0
        return max(
            1,
            int(math.ceil((self.drift_duration_s - offset) / self.drift_interval_s)),
        )
