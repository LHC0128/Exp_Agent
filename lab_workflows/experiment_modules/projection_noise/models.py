"""SDS 原始 PD 自旋投影噪声实验参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from .calibration import load_main_field_calibration


@dataclass(slots=True)
class ProjectionNoiseParams(ExperimentParams):
    """GS200 打开阶段及可选关闭对照组的 PD 原始噪声谱采集参数。"""

    schema_version = 5

    run_tag: str = parameter(default="scope_spin_noise", external_name="RUN_TAG", label="运行标签", group="basic")
    target_larmor_frequency_hz: float = parameter(default=90000.0, external_name="TARGET_LARMOR_FREQUENCY_HZ", label="目标 Larmor 频率", unit="Hz", group="basic", minimum=1.0)
    main_field_calibration_source_run: str = parameter(default="0720_124208_mx_main_field_cal", external_name="MAIN_FIELD_CALIBRATION_SOURCE_RUN", label="主场标定来源运行", group="basic")
    main_field_settle_s: float = parameter(default=1.0, external_name="MAIN_FIELD_SETTLE_S", label="主场打开后等待", unit="s", group="basic", minimum=0.0)
    measure_field_off_control: bool = parameter(
        default=True,
        external_name="MEASURE_FIELD_OFF_CONTROL",
        label="测量关闭电流源对照组",
        group="basic",
        description="开启时先采集 GS200 输出关闭背景；关闭时仅采集主场打开数据。",
    )

    scope_sample_rate_sa_s: float = parameter(default=500000.0, external_name="SCOPE_SAMPLE_RATE", label="示波器请求采样率", unit="Sa/s", group="basic", minimum=1.0)
    scope_duration_s: float = parameter(default=1.0, external_name="SCOPE_DURATION", label="单帧记录时长", unit="s", group="basic", minimum=0.001)
    acq_repeats: int = parameter(default=100, external_name="ACQ_REPEATS", label="每阶段采集帧数", group="basic", minimum=1)
    scope_pd_channel: int = parameter(default=1, external_name="SCOPE_PD_CHANNEL", label="PD 示波器通道", visible=False, minimum=1, maximum=4)
    scope_trigger_mode: Literal["AUTO"] = parameter(default="AUTO", external_name="SCOPE_TRIGGER_MODE", label="示波器触发模式", visible=False)
    scope_initial_scale_v_div: float = parameter(default=0.4, external_name="SCOPE_INITIAL_SCALE", label="示波器初始量程", unit="V/div", group="advanced", minimum=0.000001)
    scope_offset_v: float = parameter(default=0.0, external_name="SCOPE_OFFSET", label="示波器通道偏置", unit="V", visible=False)
    scope_vertical_divisions: int = parameter(default=8, external_name="SCOPE_VERTICAL_DIVISIONS", label="自动量程垂直总格数", visible=False, minimum=1)
    scope_scale_min_v_div: float = parameter(default=0.01, external_name="SCOPE_SCALE_MIN", label="自动量程下限", unit="V/div", visible=False, minimum=0.000001)
    scope_scale_max_v_div: float = parameter(default=10.0, external_name="SCOPE_SCALE_MAX", label="自动量程上限", unit="V/div", visible=False, minimum=0.000001)
    scope_auto_range_low_fraction: float = parameter(default=0.4, external_name="SCOPE_AUTO_RANGE_LOW_FRACTION", label="自动量程缩小阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_high_fraction: float = parameter(default=0.9, external_name="SCOPE_AUTO_RANGE_HIGH_FRACTION", label="自动量程放大阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_offset_tolerance_fraction: float = parameter(default=0.2, external_name="SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", label="自动偏置居中死区", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_max_attempts: int = parameter(default=3, external_name="SCOPE_AUTO_RANGE_MAX_ATTEMPTS", label="自动量程最大尝试次数", visible=False, minimum=1)
    scope_record_max_attempts: int = parameter(default=3, external_name="SCOPE_RECORD_MAX_ATTEMPTS", label="完整帧最大重试次数", visible=False, minimum=1)

    welch_nperseg: int = parameter(default=50000, external_name="WELCH_NPERSEG", label="Welch 每段点数", group="advanced", minimum=8)
    fit_half_width_hz: float = parameter(default=5000.0, external_name="FIT_HALF_WIDTH_HZ", label="拟合频带半宽", unit="Hz", group="basic", minimum=1.0)

    temp_switch_off_settle_s: float = parameter(default=0.3, external_name="TEMP_SWITCH_OFF_SETTLE_S", label="温控关闭后等待", unit="s", group="basic", minimum=0.0)
    temp_switch_on_settle_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_SETTLE_S", label="温控恢复后等待", unit="s", group="basic", minimum=0.0)
    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="初始温度稳定容差", unit="°C", group="advanced", minimum=0.0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="初始温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1.0)

    pump_laser_power_v: float = parameter(default=0.0, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", minimum=0.0, maximum=1.0, safety_key="Pump_laser_power")
    probe_laser_power_v: float = parameter(default=0.3, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature_c: float = parameter(default=120.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")

    @property
    def scope_requested_points(self) -> int:
        return int(self.scope_sample_rate_sa_s * self.scope_duration_s)

    @classmethod
    def migrate_external(
        cls, values: dict[str, Any], schema_version: int
    ) -> dict[str, Any]:
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 "
                f"{cls.schema_version}"
            )
        migrated = dict(values)
        if (
            schema_version <= 2
            and migrated.get("SCOPE_VERTICAL_DIVISIONS") == 4
        ):
            migrated["SCOPE_VERTICAL_DIVISIONS"] = 8
        fixed = migrated.pop("FIXED_PARAMS", None)
        if isinstance(fixed, dict):
            for key, value in fixed.items():
                migrated.setdefault(f"FIXED_PARAMS.{key}", value)
        aliases = {
            "PROBE_POWER": "FIXED_PARAMS.Probe_laser_power",
            "ACQ_DURATION": "SCOPE_DURATION",
            "NPERSEG": "WELCH_NPERSEG",
        }
        for old, new in aliases.items():
            if old in migrated and new not in migrated:
                migrated[new] = migrated[old]
            migrated.pop(old, None)
        if "MAIN_FIELD_NORMAL_mA" in migrated and "TARGET_LARMOR_FREQUENCY_HZ" not in migrated:
            source = str(
                migrated.get(
                    "MAIN_FIELD_CALIBRATION_SOURCE_RUN",
                    "0720_124208_mx_main_field_cal",
                )
            )
            calibration = load_main_field_calibration(find_project_root(), source)
            migrated["TARGET_LARMOR_FREQUENCY_HZ"] = (
                calibration.slope_hz_per_ma * float(migrated["MAIN_FIELD_NORMAL_mA"])
                + calibration.intercept_hz
            )
        obsolete = {
            "MAIN_FIELD_NORMAL_mA",
            "MAIN_FIELD_OFFSET_mA",
            "PSD_INTEG_FMIN",
            "PSD_INTEG_FMAX",
            "NOTCH_FREQS",
            "NOTCH_WIDTH",
            "PEAK_THRESHOLD",
            "PEAK_DILATE",
            "F1_CORRECTION",
            "PNL_THERMAL_RATIO",
            "HF2_DEMOD_IDX",
            "HF2_OSC_FREQ",
            "HF2_SIGNAL_RANGE",
            "HF2_DEMOD_ORDER",
            "HF2_DEMOD_TC",
            "HF2_DEMOD_RATE",
        }
        for key in obsolete:
            migrated.pop(key, None)
        return migrated

    def calibration(self):
        return load_main_field_calibration(
            find_project_root(), self.main_field_calibration_source_run
        )

    def target_current_ma(self) -> float:
        return self.calibration().current_for_frequency(
            self.target_larmor_frequency_hz
        )

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.main_field_calibration_source_run.strip():
            errors.append("MAIN_FIELD_CALIBRATION_SOURCE_RUN 不能为空")
        if self.scope_requested_points < 8:
            errors.append("示波器请求采样点数必须至少为 8")
        if self.welch_nperseg > self.scope_requested_points:
            errors.append("WELCH_NPERSEG 不能超过示波器请求采样点数")
        if self.target_larmor_frequency_hz + self.fit_half_width_hz >= self.scope_sample_rate_sa_s / 2.0:
            errors.append("目标频率加拟合半宽必须低于示波器请求采样率的 Nyquist 频率")
        if self.fit_half_width_hz >= self.target_larmor_frequency_hz:
            errors.append("FIT_HALF_WIDTH_HZ 必须小于目标 Larmor 频率")
        if not (
            self.scope_scale_min_v_div
            <= self.scope_initial_scale_v_div
            <= self.scope_scale_max_v_div
        ):
            errors.append("示波器初始量程必须位于自动量程上下限内")
        if self.scope_scale_min_v_div >= self.scope_scale_max_v_div:
            errors.append("SCOPE_SCALE_MIN 必须小于 SCOPE_SCALE_MAX")
        if not (
            0.0
            < self.scope_auto_range_low_fraction
            < self.scope_auto_range_high_fraction
            < 1.0
        ):
            errors.append("自动量程阈值必须满足 0 < LOW < HIGH < 1")
        if not 0.0 <= self.scope_auto_offset_tolerance_fraction < 1.0:
            errors.append("自动偏置居中死区必须满足 0 <= TOLERANCE < 1")
        try:
            current_ma = self.target_current_ma()
            validate_safety_limit("main_magnetic_field", current_ma)
        except (FileNotFoundError, TypeError, ValueError) as exc:
            errors.append(str(exc))
        return errors
