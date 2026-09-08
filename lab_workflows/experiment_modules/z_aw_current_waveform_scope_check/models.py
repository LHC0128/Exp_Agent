"""Z 任意波实际电流波形验证参数。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ...common import find_project_root, validate_safety_limit
from ...current_feedback import (
    load_corrected_control_waveform,
    load_current_coupling_calibration,
    target_current_from_omega,
    validate_current_power,
    validate_sense_resistor,
)
from ...experiment_params import parameter
from ..mx_z_optimal_control_rf_sensitivity.sources import load_theory_control
from ..z_aw_waveform_scope_check.models import ZAWWaveformScopeCheckParams


@dataclass(slots=True)
class ZAWCurrentWaveformScopeCheckParams(ZAWWaveformScopeCheckParams):
    """使用采样电阻电压比较目标和实际线圈电流。"""

    schema_version = 1

    run_tag: str = parameter(
        default="z_aw_current_waveform_scope_check",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    control_waveform_source: str = parameter(
        default="theory",
        external_name="CONTROL_WAVEFORM_SOURCE",
        label="验证波形来源",
        group="basic",
        options=(("theory", "理论换算"), ("corrected_run", "闭环冻结波形")),
    )
    corrected_control_source_run: str = parameter(
        default="",
        external_name="CORRECTED_CONTROL_SOURCE_RUN",
        label="闭环校正运行",
        group="basic",
    )
    command_z_calibration_source_run: str = parameter(
        default="0730_170249_mx_z_cal",
        external_name="COMMAND_Z_CALIBRATION_SOURCE_RUN",
        label="命令电压标定来源",
        group="advanced",
    )
    current_coupling_calibration_source_run: str = parameter(
        default="",
        external_name="CURRENT_COUPLING_CALIBRATION_SOURCE_RUN",
        label="电流耦合标定来源",
        group="basic",
        description="必须填写 mx-z-current-coupling-calibration 的运行目录名。",
    )
    z_calibration_source_run: str = parameter(
        default="0730_170249_mx_z_cal",
        external_name="Z_CALIBRATION_SOURCE_RUN",
        label="兼容命令电压标定来源",
        visible=False,
    )
    sense_resistor_ohm: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_OHM",
        label="采样电阻实测阻值",
        unit="ohm",
        group="basic",
        minimum=0.0,
    )
    sense_resistor_power_rating_w: float = parameter(
        default=0.0,
        external_name="SENSE_RESISTOR_POWER_RATING_W",
        label="采样电阻额定功率",
        unit="W",
        group="basic",
        minimum=0.0,
    )
    maximum_current_a: float = parameter(
        default=0.0,
        external_name="MAXIMUM_CURRENT_A",
        label="线圈峰值电流安全上限",
        unit="A",
        group="basic",
        minimum=0.0,
    )

    def validate_model(self) -> list[str]:
        if self.control_waveform_source == "theory":
            command_params = replace(
                self,
                z_calibration_source_run=self.command_z_calibration_source_run,
            )
            errors = ZAWWaveformScopeCheckParams.validate_model(command_params)
        else:
            errors = []
            if self.control_waveform_source != "corrected_run":
                errors.append("CONTROL_WAVEFORM_SOURCE 必须是 theory 或 corrected_run")
            if not self.run_tag.strip():
                errors.append("RUN_TAG 不能为空")
            if self.scope_measured_channel == self.scope_trigger_channel:
                errors.append("SCOPE_MEASURED_CHANNEL 与 SCOPE_TRIGGER_CHANNEL 必须不同")
            if self.scope_scale_min_v_div > self.scope_scale_max_v_div:
                errors.append("SCOPE_SCALE_MIN 必须不大于 SCOPE_SCALE_MAX")
            try:
                trigger_low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2.0
                trigger_high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2.0
                for value in (trigger_low, trigger_high, self.scope_trigger_level_v):
                    validate_safety_limit("Time_sequence_2", value)
            except ValueError as exc:
                errors.append(str(exc))
        if self.scope_measured_channel != 3:
            errors.append("SCOPE_MEASURED_CHANNEL 必须固定为 CH3 采样电阻电压")
        if self.scope_trigger_channel != 4:
            errors.append("SCOPE_TRIGGER_CHANNEL 必须固定为 CH4 共同触发")
        if not self.current_coupling_calibration_source_run.strip():
            errors.append("CURRENT_COUPLING_CALIBRATION_SOURCE_RUN 不能为空")
        try:
            validate_sense_resistor(self.sense_resistor_ohm, self.sense_resistor_power_rating_w)
        except ValueError as exc:
            errors.append(str(exc))
        if self.maximum_current_a <= 0.0:
            errors.append("MAXIMUM_CURRENT_A 必须填写正安全上限")
        try:
            calibration = load_current_coupling_calibration(
                find_project_root(), self.current_coupling_calibration_source_run
            )
            if self.control_waveform_source == "corrected_run":
                corrected = load_corrected_control_waveform(
                    find_project_root(), self.corrected_control_source_run
                )
                if corrected.coupling_calibration_run != calibration.run_name:
                    errors.append("冻结波形与选择的电流耦合标定运行不一致")
                if corrected.coupling_calibration_sha256 != calibration.analysis_sha256:
                    errors.append("冻结波形与当前电流耦合标定文件哈希不一致")
                for value in (
                    float(np.min(corrected.voltage_v)),
                    float(np.max(corrected.voltage_v)),
                    corrected.offset_v - corrected.amplitude_vpp / 2.0,
                    corrected.offset_v + corrected.amplitude_vpp / 2.0,
                ):
                    validate_safety_limit("Z_magnetic_field", value)
                target_current = corrected.target_current_a
            else:
                theory = load_theory_control(
                    Path(self.control_results_root), self.control_version
                )
                target_current = target_current_from_omega(
                    self.control_scale * theory.omega_ctrl_hz,
                    calibration,
                )
            if not np.isclose(
                calibration.sense_resistor_ohm,
                self.sense_resistor_ohm,
                rtol=1e-6,
                atol=1e-12,
            ):
                errors.append("采样电阻与电流耦合标定来源不一致")
            validate_current_power(
                target_current,
                self.sense_resistor_ohm,
                self.sense_resistor_power_rating_w,
                derating_fraction=0.5,
                maximum_current_a=self.maximum_current_a,
            )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
