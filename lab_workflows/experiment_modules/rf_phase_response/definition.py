"""射频相位响应的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import RfPhaseResponseParams
from .preparation import preflight

DESCRIPTION = (
    "在原子域表征射频相位响应：MFLI Signal Output 1 的 MF-MOD 无调制载波经串联大电阻驱动 Y 向 5 kHz "
    "射频场，MFLI 用独立解调器 Demod 4（参考 Osc 2）读该频率上的同步 X/Y；Z 通道由 Time_sequence_2 "
    "外触发无限 burst、连续打 +π 与 −π 各一个固定宽度脉冲。"
    "A03 按 Y_RF_PHASE_START/STOP/STEP_DEG 逐点改 MFLI 载波指令相位（默认 0–360°、10°，36 点），"
    "每点在独立关温控窗口内采集一段同步 X/Y，恢复温控并回读后再落盘；扫描结束后按实测切线把 Demod 4 "
    "相位转到工作点并回读。B01 离线按 A03 的 rotation_deg 把原始帧旋转到对齐帧，对 Y(φ) 用理论式 (2.2.2) "
    "整周拟合，给出 K₀、Y0/A/r/v0/φc/φz1、拟合残差、工作点与相量闭曲线。"
    "本实验的 1200B 功放已物理旁路，DG4000 直驱 Z 线圈：Z 脉冲按 Z_PULSE_VPP 设电压（默认 20 Vpp，"
    "DG 高阻档上限），线圈峰值电流按已知回路电阻估算、不是直接测量值；拖尾补偿默认关闭。"
    "A02 连接 DG 的 Z/X/trigger/temp、MFLI、GS200 与可选 TEC，严格回读 10 MHz 外部参考后配置链路、"
    "X 偏置、Demod 4、共同触发与 MF-MOD 载波并设主磁场；A04 先关 MFLI Out1、清 MF-MOD 并归零偏置，"
    "再关 Z/X 并归零、停 Demod 4 传输、恢复温控与 GS200 原状态；时序信号2 作为共同触发保持运行。"
)

WIRING_NOTES = (
    "MFLI Signal Output 1 经串联大电阻接 Y 方向射频线圈（MF-MOD：Osc 2 载波、Osc 3 边带调制，"
    "本实验只输出无调制载波）；不再使用 DG4000 Y 通道。",
    "MFLI Signal Input 1 接探测器输出，Demod 4（索引 3）参考 Osc 2 读出同步 X/Y；Demod 1–3 由 MF-MOD 占用。",
    "Z_magnetic_field 直连 Z 线圈（1200B 已物理旁路），DG 负载配成高阻：写入幅度即开路电动势，"
    "回路电阻计入 Z_DRIVE_SOURCE_IMPEDANCE_OHM（DG4000 源阻抗 50 Ω）+ 阻尼 + 线圈 + 采样；"
    "X_magnetic_field 只给静态 DC 偏置。",
    "Time_sequence_2 主输出（5 kHz 方波）接 Z 通道的外触发输入；Z 通道用无限 burst，"
    "载波相位由 MF-MOD 指令角决定，二者共用 Z 与 MFLI 的 10 MHz 外部参考。",
)

SAFETY_NOTES = (
    "MFLI Signal Output 1 的偏置与 2° 峰值调相包络之和必须在 ±10 V 安全限内"
    "（mfli_dev30661_signal_output_0）；Z_PULSE_VPP/2、按已知回路电阻估算的峰值电流、"
    "X 偏置、触发电平与电阻平均功耗在连接前预检；Z_PULSE_VPP 不得超过 DG4000 高阻档上限 20 Vpp。",
    "线圈电流为按已知回路电阻估算，非直接测量；判据与报告都标注估算值。",
    "A02 按参数快照后设置 GS200 主磁场并回读；A04 恢复运行前状态并复核输出、电流、量程与限流。",
    "每点在关温控窗口内只采不写，恢复温控并回读、等待 TEMP_SWITCH_ON_LAG_S 后才落盘；"
    "故障或取消时先恢复温控再抢救已完成帧。",
    "结束时先关 MFLI Out1 再清 MF-MOD 载波幅值/边带/输出并归零偏置，Z/X 归零回读；"
    "时序信号2 按共同触发保持运行，Z burst 已关闭故无脉冲。",
)


class RfPhaseResponseAdapter(TypedWorkflowAdapter):
    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
            return [str(exc)]

    def save_defaults(self, values):
        params = self._resolved(values)
        preflight(params, self.root)
        params.save_yaml(self.defaults_path)

    def analyze(self, run_dir, progress=None, *, cancellation=None):
        from ...common import WorkflowCancelled, emit
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("分析已取消，原始数据保留")
        emit(progress, "analysis", f"分析 {run_dir.name}", 90, estimated_remaining_seconds=None)
        self._execute(self.analysis_module, run_dir=run_dir, progress=progress, cancellation=cancellation)
        return {"run_dir": str(run_dir),
                "artifacts": [str(p) for p in (run_dir / "results").iterdir() if p.is_file()]}


MODULE = "lab_workflows.experiment_modules.rf_phase_response"
ADAPTER = RfPhaseResponseAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RfPhaseResponseParams,
                                 f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/rf_phase_response.md",
    id=run_format.EXPERIMENT_ID, title="射频相位响应", category="characterization",
    family="z-chain", variant="rf-phase-response", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("DG4000", "DG900/DG4000（温控开关）", "MFLI", "GS200", "TEC103（可选设温）"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RfPhaseResponseParams.schema_version,
    acquisition_program="experiments/RfPhaseResponse.py",
    analysis_program="experiments/RfPhaseResponse_plot.py",
    wiring_notes=WIRING_NOTES,
    safety_notes=SAFETY_NOTES,
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
