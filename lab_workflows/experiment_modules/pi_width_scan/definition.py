"""π 脉冲宽度原子域定标的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import PiWidthScanParams
from .preparation import preflight

DESCRIPTION = (
    "在原子域给 π 脉冲宽度定标：Y 向 5 kHz 射频持续激励并用 MFLI 第 0 路解调器读该频率上的 R/θ，"
    "Z 通道由 Time_sequence_2 外触发无限 burst、连续打 +π 与 −π 各一个脉冲（相隔半个周期）；"
    "按 start/stop/step 扫单脉冲宽度，逐点重复 N_REPEAT 次；"
    "判据 R(τ)=R_max|sin(π(τ−τ0)/(2τ_π))| 的峰位即 π 宽度，并与 REFERENCE_PI_WIDTH_US（默认 5.122 µs）对照。"
    "Z 通道链路（DG → 1200B → 阻尼电阻 → Z 线圈 → 采样电阻）与 π 脉冲面积标定完全一致，"
    "但信号源改为外触发无限 burst 门控；MFLI 第 0 路解调器按 DirectAW 校相档配置（1 ms、100 kSa/s）。"
    "A02 连接 DG、MFLI、1200B、DHO900 与 GS200：写 1200B 增益与阻抗、配平 Z 通道负载、"
    "配置第 0 路解调器并把参考频率设为射频频率，射频按无限 burst 与 Y_RF_PHASE_DEG 起振；"
    "关闭 GS200 主磁场输出并回读，参考时钟严格回读。"
    "A03 射频相位粗扫（可勾选）：Z 脉冲串按预测 π 宽度只写一次，之后逐角度按 DirectAW 顺序改射频 burst 相位"
    "（关 burst → 写相位 → 开 burst → 开 output），读 Y_RF_PHASE_REPEAT 次 R/θ 写 raw/phase_scan.csv 与 "
    "results/phase_vs_phase.png；取 R 峰并对相邻三点做抛物线精化，结果记入运行配置。"
    "A04 扫脉宽：射频相位取 A03 的选中/精化值，A03 未运行或关闭时回落 Y_RF_PHASE_DEG 并记 phase_source；"
    "逐点上传脉宽、关闭温控并等待 SYSTEM_SETTLE_S，在关闭窗口内连续采 SCAN_POINT_DURATION_S，"
    "按 TEMP_GROUP_SIZE 分组，组末恢复温控并等待 TEMP_SWITCH_ON_LAG_S 后再落盘；"
    "关温控窗口内不写文件，取消保留已完成点。A05 先关 1200B，Z 通道归零并关 burst/sync，"
    "关 Y 射频、停解调传输与示波器，恢复温控与 GS200 原状态后断开设备；时序信号2 作为共同触发保持运行。"
    "B01 按宽度汇总均值与标准误做加权拟合，给出 τπ±1σ、R_max、残差 RMS 与 keep 判据，"
    "残差 RMS 超过中位标准误 3 倍时标记拟合质量差，R_max 小于中位标准误 5 倍时提示 signal_low；"
    "摘要记录实际生效的射频相位、对应时间偏移、phase_source、相位粗扫曲线与 MFLI 解调相位回读；"
    "不做漂移校正。"
)


class PiWidthScanAdapter(TypedWorkflowAdapter):
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


MODULE = "lab_workflows.experiment_modules.pi_width_scan"
ADAPTER = PiWidthScanAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, PiWidthScanParams,
                             f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/pi_width_scan.md",
    id=run_format.EXPERIMENT_ID, title="π 脉冲宽度原子域定标", category="calibration",
    family="z-chain", variant="pi-width-atomic", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("DG4000", "MFLI", "Aigtek 1200B", "DHO900", "GS200", "TEC103"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=PiWidthScanParams.schema_version,
    acquisition_program="experiments/PiWidthScan.py",
    analysis_program="experiments/PiWidthScan_plot.py",
    wiring_notes=(
        "Z_magnetic_field → 1200B 输入；按 AMPLIFIER_INPUT_IMPEDANCE_OHM 写输入阻抗并把信号源负载配成一致。",
        "1200B 正端 → 阻尼电阻 → Z 线圈 → 采样电阻 → 1200B 返回地；输出 DC 耦合、单端对地。",
        "Time_sequence_2 主输出（5 kHz 方波）接 Z 通道与 Y 射频的外触发输入；两条通道都用无限 burst，"
        "由同一个触发沿启动，射频相位只由 burst 相位决定。",
        "Y_magnetic_field 输出 5 kHz 正弦驱动 Y 向射频场；MFLI Signal Input 1 接探测器输出，参考频率设为射频频率。",
        "DHO900 接采样电阻用于人工核对脉冲波形，本实验不采集示波器数据。",
    ),
    safety_notes=(
        "z_pulse_coil_current 与 Z/Y 场必须有有限上下界；脉冲峰值、射频电平、触发电平与电阻功耗在连接前预检。",
        "A02 关闭 GS200 主磁场输出并回读为 OFF 后才继续；主磁场原状态在 A05 恢复。",
        "A02 在功放输出关闭、信号源停输出时写增益与阻抗；带载改这些设置被安全回调拒绝。",
        "温控关闭窗口内不写任何文件；每 TEMP_GROUP_SIZE 个读数恢复温控后再落盘，故障时先恢复温控再抢救已读数据。",
        "结束时先关 1200B 再把 Z 通道归零并关 burst/sync；时序信号2 按共同触发保持运行，Z burst 已关闭故无脉冲。",
    ),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
