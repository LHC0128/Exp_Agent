"""保留旧 GUI/YAML 键；参数版本与原始数据版本独立。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar
import math
from ...experiment_params import ExperimentParams, parameter


@dataclass
class PhotonShotNoiseParams(ExperimentParams):
    schema_version: ClassVar[int] = 2
    system_settle_legacy_defaults: ClassVar[dict] = {"PROBE_SETTLE_TIME": 0.5}
    removed_temperature_keys: ClassVar[tuple[str, ...]] = ()

    system_settle_s: float = parameter(default=0.5, external_name="SYSTEM_SETTLE_S", label="系统稳定时间", unit="s", minimum=0, group="basic")
    probe_power_start: float = parameter(default=0.0, external_name="PROBE_POWER_START", label="Probe 起始控制电压", unit="V", group="basic")
    probe_power_stop: float = parameter(default=0.3, external_name="PROBE_POWER_STOP", label="Probe 终止控制电压", unit="V", group="basic")
    probe_power_points: int = parameter(default=31, external_name="PROBE_POWER_POINTS", label="Probe 扫描点数", minimum=3, group="basic")
    acq_duration: float = parameter(default=1.0, external_name="ACQ_DURATION", label="单次采集时长", unit="s", minimum=1e-6, group="basic")
    acq_repeats: int = parameter(default=20, external_name="ACQ_REPEATS", label="每点重复次数", minimum=1, group="basic")
    temp_recovery_time_s: float = parameter(default=2.0, external_name="TEMP_RECOVERY_TIME_S", label="恢复温控等待时间", unit="s", minimum=0, legacy_default=2.0)
    temperature: float = parameter(default=100.0, external_name="FIXED_PARAMS.temperature", label="目标温度", unit="°C", group="basic")
    shot_noise_field_ma: float = parameter(default=5.0, external_name="FIXED_PARAMS.SHOT_NOISE_FIELD_mA", label="散粒噪声主场偏置", unit="mA", group="basic")
    pump_laser_power: float = parameter(default=0.0, external_name="FIXED_PARAMS.Pump_laser_power", label="Pump 控制电压（输出保持关闭）", unit="V")
    temp_switch: float = parameter(default=5.0, external_name="FIXED_PARAMS.Temp_Switch", label="温控恢复电压", unit="V", minimum=5, maximum=5, read_only=True)
    hf2_demod_idx: int = parameter(default=0, external_name="HF2_DEMOD_IDX", label="解调器索引", minimum=0, maximum=5)
    hf2_osc_freq: float = parameter(default=90000.0, external_name="HF2_OSC_FREQ", label="锁相参考频率", unit="Hz", minimum=1e-6)
    hf2_signal_range: float = parameter(default=2.0, external_name="HF2_SIGNAL_RANGE", label="主电压输入量程", unit="V", minimum=1e-6)
    hf2_demod_order: int = parameter(default=4, external_name="HF2_DEMOD_ORDER", label="解调滤波阶数", minimum=1, maximum=8)
    hf2_demod_tc: float = parameter(default=7e-7, external_name="HF2_DEMOD_TC", label="解调时间常数", unit="s", minimum=1e-12)
    hf2_demod_rate: float = parameter(default=50000.0, external_name="HF2_DEMOD_RATE", label="请求采样率", unit="Sa/s", minimum=1, group="basic")
    psd_nperseg: int = parameter(default=4096, external_name="PSD_NPERSEG", label="Welch 分段点数", minimum=4, group="basic")
    fit_start_idx: int = parameter(default=2, external_name="FIT_START_IDX", label="拟合起始点索引", minimum=0, group="basic", legacy_default=2)
    flat_freq_fraction: float = parameter(default=0.05, external_name="FLAT_FREQ_FRACTION", label="平坦频段上限 / 实际采样率", minimum=1e-6, maximum=0.5, group="basic", legacy_default=0.05)
    run_tag: str = parameter(default="shot_noise_cal", external_name="RUN_TAG", label="运行标签", group="basic")

    def validate_model(self):
        errors = []
        # 直接构造模型同样拒绝 NaN/Inf，不能只依赖 JSON 入口转换。
        for key, value in self.to_external().items():
            if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
                errors.append(f"{key} 必须是有限数值")
        for name in ("probe_power_points", "acq_repeats", "hf2_demod_idx", "hf2_demod_order", "psd_nperseg", "fit_start_idx"):
            if type(getattr(self, name)) is not int:
                errors.append(f"{name} 必须是整数")
        if self.probe_power_stop <= self.probe_power_start:
            errors.append("Probe 终止控制电压必须大于起始值")
        if not self.run_tag.strip() or any(c in self.run_tag for c in ("/", chr(92), ":", chr(0))):
            errors.append("运行标签不能为空或包含路径分隔符")
        return errors

    @classmethod
    def derive_external(cls, values):
        p = cls.from_external(values)
        return dict(total_records=p.probe_power_points * p.acq_repeats,
            estimated_minimum_duration_s=p.probe_power_points * p.acq_repeats *
                (p.acq_duration + p.system_settle_s + p.temp_recovery_time_s))
