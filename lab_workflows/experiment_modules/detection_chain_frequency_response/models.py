"""探测链路频率响应标定强类型参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from ...experiment_params import ExperimentParams, parameter


@dataclass(slots=True)
class DetectionChainFrequencyResponseParams(ExperimentParams):
    """探测链路频率响应标定参数模型。
    
    扫描 Probe AOM AM 调制频率，固定调制幅度，
    测量 HF2 解调后基带 Y 信号的谱峰高度，
    用于标定 BPD + HF2 联合频率响应。
    """
    
    schema_version: ClassVar[int] = 1
    
    # 基础参数
    run_tag: str = parameter(
        default="freq_resp",
        external_name="RUN_TAG",
        label="运行标签",
        group="basic"
    )
    
    am_freq_start_hz: float = parameter(
        default=50000.0,
        external_name="AM_FREQ_START_HZ",
        label="AM 调制起始频率",
        unit="Hz",
        group="basic",
        minimum=1000.0,
        description="Probe AOM AM 调制的起始频率"
    )
    
    am_freq_stop_hz: float = parameter(
        default=130000.0,
        external_name="AM_FREQ_STOP_HZ",
        label="AM 调制终止频率",
        unit="Hz",
        group="basic",
        minimum=1000.0,
        description="Probe AOM AM 调制的终止频率"
    )
    
    am_freq_points: int = parameter(
        default=41,
        external_name="AM_FREQ_POINTS",
        label="频率扫描点数",
        group="basic",
        minimum=2,
        description="扫描频率点数，推荐 2-3 kHz 步进"
    )
    
    am_settle_time_s: float = parameter(
        default=1.0,
        external_name="AM_SETTLE_TIME_S",
        label="每点稳定等待",
        unit="s",
        group="basic",
        minimum=0.1,
        description="改变 AM 频率后的稳定等待时间"
    )

    temp_switch_off_lead_s: float = parameter(
        default=0.1,
        external_name="TEMP_SWITCH_OFF_LEAD_S",
        label="温控关闭后等待",
        unit="s",
        group="advanced",
        minimum=0.0,
        description="每点关闭温控后的等待时间；随后继续等待 AM 频率稳定"
    )

    temp_switch_on_lag_s: float = parameter(
        default=1.0,
        external_name="TEMP_SWITCH_ON_LAG_S",
        label="温控恢复后等待",
        unit="s",
        group="advanced",
        minimum=0.0,
        description="每点采集后恢复温控的等待时间"
    )
    
    # Probe AOM 载波和 AM 设置
    probe_aom_carrier_freq_hz: float = parameter(
        default=100e6,
        external_name="PROBE_AOM_CARRIER_FREQ_HZ",
        label="Probe AOM 载波频率",
        unit="Hz",
        group="advanced",
        minimum=1e6,
        description="Probe AOM 的射频载波频率"
    )
    
    probe_aom_carrier_amplitude_vpp: float = parameter(
        default=0.05,
        external_name="PROBE_AOM_CARRIER_AMPLITUDE_VPP",
        label="Probe AOM 载波幅度",
        unit="Vpp",
        group="advanced",
        safety_key="Probe_AOM_Carrier",
        minimum=0.0,
        maximum=0.05,
        description="CH1 载波输出幅度"
    )
    
    probe_aom_am_amplitude_vpp: float = parameter(
        default=1.0,
        external_name="PROBE_AOM_AM_AMPLITUDE_VPP",
        label="AM 调制幅度",
        unit="Vpp",
        group="basic",
        minimum=0.1,
        maximum=5.0,
        description="固定的 AM 调制信号幅度"
    )
    
    # HF2 解调参数
    hf2_demod_idx: int = parameter(
        default=0,
        external_name="HF2_DEMOD_IDX",
        label="HF2 Demod 索引",
        group="advanced",
        minimum=0,
        description="与 XY 控制噪声谱实验一致，使用 Demod0 的 Y 信号"
    )
    
    hf2_osc_freq_hz: float = parameter(
        default=90000.0,
        external_name="HF2_OSC_FREQ_HZ",
        label="HF2 解调参考频率",
        unit="Hz",
        group="basic",
        minimum=1000.0,
        description="HF2 振荡器频率，通常为 90 kHz"
    )
    
    hf2_signal_range_v: float = parameter(
        default=2.0,
        external_name="HF2_SIGNAL_RANGE_V",
        label="HF2 输入量程",
        unit="V",
        group="advanced",
        minimum=0.01
    )
    
    hf2_demod_order: int = parameter(
        default=8,
        external_name="HF2_DEMOD_ORDER",
        label="HF2 Demod 滤波器阶数",
        group="advanced",
        minimum=1,
        maximum=8
    )
    
    hf2_demod_tc_s: float = parameter(
        default=0.000692,
        external_name="HF2_DEMOD_TC_S",
        label="旧版解调时间常数（不生效）",
        unit="s",
        group="advanced",
        minimum=1e-7,
        visible=False,
        description="兼容旧配置；采集时的解调低通由 HF2_DAQ_TC_S 设置"
    )
    
    hf2_daq_duration_s: float = parameter(
        default=1.0,
        external_name="HF2_DAQ_DURATION_S",
        label="每点采集时长",
        unit="s",
        group="basic",
        minimum=0.5,
        description="每个频率点的 DAQ 采集时长"
    )
    
    hf2_daq_rate_sa_s: float = parameter(
        default=450000.0,
        external_name="HF2_DAQ_RATE_SA_S",
        label="DAQ 采样率",
        unit="Sa/s",
        group="advanced",
        minimum=1000.0
    )
    
    hf2_daq_tc_s: float = parameter(
        default=7.85e-7,
        external_name="HF2_DAQ_TC_S",
        label="HF2 采集解调时间常数",
        unit="s",
        group="advanced",
        minimum=1e-7,
        description="配置 Demod0 的低通时间常数，与 XY 正弦控制噪声谱采集阶段一致"
    )
    
    # PSD 分析参数
    psd_nperseg: int = parameter(
        default=8192,
        external_name="PSD_NPERSEG",
        label="Welch 段长",
        group="advanced",
        minimum=256,
        description="Welch PSD 计算的每段点数"
    )
    
    # 光功率与温度参数
    pump_laser_power_v: float = parameter(
        default=0.0,
        external_name="FIXED_PARAMS.Pump_laser_power",
        label="Pump 光功率",
        unit="V",
        group="basic",
        safety_key="Pump_laser_power",
        description="Pump 光功率设置；任何取值都在每点采集期间关闭温控并随后恢复"
    )
    
    probe_laser_power_v: float = parameter(
        default=0.2,
        external_name="FIXED_PARAMS.Probe_laser_power",
        label="Probe 光功率",
        unit="V",
        group="basic",
        safety_key="Probe_laser_power"
    )
    
    temperature_c: float = parameter(
        default=100.0,
        external_name="FIXED_PARAMS.temperature",
        label="气室温度",
        unit="°C",
        group="basic",
        safety_key="temperature"
    )
    
    @classmethod
    def migrate_external(
        cls,
        values: dict[str, Any],
        schema_version: int,
    ) -> dict[str, Any]:
        """参数版本迁移（当前仅 v1）。"""
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 {cls.schema_version}"
            )
        return values
