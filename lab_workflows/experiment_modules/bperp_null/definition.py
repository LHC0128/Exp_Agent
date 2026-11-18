"""横向静态场补平实验的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import BperpNullParams
from .preparation import preflight

DESCRIPTION = (
    "关射频，用 Z 向连续 +π/−π 双脉冲和 MFLI 两个解调器测 1f/2f 复振幅："
    "第 0 路谐波 1 读 5 kHz，第 1 路谐波 2 读 10 kHz（共用 5 kHz 振荡器、相位 0）。"
    "静态横向场 Bx、By 在两个半周期里作用相同，只进入 10 kHz 偶部；"
    "拖尾、α≠π 与拖尾横向分量随脉冲反号，主要进入 5 kHz 奇部。"
    "所以 10 kHz 复振幅 c2 = 0 即 B⊥ = 0，与 Bz、拖尾和脉宽无关。"
    "A02 连接 DG、MFLI、1200B、GS200：写 1200B 增益与阻抗、配平 Z 负载、配置两个解调器、"
    "按 X_BIAS_MV/Y_BIAS_MV 给 X/Y 线圈加直流（UI 以 mV 输入，后端换算成 V）（Y 通道不出射频）、关闭 GS200 主磁场并回读、严格回读参考时钟。"
    "A03 先上传 Z 任意波：默认内置 ±π 升余弦脉冲；Z_WAVEFORM_FROM_FILE 开启时上传 data/z_waveforms/ 下"
    "选定的一个周期波形文件（如拖尾补偿波形，峰值仍为 Z_PEAK_CURRENT_MA），原样复制到 raw/z_waveform.csv 并记录 SHA-256。"
    "随后在 X_BIAS_MV/Y_BIAS_MV 附近按各自 start/stop/step 扫 X/Y 偏移网格，每个点重复 N_REPEAT 次；"
    "两路解调器在同一个 DAQ 采集里同时读 X/Y，温控关闭窗口内只读不写盘，组末恢复温控后落盘。"
    "A04 安全收尾：先关 1200B，Z 归零并关 burst/sync，X/Y 归零关输出，恢复温控与 GS200 原状态。"
    "B01 按 (x,y) 聚合重复读数，对 c2 做复平面拟合 c=a+b·x+c·y，解 c=0 得补偿电压与蒙特卡洛不确定度；"
    "同法给 5 kHz 零点，补偿点处的残余 |c1| 与两个零点之差作为拖尾横向分量的指示量；"
    "写 null_summary.json、next_run.yaml（下一轮建议的 X_BIAS_MV/Y_BIAS_MV 与扫描范围）和两张图。"
    "本实验只报直流电压与 mV/V 斜率，不做线圈常数或磁场换算。"
)


class BperpNullAdapter(TypedWorkflowAdapter):
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


MODULE = "lab_workflows.experiment_modules.bperp_null"
ADAPTER = BperpNullAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, BperpNullParams,
                           f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/bperp_null.md",
    id=run_format.EXPERIMENT_ID, title="横向静态场补平（10 kHz 偶部）", category="calibration",
    family="z-chain", variant="bperp-null-2f", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("DG4000", "MFLI", "Aigtek 1200B", "GS200", "TEC103"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=BperpNullParams.schema_version,
    acquisition_program="experiments/BperpNull.py",
    analysis_program="experiments/BperpNull_plot.py",
    wiring_notes=(
        "Z_magnetic_field → 1200B 输入；按 AMPLIFIER_INPUT_IMPEDANCE_OHM 写输入阻抗并把信号源负载配成一致。",
        "1200B 正端 → 阻尼电阻 → Z 线圈 → 采样电阻 → 1200B 返回地；输出 DC 耦合、单端对地。",
        "Time_sequence_2 主输出（脉冲频率方波）接 Z 通道外触发；Z 用无限 burst，由触发沿启动。",
        "X_magnetic_field 与 Y_magnetic_field 分别是 X、Y 线圈的直流偏置；本实验关射频，Y 通道不输出正弦。",
        "MFLI Signal Input 1 接探测器输出；振荡器 0 设为脉冲频率，第 0 路谐波 1 读 5 kHz、第 1 路谐波 2 读 10 kHz。",
    ),
    safety_notes=(
        "X_magnetic_field、Y_magnetic_field、Z_magnetic_field 与 z_pulse_coil_current 必须有有限上下界；"
        "脉冲峰值、触发电平与电阻功耗在连接前预检。",
        "A02 关闭 GS200 主磁场输出并回读为 OFF 后才继续；主磁场原状态在 A04 恢复。",
        "A02 在功放输出关闭、信号源停输出时写增益与阻抗；带载改这些设置被安全回调拒绝。",
        "温控关闭窗口内不写任何文件；每 TEMP_GROUP_SIZE 个读数恢复温控后再落盘，故障时先恢复温控再抢救已读数据。",
        "结束时先关 1200B 再把 Z 通道归零并关 burst/sync，X/Y 偏置归零；时序信号2 按共同触发保持运行。",
    ),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
