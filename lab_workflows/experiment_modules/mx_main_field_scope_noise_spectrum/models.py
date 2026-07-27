"""Mx 主磁场示波器噪声谱参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class MxMainFieldScopeNoiseSpectrumParams(ExperimentParams):
    """扫描主场对应的 Larmor 频率并采集 PD 示波器原始波形。"""

    schema_version = 7

    run_tag: str = parameter(default="mx_main_field_scope_noise", external_name="RUN_TAG", label="运行标签", group="basic")
    control_frequency_start_hz: float = parameter(default=0.0, external_name="CONTROL_FREQUENCY_START_HZ", label="控制频率起点", unit="Hz", group="basic", minimum=0.0)
    control_frequency_stop_hz: float = parameter(default=50000.0, external_name="CONTROL_FREQUENCY_STOP_HZ", label="控制频率终点", unit="Hz", group="basic", minimum=0.0)
    control_frequency_points: int = parameter(default=500, external_name="CONTROL_FREQUENCY_POINTS", label="控制频率点数", group="basic", minimum=10)
    main_field_calibration_hz_per_ma: float = parameter(default=9671.91741380711, external_name="MAIN_FIELD_CALIBRATION_HZ_PER_MA", label="主场标定斜率", unit="Hz/mA", group="basic", minimum=0.001)
    main_field_calibration_intercept_hz: float = parameter(default=196.65637261343872, external_name="MAIN_FIELD_CALIBRATION_INTERCEPT_HZ", label="主场标定截距", unit="Hz", group="basic")
    main_field_calibration_source_run: str = parameter(default="0720_124208_mx_main_field_cal", external_name="MAIN_FIELD_CALIBRATION_SOURCE_RUN", label="主场标定来源运行", group="advanced")
    x_dc_field_v: float = parameter(default=-0.015, external_name="FIXED_PARAMS.X_magnetic_field", label="X 方向 DC 补偿场", unit="V", group="basic", safety_key="X_magnetic_field", description="设为 0 V 时关闭输出；非零时输出所设电压并在扫描期间保持开启。")
    y_dc_field_v: float = parameter(default=-0.004, external_name="FIXED_PARAMS.Y_magnetic_field", label="Y 方向 DC 补偿场", unit="V", group="basic", safety_key="Y_magnetic_field", description="设为 0 V 时关闭输出；非零时输出所设电压并在扫描期间保持开启。")

    scope_sample_rate_sa_s: float = parameter(default=200000.0, external_name="SCOPE_SAMPLE_RATE", label="示波器请求采样率", unit="Sa/s", group="basic", minimum=1.0)
    scope_duration_s: float = parameter(default=1.0, external_name="SCOPE_DURATION", label="每点记录时长", unit="s", group="basic", minimum=0.001)
    scope_ac_coupling: bool = parameter(default=False, external_name="SCOPE_AC_COUPLING", label="示波器交流耦合", group="basic", description="开启时使用 AC 耦合；关闭时使用 DC 耦合。实验结束后统一恢复为 DC 耦合。")
    scope_pd_channel: int = parameter(default=1, external_name="SCOPE_PD_CHANNEL", label="PD 示波器通道", visible=False, minimum=1, maximum=4)
    scope_trigger_mode: Literal["AUTO"] = parameter(default="AUTO", external_name="SCOPE_TRIGGER_MODE", label="示波器触发模式", visible=False)
    scope_initial_scale_v_div: float = parameter(default=0.5, external_name="SCOPE_INITIAL_SCALE", label="示波器初始量程", unit="V/div", group="advanced", minimum=0.000001)
    scope_offset_v: float = parameter(default=0.0, external_name="SCOPE_OFFSET", label="示波器通道偏置", unit="V", visible=False)
    scope_vertical_divisions: int = parameter(default=8, external_name="SCOPE_VERTICAL_DIVISIONS", label="自动量程垂直总格数", visible=False, minimum=1)
    scope_scale_min_v_div: float = parameter(default=0.01, external_name="SCOPE_SCALE_MIN", label="自动量程下限", unit="V/div", visible=False, minimum=0.000001)
    scope_scale_max_v_div: float = parameter(default=10.0, external_name="SCOPE_SCALE_MAX", label="自动量程上限", unit="V/div", visible=False, minimum=0.000001)
    scope_auto_range_low_fraction: float = parameter(default=0.4, external_name="SCOPE_AUTO_RANGE_LOW_FRACTION", label="自动量程缩小阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_high_fraction: float = parameter(default=0.9, external_name="SCOPE_AUTO_RANGE_HIGH_FRACTION", label="自动量程放大阈值", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_offset_tolerance_fraction: float = parameter(default=0.05, external_name="SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", label="自动偏置居中死区", visible=False, minimum=0.0, maximum=1.0)
    scope_auto_range_max_attempts: int = parameter(default=3, external_name="SCOPE_AUTO_RANGE_MAX_ATTEMPTS", label="自动量程最大尝试次数", visible=False, minimum=1)

    welch_nperseg: int = parameter(default=10000, external_name="WELCH_NPERSEG", label="Welch 每段点数", group="advanced", minimum=8)
    fit_peak_margin_hz: float = parameter(default=10000.0, external_name="FIT_PEAK_MARGIN_HZ", label="拟合脊线半窗口", unit="Hz", group="advanced", minimum=0.001)
    fit_half_width_hz: float = parameter(default=5000.0, external_name="FIT_HALF_WIDTH_HZ", label="控制频率拟合半宽", unit="Hz", group="advanced", minimum=0.001, description="每个 PSD 频率只使用其中心附近 ± 此半宽的控制频率点拟合。")
    fit_gamma_guess_hz: float = parameter(default=300.0, external_name="FIT_GAMMA_GUESS_HZ", label="拟合线宽初值", unit="Hz", group="advanced", minimum=0.001)
    fit_example_plot_count: int = parameter(default=10, external_name="FIT_EXAMPLE_PLOT_COUNT", label="示例拟合图数量", group="advanced", minimum=1)
    global_2d_analysis_enabled: bool = parameter(default=True, external_name="GLOBAL_2D_ANALYSIS_ENABLED", label="启用二维全局分解", group="advanced")
    global_fit_frequency_min_hz: float = parameter(default=500.0, external_name="GLOBAL_FIT_FREQUENCY_MIN_HZ", label="二维输出频率下限", unit="Hz", group="advanced", minimum=0.001, description="固定核心全局量后，向低频投影 N_S1 和 C_S_beta 的起点。")
    global_core_fit_frequency_min_hz: float = parameter(default=2000.0, external_name="GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ", label="二维核心拟合频率下限", unit="Hz", group="advanced", minimum=0.001, description="仅用该频率以上的数据确定 Gamma、控制频率偏移和 H(Omega)。")
    global_fit_frequency_max_hz: float = parameter(default=45000.0, external_name="GLOBAL_FIT_FREQUENCY_MAX_HZ", label="二维拟合频率上限", unit="Hz", group="advanced", minimum=0.001)
    global_background_margin_hz: float = parameter(default=5000.0, external_name="GLOBAL_BACKGROUND_MARGIN_HZ", label="二维背景脊线排除半宽", unit="Hz", group="advanced", minimum=0.001)
    global_cv_folds: int = parameter(default=5, external_name="GLOBAL_CV_FOLDS", label="二维控制频率交叉验证折数", group="advanced", minimum=2)

    temp_switch_off_settle_s: float = parameter(default=0.3, external_name="TEMP_SWITCH_OFF_SETTLE_S", label="温控关闭后统一等待", unit="s", group="basic", minimum=0.0)
    temp_switch_on_settle_s: float = parameter(default=1.0, external_name="TEMP_SWITCH_ON_SETTLE_S", label="温控恢复后等待", unit="s", group="basic", minimum=0.0)
    temperature_tolerance_c: float = parameter(default=1.0, external_name="TEMPERATURE_TOLERANCE_C", label="初始温度稳定容差", unit="°C", group="advanced", minimum=0.0)
    temperature_stable_reads: int = parameter(default=1, external_name="TEMPERATURE_STABLE_READS", label="初始温度连续稳定读数", group="advanced", minimum=1)
    temperature_poll_interval_s: float = parameter(default=5.0, external_name="TEMPERATURE_POLL_INTERVAL_S", label="温度轮询间隔", unit="s", group="advanced", minimum=0.1)
    temperature_timeout_s: float = parameter(default=1200.0, external_name="TEMPERATURE_TIMEOUT_S", label="温度稳定超时", unit="s", group="advanced", minimum=1.0)

    pump_carrier_frequency_hz: float = parameter(default=100e6, external_name="PUMP_CARRIER_FREQUENCY_HZ", label="Pump AOM 载波频率", unit="Hz", group="advanced", minimum=1.0)
    pump_carrier_amplitude_vpp: float = parameter(default=0.18, external_name="PUMP_CARRIER_AMPLITUDE_VPP", label="Pump AOM 载波幅度", unit="Vpp", group="advanced", minimum=0.0, maximum=0.18, safety_key="Pump_modulation")
    pump_gate_voltage_v: float = parameter(default=5.0, external_name="PUMP_GATE_VOLTAGE_V", label="Pump RF 开关常开电平", unit="V", group="advanced", minimum=5.0, maximum=5.0, safety_key="Time_sequence")
    pump_laser_power_v: float = parameter(default=0.5, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
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
            schema_version <= 1
            and migrated.get("SCOPE_VERTICAL_DIVISIONS") == 4
        ):
            migrated["SCOPE_VERTICAL_DIVISIONS"] = 8
        if schema_version <= 5:
            migrated.setdefault("FIXED_PARAMS.X_magnetic_field", 0.0)
            migrated.setdefault("FIXED_PARAMS.Y_magnetic_field", 0.0)
        if schema_version <= 6:
            legacy_minimum_hz = float(
                migrated.get("GLOBAL_FIT_FREQUENCY_MIN_HZ", 5000.0)
            )
            migrated["GLOBAL_FIT_FREQUENCY_MIN_HZ"] = min(
                legacy_minimum_hz, 500.0
            )
            migrated.setdefault(
                "GLOBAL_CORE_FIT_FREQUENCY_MIN_HZ",
                min(legacy_minimum_hz, 2000.0),
            )
        return migrated

    def current_for_control_frequency(self, control_frequency_hz: float) -> float:
        """按主场标定外推，将目标 Larmor 频率换算为 GS200 电流。"""
        return (
            float(control_frequency_hz) - self.main_field_calibration_intercept_hz
        ) / self.main_field_calibration_hz_per_ma

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if not self.main_field_calibration_source_run.strip():
            errors.append("MAIN_FIELD_CALIBRATION_SOURCE_RUN 不能为空")
        if self.control_frequency_start_hz >= self.control_frequency_stop_hz:
            errors.append("控制频率范围必须满足起点 < 终点")
        if self.control_frequency_stop_hz >= self.scope_sample_rate_sa_s / 2.0:
            errors.append("控制频率终点必须低于示波器请求采样率的 Nyquist 频率")
        if self.scope_requested_points < 8:
            errors.append("示波器请求采样点数必须至少为 8")
        if self.welch_nperseg > self.scope_requested_points:
            errors.append("WELCH_NPERSEG 不能超过示波器请求采样点数")
        control_step_hz = (
            self.control_frequency_stop_hz - self.control_frequency_start_hz
        ) / (self.control_frequency_points - 1)
        if 2.0 * self.fit_half_width_hz / control_step_hz < 9.0:
            errors.append("FIT_HALF_WIDTH_HZ 内必须至少包含 10 个控制频率点")
        if self.global_fit_frequency_min_hz >= self.global_fit_frequency_max_hz:
            errors.append("二维拟合频率范围必须满足下限 < 上限")
        if not (
            self.global_fit_frequency_min_hz
            <= self.global_core_fit_frequency_min_hz
            < self.global_fit_frequency_max_hz
        ):
            errors.append("二维频率范围必须满足输出下限 <= 核心拟合下限 < 上限")
        if self.global_background_margin_hz < 2.0 * control_step_hz:
            errors.append("GLOBAL_BACKGROUND_MARGIN_HZ 必须至少覆盖两个控制频率步长")
        if (
            self.global_2d_analysis_enabled
            and self.control_frequency_stop_hz
            >= self.global_core_fit_frequency_min_hz
            and self.global_cv_folds > self.control_frequency_points // 4
        ):
            errors.append("GLOBAL_CV_FOLDS 必须保证每折至少包含 4 个控制频率点")
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
        for name, control_frequency in (
            ("CONTROL_FREQUENCY_START_HZ", self.control_frequency_start_hz),
            ("CONTROL_FREQUENCY_STOP_HZ", self.control_frequency_stop_hz),
        ):
            current_ma = self.current_for_control_frequency(control_frequency)
            try:
                validate_safety_limit("main_magnetic_field", current_ma)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        return errors
