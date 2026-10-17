"""Z 任意波实际电流闭环波形校正实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ZAWClosedLoopWaveformCorrectionParams


ADAPTER = TypedWorkflowAdapter(
    "z-aw-closed-loop-waveform-correction",
    "Z_AW_Closed_Loop_Waveform_Correction",
    ZAWClosedLoopWaveformCorrectionParams,
    "lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.workflow",
    "lab_workflows.experiment_modules.z_aw_closed_loop_waveform_correction.analysis",
)

DEFINITION = ExperimentDefinition(
    id="z-aw-closed-loop-waveform-correction",
    title="Z 任意波实际电流闭环波形校正",
    category="optimization",
    family="hardware-chain",
    variant="iterative-current-feedback",
    description="复用 XY 双列分步界面，在节点内编辑闭环来源、Scope 与迭代参数；支持谐波辨识、时域反馈和频响预加重，保留 CH3 实际电流与 CH4 下降沿参考。应用草稿后显式重跑，每轮结束关闭 Z 并保留全部尝试；具体目录独立分步分析，无分析覆盖参数。",
    data_type="Z_AW_Closed_Loop_Waveform_Correction",
    required_devices=("DG4000", "SDS"),
    required_mapping_keys=("Z_magnetic_field", "Time_sequence_2", "scope_waveform"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Z_AW_Closed_Loop_Waveform_Correction.py",
    analysis_program="experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py",
    wiring_notes=(
        "SDS CH3 测量低端采样电阻，CH4 提供共同触发；每轮使用同一触发和采样配置。",
    ),
    safety_notes=(
        "理论采样电阻电压乘 1.5 倍余量选择 CH3 档位；每轮检查 DG 电压及 CH3 量程。",
        "时域模式只需 CH3 电流和 CH4 触发；频响预加重模式才要求有效频响来源。",
        "时域与频响反馈误差增大仍继续；谐波模式按独立验证决定接受或真实回退。",
        "Z 输出沿用当前 50 Ω/Vpp 数值基准，回读一致后开启；最终只导出已实测命令。",
        "每轮结束、取消、异常与 EOF 均停止 SDS 并关闭 Z；共同触发保持当前状态，结束会话后释放硬件锁。",
    ),
    schema_version=ZAWClosedLoopWaveformCorrectionParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
