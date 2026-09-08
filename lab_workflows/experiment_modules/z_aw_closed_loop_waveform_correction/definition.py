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
    description="根据采样电阻实测电流，使用正则化逆传递函数迭代生成冻结校正波形。",
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
        "每轮更新均检查 DG 输出范围、线圈电流上限和采样电阻 RMS 功率。",
        "频响可靠频点之外不执行逆滤波。",
    ),
    schema_version=ZAWClosedLoopWaveformCorrectionParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
