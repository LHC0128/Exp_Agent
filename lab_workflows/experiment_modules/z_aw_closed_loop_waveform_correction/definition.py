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
    description="由静态电流标定初始化命令，采集并平移对齐后低通电流误差，按静态斜率累加电压修正。",
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
        "误差增大仍继续；连续达标、轮数上限、电压越界、取消或硬件失败时停止。",
        "Z 输出沿用当前 50 Ω/Vpp 数值基准，回读一致后开启；最终只导出已实测命令。",
    ),
    schema_version=ZAWClosedLoopWaveformCorrectionParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
