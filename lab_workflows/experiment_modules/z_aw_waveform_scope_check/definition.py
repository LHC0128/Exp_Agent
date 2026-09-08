"""Z 任意波线圈波形一致性验证实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ZAWWaveformScopeCheckParams


ADAPTER = TypedWorkflowAdapter(
    "z-aw-waveform-scope-check",
    "Z_AW_Waveform_Scope_Check",
    ZAWWaveformScopeCheckParams,
    "lab_workflows.experiment_modules.z_aw_waveform_scope_check.workflow",
    "lab_workflows.experiment_modules.z_aw_waveform_scope_check.analysis",
)

DEFINITION = ExperimentDefinition(
    id="z-aw-waveform-scope-check",
    title="Z 任意波线圈波形一致性验证",
    category="verification",
    family="hardware-chain",
    variant="optimal-control-scope",
    description="验证 Z 最优控制任意波经 Z 小线圈后的实测波形与理论波形的一致性。",
    data_type="Z_AW_Waveform_Scope_Check",
    required_devices=("DG4000", "SDS"),
    required_mapping_keys=("Z_magnetic_field", "Time_sequence_2", "scope_waveform"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Z_AW_Waveform_Scope_Check.py",
    analysis_program="experiments/Z_AW_Waveform_Scope_Check_plot.py",
    wiring_notes=(
        "Time_sequence_2 CH2 已接入 Z_magnetic_field CH1 Ext Trig，使用外部下降沿 Burst。",
        "Z 小线圈两端接入 SDS CH3；SDS CH4 接收同一触发方波。",
        "SDS CH3/CH4 使用 1 MΩ、DC、1×；CH4 以 2.5 V 下降沿触发。",
    ),
    safety_notes=(
        "只控制 Z 任意波、共同触发和 SDS，不启动 GS200、激光、TEC 或 HF2。",
        "正常、取消和异常结束均将 Z CH1 与 Time_sequence_2 CH2 设置为 DC 0 V、Output OFF。",
        "波形、触发电平和输出范围在写入前执行安全校验。",
    ),
    schema_version=ZAWWaveformScopeCheckParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
