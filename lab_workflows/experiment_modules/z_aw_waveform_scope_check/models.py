"""Z 任意波线圈波形一致性验证参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ...control_sources import (
    build_applied_control,
    load_theory_control,
    load_z_calibration,
)


@dataclass(slots=True)
class ZAWWaveformScopeCheckParams(ExperimentParams):
    """只驱动 Z 任意波和共同触发并采集 SDS CH3/CH4。"""

    schema_version = 1

    run_tag: str = parameter(
        default="z_aw_waveform_scope_check",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    control_version: str = parameter(
        default="v2",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
        description="按 vN 读取同版本的最优控制波形和理论参数。",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录",
        group="advanced",
    )
    z_calibration_source_run: str = parameter(
        default="0730_170249_mx_z_cal",
        external_name="Z_CALIBRATION_SOURCE_RUN",
        label="Z 标定来源运行",
        group="basic",
    )
    control_scale: float = parameter(
        default=1.0,
        external_name="CONTROL_SCALE",
        label="控制幅度比例",
        group="basic",
        minimum=0.000001,
    )
    z_aw_output_vpp: float = parameter(
        default=6.0,
        external_name="Z_AW_OUTPUT_VPP",
        label="Z 控制 AW 输出幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
    )
    z_aw_output_offset_v: float = parameter(
        default=0.0,
        external_name="Z_AW_OUTPUT_OFFSET",
        label="Z 控制 AW 输出偏置",
        unit="V",
        group="advanced",
    )
    control_burst_phase_deg: float = parameter(
        default=0.0,
        external_name="CONTROL_BURST_PHASE_DEG",
        label="Z 控制 Burst 相位",
        unit="deg",
        group="advanced",
        minimum=0.0,
        maximum=360.0,
    )
    trigger_frequency_hz: float = parameter(
        default=100.0,
        external_name="TRIGGER_FREQUENCY_HZ",
        label="共同触发频率",
        unit="Hz",
        group="basic",
        minimum=0.001,
    )
    trigger_amplitude_vpp: float = parameter(
        default=5.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="basic",
        minimum=0.001,
        maximum=20.0,
    )
    trigger_offset_v: float = parameter(
        default=2.5,
        external_name="TRIGGER_OFFSET_V",
        label="共同触发偏置",
        unit="V",
        group="advanced",
        minimum=-10.0,
        maximum=10.0,
    )
    trigger_duty_percent: float = parameter(
        default=50.0,
        external_name="TRIGGER_DUTY_PERCENT",
        label="共同触发占空比",
        unit="%",
        group="advanced",
        minimum=0.1,
        maximum=99.9,
    )
    scope_cycles: int = parameter(
        default=3,
        external_name="SCOPE_CYCLES",
        label="每次采集周期数",
        group="basic",
        minimum=2,
    )
    scope_repeats: int = parameter(
        default=5,
        external_name="SCOPE_REPEATS",
        label="重复采集次数",
        group="basic",
        minimum=1,
    )

    # 以下字段固定为本实验接线角色，仅写入配置快照，不展示为 GUI 参数。
    scope_measured_channel: int = parameter(
        default=3,
        external_name="SCOPE_MEASURED_CHANNEL",
        label="线圈测量通道",
        visible=False,
        minimum=1,
        maximum=4,
    )
    scope_trigger_channel: int = parameter(
        default=4,
        external_name="SCOPE_TRIGGER_CHANNEL",
        label="触发参考通道",
        visible=False,
        minimum=1,
        maximum=4,
    )
    scope_initial_scale_v_div: float = parameter(
        default=1.0,
        external_name="SCOPE_INITIAL_SCALE",
        label="示波器初始量程",
        unit="V/div",
        visible=False,
        minimum=0.000001,
    )
    scope_scale_min_v_div: float = parameter(
        default=0.01,
        external_name="SCOPE_SCALE_MIN",
        label="示波器量程下限",
        unit="V/div",
        visible=False,
        minimum=0.000001,
    )
    scope_scale_max_v_div: float = parameter(
        default=10.0,
        external_name="SCOPE_SCALE_MAX",
        label="示波器量程上限",
        unit="V/div",
        visible=False,
        minimum=0.000001,
    )
    scope_vertical_divisions: int = parameter(
        default=8,
        external_name="SCOPE_VERTICAL_DIVISIONS",
        label="示波器垂直总格数",
        visible=False,
        minimum=1,
    )
    scope_auto_range_low_fraction: float = parameter(
        default=0.4,
        external_name="SCOPE_AUTO_RANGE_LOW_FRACTION",
        label="量程缩小阈值",
        visible=False,
        minimum=0.0,
        maximum=1.0,
    )
    scope_auto_range_high_fraction: float = parameter(
        default=0.9,
        external_name="SCOPE_AUTO_RANGE_HIGH_FRACTION",
        label="量程放大阈值",
        visible=False,
        minimum=0.0,
        maximum=1.0,
    )
    scope_auto_range_max_attempts: int = parameter(
        default=3,
        external_name="SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
        label="量程调整最大尝试次数",
        visible=False,
        minimum=1,
    )
    scope_trigger_level_v: float = parameter(
        default=2.5,
        external_name="SCOPE_TRIGGER_LEVEL_V",
        label="示波器触发电平",
        unit="V",
        visible=False,
    )

    @classmethod
    def derive_external(cls, values: dict[str, object]) -> dict[str, object]:
        """在 GUI 派生显示中报告理论周期和预计采集时长。"""
        try:
            theory = load_theory_control(
                Path(str(values.get("CONTROL_RESULTS_ROOT", ""))),
                str(values.get("CONTROL_VERSION", "v2")),
            )
        except (OSError, TypeError, ValueError, KeyError):
            return {}
        period_s = 1.0 / theory.repeat_frequency_hz
        repeats = int(values.get("SCOPE_REPEATS", 5))
        cycles = int(values.get("SCOPE_CYCLES", 3))
        return {
            "CONTROL_REPEAT_FREQUENCY_HZ": theory.repeat_frequency_hz,
            "SCOPE_DURATION_S": cycles * period_s,
            "TOTAL_CAPTURE_DURATION_S": repeats * cycles * period_s,
        }

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.scope_measured_channel == self.scope_trigger_channel:
            errors.append("SCOPE_MEASURED_CHANNEL 与 SCOPE_TRIGGER_CHANNEL 必须不同")
        if self.scope_scale_min_v_div > self.scope_scale_max_v_div:
            errors.append("SCOPE_SCALE_MIN 必须不大于 SCOPE_SCALE_MAX")
        if not np.isclose(self.scope_trigger_level_v, 2.5, atol=1e-12):
            errors.append("SCOPE_TRIGGER_LEVEL_V 必须为 2.5 V")
        try:
            trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
            trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
            validate_safety_limit("Time_sequence_2", trigger_low)
            validate_safety_limit("Time_sequence_2", trigger_high)
            validate_safety_limit("Time_sequence_2", self.scope_trigger_level_v)
        except ValueError as exc:
            errors.append(str(exc))

        root = find_project_root()
        try:
            theory = load_theory_control(
                Path(self.control_results_root), self.control_version
            )
            calibration = load_z_calibration(
                root, self.z_calibration_source_run
            )
            build_applied_control(
                theory,
                calibration,
                self.control_scale,
                output_vpp=self.z_aw_output_vpp,
                output_offset_v=self.z_aw_output_offset_v,
            )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
