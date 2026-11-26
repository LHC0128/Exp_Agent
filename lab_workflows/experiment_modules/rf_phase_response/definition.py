"""射频相位响应的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import RfPhaseResponseParams
from .preparation import preflight

DESCRIPTION = (
    "在原子域表征射频相位响应：Y 向 5 kHz 射频持续激励并用 MFLI 第 0 路解调器读该频率上的 R/θ，"
    "Z 通道由 Time_sequence_2 外触发无限 burst、连续打 +π 与 −π 各一个固定宽度脉冲；"
    "按 Y_RF_PHASE_START/STOP/STEP_DEG 扫射频 burst 相位（默认 0–360°、15°，24 点），"
    "每点读 N_REPEAT 次；离线把复数读数分解为 Z(φ)=c0+c+1·e^{iφ}+c−1·e^{−iφ}，"
    "给出三分量、模型残差与最佳读相位（模型 |Z| 最大处），并与实测 R 最大相位对照。"
    "本实验的 1200B 功放已物理旁路，DG4000 直驱 Z 线圈：Z 脉冲按 Z_PULSE_VPP 设电压"
    "（默认 20 Vpp，DG 高阻档上限），线圈峰值电流按已知回路电阻估算、不是直接测量值；"
    "拖尾补偿默认关闭（只发原升余弦 ±脉冲），Z_PULSE_WIDTH_US、拖尾 s_f/τe、X/Y 偏置与解调配置均可调。"
    "A02 连接 DG、MFLI、DHO900 与 GS200：配平 Z 通道高阻负载、"
    "配置第 0 路解调器并把参考频率设为射频频率，射频按无限 burst 与 Y_RF_PHASE_DEG 起振；"
    "关闭 GS200 主磁场输出并回读，参考时钟严格回读。A03 逐相位按 DirectAW 顺序改 burst 相位"
    "（关 burst → 写相位 → 开 burst → 开 output）后读数，按 TEMP_GROUP_SIZE 分组采集、"
    "关温控窗口内不写盘，取消保留已完成读数。A04 Z 通道归零并关 burst/sync，"
    "关 Y 射频、停解调传输与示波器，恢复温控与 GS200 原状态后断开设备；时序信号2 作为共同触发保持运行。"
)

WIRING_NOTES = (
    "Z_magnetic_field 直连 Z 线圈（1200B 已物理旁路），DG 负载配成高阻：写入幅度即开路电动势，"
    "回路电阻计入 Z_DRIVE_SOURCE_IMPEDANCE_OHM（DG4000 源阻抗 50 Ω）+ 阻尼 + 线圈 + 采样。",
    "Time_sequence_2 主输出（5 kHz 方波）接 Z 通道与 Y 射频的外触发输入；两条通道都用无限 burst，"
    "由同一个触发沿启动，射频相位只由 burst 相位决定。",
    "Y_magnetic_field 输出 5 kHz 正弦驱动 Y 向射频场；MFLI Signal Input 1 接探测器输出，参考频率设为射频频率。",
    "DHO900 接采样电阻用于人工核对脉冲波形，本实验不采集示波器数据。",
)

SAFETY_NOTES = (
    "z_pulse_coil_current 与 Z/Y 场必须有有限上下界；Z_PULSE_VPP/2、按回路电阻估算的峰值电流、"
    "射频电平、触发电平与电阻功耗在连接前预检；Z_PULSE_VPP 不得超过 DG4000 高阻档上限 20 Vpp。",
    "线圈电流为按已知回路电阻估算，非直接测量；判据与报告都标注估算值。",
    "A02 关闭 GS200 主磁场输出并回读为 OFF 后才继续；主磁场原状态在 A04 恢复。",
    "温控关闭窗口内不写任何文件；每 TEMP_GROUP_SIZE 个读数恢复温控后再落盘，故障时先恢复温控再抢救已读数据。",
    "结束时先把 Z 通道归零并关 burst/sync；时序信号2 按共同触发保持运行，Z burst 已关闭故无脉冲。",
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
        return {"run_dir": str(run_dir), "artifacts": [str(p) for p in (run_dir / "results").iterdir() if p.is_file()]}


MODULE = "lab_workflows.experiment_modules.rf_phase_response"
ADAPTER = RfPhaseResponseAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RfPhaseResponseParams,
                                 f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/rf_phase_response.md",
    id=run_format.EXPERIMENT_ID, title="射频相位响应", category="characterization",
    family="z-chain", variant="rf-phase-response", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("DG4000", "MFLI", "DHO900", "GS200", "TEC103"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RfPhaseResponseParams.schema_version,
    acquisition_program="experiments/RfPhaseResponse.py",
    analysis_program="experiments/RfPhaseResponse_plot.py",
    wiring_notes=WIRING_NOTES,
    safety_notes=SAFETY_NOTES,
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
