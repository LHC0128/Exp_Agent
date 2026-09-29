"""Z 线圈实际电流频率响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ZCoilCurrentFrequencyResponseParams


ADAPTER = TypedWorkflowAdapter(
    "z-coil-current-frequency-response",
    "Z_Coil_Current_Frequency_Response",
    ZCoilCurrentFrequencyResponseParams,
    "lab_workflows.experiment_modules.z_coil_current_frequency_response.workflow",
    "lab_workflows.experiment_modules.z_coil_current_frequency_response.analysis",
)

DEFINITION = ExperimentDefinition(
    id="z-coil-current-frequency-response",
    title="Z 线圈实际电流频率响应",
    category="calibration",
    family="field-response",
    variant="coherent-swept-sine-log-current",
    description=(
        "沿对数频率轴测量 Z 线圈实际电流频率响应；可不接示波器 CH1，"
        "此时按 DG 设定幅值归一，结果用于测量，不作为闭环相位标定。"
    ),
    data_type="Z_Coil_Current_Frequency_Response",
    required_devices=("DG4000", "SDS"),
    required_mapping_keys=("Z_magnetic_field", "Time_sequence_2", "scope_waveform"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Z_Coil_Current_Frequency_Response.py",
    analysis_program="experiments/Z_Coil_Current_Frequency_Response_plot.py",
    wiring_notes=(
        "采样电阻必须位于线圈回路低端，SDS CH3 跨接采样电阻且外壳接 DG 信号地。",
        "SDS CH1 驱动参考为可选；未接时 SCOPE_REFERENCE_CHANNEL=0，"
        "只用 DG 设定幅值归一，保留相对触发相位。",
        "Z 输出使用内置正弦、外部下降沿无限 Burst 与 50 Ω/Vpp 数值基准，每帧相干重触发。",
        "SDS CH3 使用 1 MOhm、DC、1x；CH4 接共同触发并使用 2.5 V 下降沿。",
    ),
    safety_notes=(
        "阻值、额定功率和线圈峰值电流上限未填写时预检失败。",
        "禁止使用普通示波器通道测量高端浮置采样电阻。",
        "只有使用有效的同帧 CH1 驱动参考时，响应相位才可用于闭环；"
        "未覆盖谐波不参与修正。",
    ),
    schema_version=ZCoilCurrentFrequencyResponseParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    derive_runner=ADAPTER.derive,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
