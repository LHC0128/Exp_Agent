"""XY 正弦控制噪声谱强类型参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from ...common import validate_safety_limit
from ...experiment_params import ExperimentParams, parameter


def welch_settings(rate: float, duration: float, bin_width: float, *, minimum_segment: int = 2) -> dict[str, Any]:
    """预检、采集快照及离线分析共用分段规则；不连接硬件。"""
    if any(not math.isfinite(value) or value <= 0 for value in (rate, duration, bin_width)):
        raise ValueError("采样率、每点采集时长和频率格间距必须为有限正数")
    nperseg = max(2, minimum_segment, math.ceil(rate / bin_width))
    samples = int(rate * duration)
    if nperseg > samples:
        raise ValueError("每点采集时长不足一个 Welch 分段；请增加采集时长或增大频率格间距")
    overlap = nperseg // 2
    return dict(nperseg=nperseg, noverlap=overlap, bin_width_hz=rate / nperseg,
                segments_per_point=1 + (samples - nperseg) // (nperseg - overlap))


@dataclass(slots=True)
class NoiseSpectrumXYParams(ExperimentParams):
    schema_version: ClassVar[int] = 3

    run_tag: str = parameter(default="noise", external_name="RUN_TAG", label="运行标签", group="basic")
    target_noise_freq_start_hz: float = parameter(default=0.0, external_name="TARGET_NOISE_FREQ_START_HZ", label="目标噪声频率起点", unit="Hz", group="basic", minimum=0)
    target_noise_freq_stop_hz: float = parameter(default=50000.0, external_name="TARGET_NOISE_FREQ_STOP_HZ", label="目标噪声频率终点", unit="Hz", group="basic", minimum=0)
    target_noise_freq_points: int = parameter(default=500, external_name="TARGET_NOISE_FREQ_POINTS", label="目标频率扫描点数", group="basic", minimum=2)
    xy_settle_time: float = parameter(default=0.5, external_name="XY_SETTLE_TIME", label="每点稳定等待", unit="s", group="basic", minimum=0)
    temp_switch_on_settle_s: float = parameter(default=2.0, external_name="TEMP_SWITCH_ON_SETTLE_S", label="恢复温控等待时间", unit="s", group="advanced", minimum=0, description="校相测量和每点采集恢复温控后等待的时间；支持取消。")
    pump_mod_freq: float = parameter(default=10000.0, external_name="PUMP_MOD_FREQ", label="Pump 调制频率", unit="Hz", group="basic", minimum=0.001)
    pump_mod_amplitude: float = parameter(default=0.18, external_name="PUMP_MOD_AMPLITUDE", label="Pump 载波幅度", unit="Vpp", group="basic", safety_key="Pump_modulation")
    pump_mod_duty: float = parameter(default=5.0, external_name="PUMP_MOD_DUTY", label="Pump 占空比", unit="%", group="basic", minimum=1, maximum=50)
    rf_gate_amplitude: float = parameter(default=5.0, external_name="RF_GATE_AMPLITUDE", label="RF 门控幅度", unit="Vpp", group="advanced", minimum=0)
    rf_gate_offset: float = parameter(default=2.5, external_name="RF_GATE_OFFSET", label="RF 门控偏置", unit="V", group="advanced")
    xy_ctrl_freq: float = parameter(default=10000.0, external_name="XY_CTRL_FREQ", label="XY 控制载波频率", unit="Hz", group="basic", minimum=0.001)
    xy_ctrl_phase: float = parameter(default=90.0, external_name="XY_CTRL_PHASE", label="XY 整体相位", unit="deg", group="advanced")
    xy_ctrl_quad: float = parameter(default=90.0, external_name="XY_CTRL_QUAD", label="XY 正交相位差", unit="deg", group="advanced")
    xy_calib_envelope_v: float = parameter(default=1.5, external_name="XY_CALIB_ENVELOPE_V", label="相位校准正弦峰值", unit="V", group="advanced", minimum=0)
    xy_phase_cal_tol_deg: float = parameter(default=1.0, external_name="XY_PHASE_CAL_TOL_DEG", label="XY 相位校准容差", unit="deg", group="advanced", minimum=0)
    xy_phase_cal_max_iter: int = parameter(default=10, external_name="XY_PHASE_CAL_MAX_ITER", label="XY 相位校准最大测量次数", group="advanced", minimum=1)
    xy_phase_cal_min_r_v: float = parameter(default=1e-12, external_name="XY_PHASE_CAL_MIN_R_V", label="XY 校相最低 R", unit="V", group="advanced", minimum=0)
    xy_phase_cal_min_r_ratio: float = parameter(default=0.1, external_name="XY_PHASE_CAL_MIN_R_RATIO", label="XY 校相最低 R 比例", group="advanced", minimum=0, maximum=1)
    xy_ctrl_k_hz_per_v: float = parameter(default=15075.784562638912, external_name="XY_CTRL_K_HZ_PER_V", label="正弦控制标定斜率 K", unit="Hz/V", group="advanced", minimum=0.001)
    xy_ctrl_b_hz: float = parameter(default=-218.47506313463893, external_name="XY_CTRL_B_HZ", label="正弦控制标定截距 B", unit="Hz", group="advanced")
    xy_calibration_min_envelope_v: float = parameter(default=0.0, external_name="XY_CALIBRATION_MIN_ENVELOPE_V", label="标定有效最小包络", unit="V", group="advanced", minimum=0)
    xy_calibration_max_envelope_v: float = parameter(default=4.0, external_name="XY_CALIBRATION_MAX_ENVELOPE_V", label="标定有效最大包络", unit="V", group="advanced", minimum=0)
    xy_trigger_freq: float = parameter(default=100.0, external_name="XY_TRIGGER_FREQ", label="Time_sequence_2 触发频率", unit="Hz", group="advanced", minimum=0.001)
    xy_trigger_amplitude: float = parameter(default=5.0, external_name="XY_TRIGGER_AMPLITUDE", label="Time_sequence_2 触发幅度", unit="Vpp", group="advanced", minimum=0)
    xy_trigger_offset: float = parameter(default=2.5, external_name="XY_TRIGGER_OFFSET", label="Time_sequence_2 触发偏置", unit="V", group="advanced")
    xy_trigger_duty: float = parameter(default=50.0, external_name="XY_TRIGGER_DUTY", label="Time_sequence_2 触发占空比", unit="%", group="advanced", minimum=0.001, maximum=100)
    xy_trigger_phase: float = parameter(default=0.0, external_name="XY_TRIGGER_PHASE", label="Time_sequence_2 触发相位", unit="deg", group="advanced")
    hf2_demod_idx: int = parameter(default=0, external_name="HF2_DEMOD_IDX", label="HF2 Demod 索引", group="advanced", minimum=0)
    hf2_osc_freq: float = parameter(default=10000.0, external_name="HF2_OSC_FREQ", label="HF2 振荡器频率", unit="Hz", group="advanced", minimum=0.001)
    hf2_signal_range: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE", label="HF2 输入量程", unit="V", group="advanced", minimum=0.001)
    hf2_demod_order: int = parameter(default=8, external_name="HF2_DEMOD_ORDER", label="HF2 Demod 阶数", group="advanced", minimum=1)
    hf2_demod_tc: float = parameter(default=0.000692, external_name="HF2_DEMOD_TC", label="HF2 校相时间常数", unit="s", group="advanced", minimum=0)
    hf2_demod_rate: float = parameter(default=100000.0, external_name="HF2_DEMOD_RATE", label="HF2 Demod 采样率", unit="Sa/s", group="advanced", minimum=1)
    hf2_daq_duration: float = parameter(default=1.0, external_name="HF2_DAQ_DURATION", label="每点采集时长", unit="s", group="basic", minimum=0.001)
    hf2_daq_tc: float = parameter(default=7.85e-7, external_name="HF2_DAQ_TC", label="DAQ 时间常数", unit="s", group="advanced", minimum=0)
    hf2_daq_rate: float = parameter(default=100000.0, external_name="HF2_DAQ_RATE", label="DAQ 采样率", unit="Sa/s", group="advanced", minimum=1)
    analysis_bin_width_hz: float = parameter(default=12.0, external_name="ANALYSIS_BIN_WIDTH_HZ", label="频率格间距", unit="Hz", group="basic", minimum=0.1, description="唯一分段设置：按实际采样率自动计算每段点数，实际格间距不大于此值。不改变采样率；越细可用于平均的段数越少。")
    analysis_frequency_min_hz: float = parameter(default=300.0, external_name="ANALYSIS_FREQUENCY_MIN_HZ", label="分析频带下限", unit="Hz", group="advanced", minimum=0)
    analysis_frequency_max_hz: float = parameter(default=50000.0, external_name="ANALYSIS_FREQUENCY_MAX_HZ", label="分析频带上限", unit="Hz", group="advanced", minimum=1, description="移动脊线搜索与分离频带；不使用采集 K/B 强制定位。")
    analysis_fit_half_width_hz: float = parameter(default=3000.0, external_name="ANALYSIS_FIT_HALF_WIDTH_HZ", label="局部拟合半窗口", unit="Hz", group="advanced", minimum=1)
    pump_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 光功率", unit="V", group="basic", safety_key="Pump_laser_power")
    probe_laser_power: float = parameter(default=0.1, external_name="FIXED_PARAMS.Probe_laser_power", label="Probe 光功率", unit="V", group="basic", safety_key="Probe_laser_power")
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="气室温度", unit="°C", group="basic", safety_key="temperature")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控开关电平", unit="V", group="advanced", safety_key="Temp_Switch")
    main_magnetic_field: float = parameter(default=1.03, external_name="FIXED_PARAMS.main_magnetic_field", label="主磁场电流", unit="mA", group="basic", safety_key="main_magnetic_field")

    @classmethod
    def migrate_external(
        cls,
        values: dict[str, Any],
        schema_version: int,
    ) -> dict[str, Any]:
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 {cls.schema_version}"
            )
        migrated = dict(values)
        if schema_version < 2:
            for name in (
                "XY_AW_OUTPUT_VPP",
                "XY_AW_OUTPUT_OFFSET_V",
                "XY_AW_REPEAT_FREQ_HZ",
                "XY_AW_POINTS",
            ):
                migrated.pop(name, None)
        # 旧段长只用于缺少格间距的配置迁移；显式格间距始终优先。
        old_segment = migrated.pop("HF2_NPERSEG", None)
        if old_segment is not None and "ANALYSIS_BIN_WIDTH_HZ" not in migrated:
            rate = float(migrated.get("HF2_DAQ_RATE", cls().hf2_daq_rate))
            segment = float(old_segment)
            if not math.isfinite(segment) or segment < 2 or not segment.is_integer():
                raise ValueError("旧 HF2_NPERSEG 必须是至少为 2 的整数")
            migrated["ANALYSIS_BIN_WIDTH_HZ"] = min(cls().analysis_bin_width_hz, rate / segment)
        return migrated

    @classmethod
    def derive_external(cls, values: dict[str, Any]) -> dict[str, Any]:
        """GUI 只读预览使用请求采样率；实际值由运行快照和分析摘要给出。"""
        try:
            params = cls.from_external(values)
            result = welch_settings(params.hf2_daq_rate, params.hf2_daq_duration,
                                    params.analysis_bin_width_hz)
            result["scan_seconds"] = params.target_noise_freq_points * (
                params.hf2_daq_duration + params.xy_settle_time + params.temp_switch_on_settle_s + 0.3)
            return result
        except (TypeError, ValueError, OverflowError) as exc:
            return {"error": str(exc)}

    def validate_model(self) -> list[str]:
        errors: list[str] = []
        if not self.run_tag.strip():
            errors.append("RUN_TAG 不能为空")
        if self.target_noise_freq_start_hz >= self.target_noise_freq_stop_hz:
            errors.append("目标噪声谱范围必须满足起点 < 终点")
        if self.xy_calibration_min_envelope_v >= self.xy_calibration_max_envelope_v:
            errors.append("正弦控制标定有效电压范围无效")
        if not self.xy_calibration_min_envelope_v <= self.xy_calib_envelope_v <= self.xy_calibration_max_envelope_v:
            errors.append("相位校准正弦峰值超出标定有效范围")
        if self.xy_calib_envelope_v <= 0:
            errors.append("相位校准正弦峰值必须大于 0")
        envelope_start = (self.target_noise_freq_start_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v
        envelope_stop = (self.target_noise_freq_stop_hz - self.xy_ctrl_b_hz) / self.xy_ctrl_k_hz_per_v
        envelope_min, envelope_max = min(envelope_start, envelope_stop), max(envelope_start, envelope_stop)
        if envelope_min < 0:
            errors.append("目标频率反算得到负的 |V_env|，请修改目标频率范围或标定 B")
        if envelope_min < self.xy_calibration_min_envelope_v or envelope_max > self.xy_calibration_max_envelope_v:
            errors.append(
                f"目标频率反算得到 V_env=[{envelope_min:.6f}, {envelope_max:.6f}] V，超出标定有效范围"
            )
        peak_max = max(envelope_max, self.xy_calib_envelope_v)
        for key in ("X_magnetic_field", "Y_magnetic_field"):
            for value in (-peak_max, peak_max):
                try:
                    validate_safety_limit(key, value)
                except ValueError as exc:
                    errors.append(str(exc))
        if not math.isclose(self.xy_ctrl_freq, self.pump_mod_freq, rel_tol=0, abs_tol=1e-9):
            errors.append("XY 控制频率必须与 Pump 调制频率一致")
        if not math.isclose(self.hf2_osc_freq, self.pump_mod_freq, rel_tol=0, abs_tol=1e-9):
            errors.append("HF2 振荡器频率必须与 Pump 调制频率一致")
        try:
            welch_settings(self.hf2_daq_rate, self.hf2_daq_duration, self.analysis_bin_width_hz)
        except ValueError as exc:
            errors.append(str(exc))
        if self.analysis_frequency_min_hz >= self.analysis_frequency_max_hz:
            errors.append("分析频带必须满足下限 < 上限")
        return errors
