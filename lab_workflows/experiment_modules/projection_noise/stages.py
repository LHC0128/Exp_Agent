"""投影噪声的唯一模块、节点编辑归属与参数失效清单。"""
from dataclasses import asdict
from ..mx_y_rf_sensitivity.stages import (
    Stage, initial_states, invalidate, invalidated_stages, parameter_impacts,
    validate_selection as _validate_selection,
)
from .run_format import EXPERIMENT_ID

FINISH_ID = "A09"
ANALYSIS_KEYS = ("WELCH_NPERSEG", "FIT_HALF_WIDTH_HZ")
SCOPE = ("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_INITIAL_SCALE", "SCOPE_PD_CHANNEL",
    "SCOPE_TRIGGER_MODE", "SCOPE_OFFSET", "SCOPE_VERTICAL_DIVISIONS", "SCOPE_SCALE_MIN",
    "SCOPE_SCALE_MAX", "SCOPE_AUTO_RANGE_LOW_FRACTION", "SCOPE_AUTO_RANGE_HIGH_FRACTION",
    "SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION", "SCOPE_AUTO_RANGE_MAX_ATTEMPTS",
    "SCOPE_RECORD_MAX_ATTEMPTS", *ANALYSIS_KEYS)
ACQUISITION = (
    Stage("A01", "参数、主场标定与映射预检", "准备", (),
        ("TARGET_LARMOR_FREQUENCY_HZ", "MAIN_FIELD_CALIBRATION_SOURCE_RUN"),
        quality_gates=("标定成功、反算电流安全；包括保留的 Pump 载波/门控在内无通道冲突；不连接硬件",)),
    Stage("A02", "连接设备与 GS200 初态回读", "准备", ("A01",), ("RUN_TAG",),
        quality_gates=("连接复用、GS200 完整初态可恢复；不连接专用 Pump 载波/门控设备",)),
    Stage("A03", "已连接设备参考时钟同步与回读", "准备", ("A02",),
        quality_gates=("参考时钟严格回读一致；不设置未连接的 Pump 载波/门控设备",)),
    Stage("A04", "辅助场归零与光功率准备", "初始条件", ("A03",),
        ("FIXED_PARAMS.Pump_laser_power", "FIXED_PARAMS.Probe_laser_power"),
        quality_gates=("GS200 暂时关闭；XY/Z 归零关闭；Pump 载波与门控全程不写",)),
    Stage("A05", "TEC 目标温度", "初始条件", ("A04",), ("FIXED_PARAMS.temperature",),
        quality_gates=("仅下发 UI 目标，不启停、不判稳；串口不可用告警跳过",)),
    Stage("A06", "SDS 固定采样率与量程回读", "采集", ("A05",), SCOPE,
        display_parameters=("SCOPE_SAMPLE_RATE", "SCOPE_DURATION", "SCOPE_INITIAL_SCALE", *ANALYSIS_KEYS),
        quality_gates=("FSRate、实际采样率与点数通过；Nyquist 覆盖拟合窗口",)),
    Stage("A07", "可选 OFF 背景与 ON 自旋噪声采集", "采集", ("A06",),
        ("MEASURE_FIELD_OFF_CONTROL", "ACQ_REPEATS", "MAIN_FIELD_SETTLE_S",
         "SYSTEM_SETTLE_S", "TEMP_SWITCH_ON_SETTLE_S"),
        ("raw/scope_acquisition_index.npz", "raw/field_on/waveform_*.npz"),
        quality_gates=("保持可选 OFF→ON 顺序和跨阶段量程继承；每次尝试恢复温控后保存，失败抢救已读帧",)),
    Stage("A08", "本轮安全恢复与封存", "结束", ("A07",),
        quality_gates=("恢复 GS200 完整原态、温控与 SDS STOP；XY/Z 关闭；不修改 Pump 载波/门控；恢复失败不能完成",)),
    Stage(FINISH_ID, "安全结束会话", "结束", ()),
)
ANALYSIS = (
    Stage("B01", "加载原始数据与采样资格", "独立分析", (), ANALYSIS_KEYS,
        display_parameters=(), artifacts=("projection_inputs.json",),
        quality_gates=("本实验身份、独立 raw 格式、完整波形与时间轴有效；目录已停止写入",)),
    Stage("B02", "Welch、洛伦兹诊断与结果图", "独立分析", ("B01",), ANALYSIS_KEYS,
        artifacts=("projection_analysis.json",),
        quality_gates=("SDS 保留原自由洛伦兹拟合；失败仍输出诊断；旧 HF2 仅走原兼容 PSD，不套用 RF 拟合",)),
)


def validate_selection(stages, states, selected):
    if FINISH_ID in selected and selected[-1] != FINISH_ID:
        raise ValueError("安全结束必须最后执行")
    return _validate_selection(stages, states, selected)


def definition():
    def public(stage):
        return asdict(stage) | {"display_parameters": list(
            stage.parameters if stage.display_parameters is None else stage.display_parameters)}
    return dict(acquisition=[public(stage) for stage in ACQUISITION],
        analysis=[public(stage) for stage in ANALYSIS],
        parameter_impacts=dict(acquisition=parameter_impacts(ACQUISITION), analysis=parameter_impacts(ANALYSIS)),
        diagram_url=f"/api/experiments/{EXPERIMENT_ID}/workflow/diagram")
