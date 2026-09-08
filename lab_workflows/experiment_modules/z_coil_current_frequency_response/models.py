"""Z 线圈实际电流频率响应参数。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...current_feedback import validate_sense_resistor
from ...experiment_params import parameter
from ..z_coil_inductance_frequency_response.models import (
    ZCoilInductanceFrequencyResponseParams,
)
from ..mx_z_optimal_control_rf_sensitivity.sources import load_theory_control


@dataclass(slots=True)
class ZCoilCurrentFrequencyResponseParams(ZCoilInductanceFrequencyResponseParams):
    """以低端采样电阻电压重建线圈电流的相干正弦扫频。"""

    schema_version = 1

    run_tag: str = parameter(
        default="z_coil_current_frequency_response",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )
    frequency_stop_hz: float = parameter(
        default=120000.0,
        external_name="FREQUENCY_STOP_HZ",
        label="扫频终点",
        unit="Hz",
        group="basic",
        minimum=0.001,
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
    frequency_points: int = parameter(
        default=49,
        external_name="FREQUENCY_POINTS",
        label="扫频点数",
        group="basic",
        minimum=4,
    )
    drive_amplitude_vpp: float = parameter(
        default=0.1,
        external_name="DRIVE_AMPLITUDE_VPP",
        label="低幅度正弦驱动",
        unit="Vpp",
        group="basic",
        minimum=0.000001,
    )
    control_version: str = parameter(
        default="v2",
        external_name="CONTROL_VERSION",
        label="带宽参考控制版本",
        group="basic",
    )
    control_results_root: str = parameter(
        default=r"D:\Code\theory_agent\simulate\results\oc_sens",
        external_name="CONTROL_RESULTS_ROOT",
        label="最优控制结果根目录",
        group="advanced",
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
        default=64,
        external_name="SCOPE_SAMPLES_PER_CYCLE",
        label="每周期采样点数",
        group="advanced",
        minimum=8,
        maximum=4096,
        description="动态采样率的目标值；64 点/周期足以进行正弦幅值和相位拟合。",
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
        if not self.coherent_rearm:
            errors.append("COHERENT_REARM 必须开启，复数传递函数需要相干重触发")
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
        try:
            theory = load_theory_control(
                Path(self.control_results_root), self.control_version
            )
            step_s = float(np.median(np.diff(theory.time_s)))
            frequencies = np.fft.rfftfreq(theory.time_s.size, d=step_s)
            energy = np.abs(np.fft.rfft(theory.omega_ctrl_hz)) ** 2
            cumulative = np.cumsum(energy)
            index = int(np.searchsorted(cumulative, 0.99 * cumulative[-1]))
            required_stop_hz = float(frequencies[min(index, frequencies.size - 1)])
            if self.frequency_stop_hz < required_stop_hz:
                errors.append(
                    f"FREQUENCY_STOP_HZ={self.frequency_stop_hz:.6g} Hz 未覆盖 "
                    f"{self.control_version} 控制波形 99% 频谱能量上限 "
                    f"{required_stop_hz:.6g} Hz"
                )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(str(exc))
        return errors
