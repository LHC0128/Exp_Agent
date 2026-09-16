"""Z 任意波静态斜率闭环参数；独立声明 GUI 字段。"""
from __future__ import annotations

from dataclasses import dataclass

from ...common import find_project_root, validate_safety_limit
from ...experiment_params import ExperimentParams, parameter
from ...control_sources import (
    CONTROL_SOURCE_SET_ROOTS, load_theory_control, resolve_control_results_root,
)
from .response_filter import prepare_closed_loop


@dataclass(slots=True)
class ZAWClosedLoopWaveformCorrectionParams(ExperimentParams):
    """响应逆滤波闭环：静态标定初始化，AW 频响构造周期滤波核。"""

    schema_version = 13
    run_tag: str = parameter(
        default="z_aw_closed_loop_waveform_correction",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic",
    )

    control_version: str = parameter(
        default="v6",
        external_name="CONTROL_VERSION",
        label="最优控制版本",
        group="basic",
        description="按 vN 读取同版本的最优控制波形和理论参数。",
    )

    control_scale: float = parameter(
        default=1.0,
        external_name="CONTROL_SCALE",
        label="控制幅度比例",
        group="advanced",
        minimum=0.000001,
    )

    correction_method: str = parameter(
        default="harmonic_jacobian", external_name="CORRECTION_METHOD",
        label="校正方法", group="basic",
        options=(("harmonic_jacobian", "高通链路谐波辨识闭环（CH4 固定参考）"),
                 ("time_domain", "历史时域实际电流反馈"),
                 ("response_filtered_feedback", "频响预加重反馈")),
    )

    z_aw_output_vpp: float = parameter(
        default=10.0,
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
        visible=False,
        description="闭环实验固定使用 0 V；保留旧键仅用于兼容历史配置。",
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
        group="advanced",
        minimum=0.001,
    )

    trigger_amplitude_vpp: float = parameter(
        default=10.0,
        external_name="TRIGGER_AMPLITUDE_VPP",
        label="共同触发幅度",
        unit="Vpp",
        group="advanced",
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
        minimum=20.0,
        maximum=80.0,
    )

    scope_cycles: int = parameter(
        default=3,
        external_name="SCOPE_CYCLES",
        label="每次采集周期数",
        group="advanced",
        minimum=2,
    )

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

    scope_vertical_divisions: int = parameter(
        default=8,
        external_name="SCOPE_VERTICAL_DIVISIONS",
        label="示波器垂直总格数",
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

    control_source_set: str = parameter(default="oc_sens", external_name="CONTROL_SOURCE_SET", label="理论结果集", group="basic", options=tuple((x, x) for x in CONTROL_SOURCE_SET_ROOTS))
    current_coupling_calibration_source_run: str = parameter(default="0914_184134_mx_z_current_coupling_calibration", external_name="CURRENT_COUPLING_CALIBRATION_SOURCE_RUN", label="静态电流耦合标定", group="basic")
    current_frequency_response_source_run: str = parameter(default="", external_name="CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN", label="电流频响标定来源", group="basic", description="只接受 AW 外部下降沿 Burst 协议的新标定运行目录名。")
    inverse_regularization: float = parameter(default=0.05, external_name="INVERSE_REGULARIZATION", label="逆滤波正则化比例", group="basic", minimum=0.000001, maximum=1.0, description="lambda = 该比例 × 学习频带内最大 |H|，抑制弱响应频率上的过大修正。")
    max_iterations: int = parameter(default=20, external_name="MAX_ITERATIONS", label="最大测量轮数（含初始轮）", minimum=1, group="basic")
    iteration_repeats: int = parameter(default=5, external_name="ITERATION_REPEATS", label="每轮重复采集帧数", minimum=1, group="basic")
    iteration_damping: float = parameter(default=0.5, external_name="ITERATION_DAMPING", label="更新系数 α", minimum=0.000001, maximum=1.0, group="basic")
    error_cutoff_hz: float = parameter(default=500000.0, external_name="ERROR_CUTOFF_HZ", label="最高学习频率", unit="Hz", minimum=0.000001, group="basic", description="闭环只更新该频率以下的电压分量；带外误差仍完整计入评分。")
    target_relative_rms: float = parameter(default=0.03, external_name="TARGET_RELATIVE_RMS", label="相对 RMS 误差阈值", minimum=0.000001, group="basic")
    required_passes: int = parameter(default=3, external_name="REQUIRED_PASSES", label="连续达标轮数", minimum=1)
    output_settle_s: float = parameter(default=0.1, external_name="OUTPUT_SETTLE_S", label="更新后稳定等待", unit="s", minimum=0.0)
    scope_headroom_factor: float = parameter(default=1.5, external_name="SCOPE_HEADROOM_FACTOR", label="理论电压量程余量倍数", minimum=1.0, group="advanced", description="理论采样电阻电压峰峰值乘以该倍数，向上选 1/2/5 档并居中；采集中只放大量程。")

    holdout_repeats: int = parameter(default=5, external_name="HOLDOUT_REPEATS", label="独立验证帧数", minimum=1, maximum=20, group="basic")
    maximum_step_v: float = parameter(default=0.5, external_name="MAXIMUM_STEP_V", label="单轮最大电压增量", unit="V", minimum=0.000001, group="advanced")
    maximum_backtracks: int = parameter(default=4, external_name="MAXIMUM_BACKTRACKS", label="最大连续回退次数", minimum=1, group="advanced")
    maximum_current_a: float = parameter(default=1.0, external_name="MAXIMUM_CURRENT_A", label="峰值电流上限", unit="A", minimum=0.000001, group="advanced")
    sense_resistor_power_rating_w: float = parameter(default=10.0, external_name="SENSE_RESISTOR_POWER_RATING_W", label="采样电阻额定功率", unit="W", minimum=0.000001, group="advanced")
    sense_resistor_power_derating: float = parameter(default=0.5, external_name="SENSE_RESISTOR_POWER_DERATING", label="采样电阻功率降额系数", minimum=0.000001, maximum=1.0, group="advanced")

    harmonic_amplitude_fraction: float = parameter(default=0.01, external_name="HARMONIC_AMPLITUDE_FRACTION", label="目标谐波幅度门限（相对最大值）", minimum=0.000001, maximum=1.0, group="advanced")
    maximum_harmonics: int = parameter(default=16, external_name="MAXIMUM_HARMONICS", label="最大辨识谐波数", minimum=1, maximum=64, group="advanced")
    identification_step_v: float = parameter(default=0.02, external_name="IDENTIFICATION_STEP_V", label="辨识初始扰动峰值", unit="V", minimum=0.000001, group="advanced")
    identification_maximum_step_v: float = parameter(default=0.05, external_name="IDENTIFICATION_MAXIMUM_STEP_V", label="辨识最大扰动峰值", unit="V", minimum=0.000001, group="advanced")
    identification_repeats: int = parameter(default=3, external_name="IDENTIFICATION_REPEATS", label="每个扰动重复帧数", minimum=3, maximum=20, group="advanced")
    identification_min_snr: float = parameter(default=5.0, external_name="IDENTIFICATION_MIN_SNR", label="辨识差分最低信噪比", minimum=3.0, group="advanced")
    repeatability_relative_rms: float = parameter(default=0.02, external_name="REPEATABILITY_RELATIVE_RMS", label="重载波形变化上限（相对目标 RMS）", minimum=0.000001, maximum=0.2, group="advanced")
    harmonic_repeatability_fraction: float = parameter(default=0.05, external_name="HARMONIC_REPEATABILITY_FRACTION", label="重载谐波复系数变化上限", minimum=0.000001, maximum=0.5, group="advanced")
    minimum_improvement: float = parameter(default=0.0002, external_name="MINIMUM_IMPROVEMENT", label="最小误差改善（绝对比例）", minimum=0.0, group="advanced")
    reidentify_interval: int = parameter(default=3, external_name="REIDENTIFY_INTERVAL", label="重新辨识间隔轮数", minimum=1, group="advanced")
    final_validation_repeats: int = parameter(default=5, external_name="FINAL_VALIDATION_REPEATS", label="最终重载验证帧数", minimum=3, maximum=20, group="basic")

    @classmethod
    def migrate_external(cls, values: dict[str, object], schema_version: int) -> dict[str, object]:
        """只在配置入口处理已发布的旧键，新循环不保留旧算法分支。"""
        migrated = super(ZAWClosedLoopWaveformCorrectionParams, cls).migrate_external(values, schema_version)
        old_root = str(migrated.pop("CONTROL_RESULTS_ROOT", "")).replace("\\", "/").rstrip("/")
        if "CONTROL_SOURCE_SET" not in migrated and old_root:
            name = old_root.split("/")[-1]
            if name not in CONTROL_SOURCE_SET_ROOTS:
                raise ValueError("旧根目录不是受支持的固定结果集")
            migrated["CONTROL_SOURCE_SET"] = name
        if "TIME_DOMAIN_CUTOFF_HZ" in migrated:
            cutoff = migrated.pop("TIME_DOMAIN_CUTOFF_HZ")
            # 旧 0 表示不限带宽；新版本明确要求正截止频率，迁移为新默认。
            migrated.setdefault("ERROR_CUTOFF_HZ", cutoff if float(cutoff) > 0 else 40000.0)
        # 旧指标除以目标标准差；改为目标 RMS 后采用明确的新阈值默认。
        migrated.pop("TARGET_SHAPE_NRMSE", None)
        if "SCOPE_REPEATS" in migrated:
            migrated.setdefault("ITERATION_REPEATS", migrated.pop("SCOPE_REPEATS"))
        removed = (
            "Z_CALIBRATION_SOURCE_RUN",
            "TIME_DOMAIN_INITIALIZATION", "TIME_DOMAIN_LEARNING_OPERATOR",
            "TIME_DOMAIN_PROJECT_VOLTAGE", "INITIAL_WAVEFORM_SOURCE_RUN",
            "TIME_DOMAIN_MAX_STEP_V", "TIME_DOMAIN_MIN_DAMPING",
            "MAXIMUM_ERROR_INCREASE_FRACTION", "SENSE_RESISTOR_OHM",
            "CURRENT_PREDICTION_GAIN_MARGIN", "SCOPE_INITIAL_SCALE", "SCOPE_SCALE_MIN",
            "SCOPE_SCALE_MAX", "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION",
            "SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
        )
        for name in removed:
            migrated.pop(name, None)
        if schema_version < 12 and migrated.get("CORRECTION_METHOD") != "harmonic_jacobian":
            # 历史运行的算法解释不随新版默认值改变。
            old_method = migrated.get("CORRECTION_METHOD")
            migrated["CORRECTION_METHOD"] = (
                "time_domain" if old_method == "time_domain" or (old_method is None and schema_version == 10)
                else "response_filtered_feedback"
            )
        return migrated

    @classmethod
    def derive_external(cls, values: dict[str, object]) -> dict[str, object]:
        """表单中仅派生理论周期，不连接仪器。"""
        try:
            theory = load_theory_control(resolve_control_results_root(str(values.get("CONTROL_SOURCE_SET", "oc_sens"))), str(values.get("CONTROL_VERSION", "v6")))
        except (OSError, ValueError, KeyError):
            return {}
        return {"CONTROL_REPEAT_FREQUENCY_HZ": theory.repeat_frequency_hz,
                "SCOPE_DURATION_S": int(values.get("SCOPE_CYCLES", 3)) / theory.repeat_frequency_hz}

    def validate_model(self) -> list[str]:
        errors = []
        if self.correction_method == "harmonic_jacobian":
            if min(self.iteration_repeats, self.holdout_repeats) < 3:
                errors.append("谐波模式反馈和独立验收至少各 3 帧，才能估计重复噪声")
            if self.identification_step_v > self.identification_maximum_step_v:
                errors.append("辨识初始扰动不能超过最大扰动")
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if (self.scope_measured_channel, self.scope_trigger_channel) != (3, 4):
            errors.append("采集固定为 CH3 电流反馈、CH4 触发")
        low = self.trigger_offset_v - self.trigger_amplitude_vpp / 2
        high = self.trigger_offset_v + self.trigger_amplitude_vpp / 2
        if not low < self.scope_trigger_level_v < high:
            errors.append("CH4 触发电平必须位于触发波形高低电平之间")
        try:
            validate_safety_limit("Time_sequence_2", low)
            validate_safety_limit("Time_sequence_2", high)
            prepare_closed_loop(find_project_root(), self)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
        return errors
