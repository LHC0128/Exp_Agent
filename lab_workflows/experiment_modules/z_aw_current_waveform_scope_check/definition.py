"""Z 任意波实际电流波形验证实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ZAWCurrentWaveformScopeCheckParams


ADAPTER = TypedWorkflowAdapter(
    "z-aw-current-waveform-scope-check",
    "Z_AW_Current_Waveform_Scope_Check",
    ZAWCurrentWaveformScopeCheckParams,
    "lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.workflow",
    "lab_workflows.experiment_modules.z_aw_current_waveform_scope_check.analysis",
)

DEFINITION = ExperimentDefinition(
    id="z-aw-current-waveform-scope-check",
    title="Z 任意波实际电流波形验证",
    category="verification",
    family="hardware-chain",
    variant="optimal-control-current-sense",
    description="比较最优控制目标电流与采样电阻实测电流的波形、延迟和频谱误差。",
    data_type="Z_AW_Current_Waveform_Scope_Check",
    required_devices=("DG4000", "SDS"),
    required_mapping_keys=("Z_magnetic_field", "Time_sequence_2", "scope_waveform"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Z_AW_Current_Waveform_Scope_Check.py",
    analysis_program="experiments/Z_AW_Current_Waveform_Scope_Check_plot.py",
    wiring_notes=(
        "SDS CH3 测量低端采样电阻，CH4 接共同触发；CH3 不再跨接线圈。",
        "采样电阻低端与 DG4000 BNC 外壳共地。",
    ),
    safety_notes=(
        "采样电阻来源、功率和线圈峰值电流上限未通过预检时不输出任意波。",
    ),
    schema_version=ZAWCurrentWaveformScopeCheckParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
