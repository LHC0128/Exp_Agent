"""Z 线圈电感效应频率响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ZCoilInductanceFrequencyResponseParams


ADAPTER = TypedWorkflowAdapter(
    "z-coil-inductance-frequency-response",
    "Z_Coil_Inductance_Frequency_Response",
    ZCoilInductanceFrequencyResponseParams,
    "lab_workflows.experiment_modules.z_coil_inductance_frequency_response.workflow",
    "lab_workflows.experiment_modules.z_coil_inductance_frequency_response.analysis",
)

DEFINITION = ExperimentDefinition(
    id="z-coil-inductance-frequency-response",
    title="Z 线圈电感效应频率响应",
    category="measurement",
    family="field-response",
    variant="scope-sine-sweep",
    description=(
        "使用固定幅度的 Z 方向正弦驱动和 SDS 双通道采集，比较理想 DG 设定值"
        "与 Z 线圈端电压的幅值、相位和波形差异。"
    ),
    data_type="Z_Coil_Inductance_Frequency_Response",
    required_devices=("DG4000", "SDS"),
    required_mapping_keys=("Z_magnetic_field", "Time_sequence_2", "scope_waveform"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Z_Coil_Inductance_Frequency_Response.py",
    analysis_program="experiments/Z_Coil_Inductance_Frequency_Response_plot.py",
    wiring_notes=(
        "Time_sequence_2 CH2 接 Z_magnetic_field CH1 Ext Trig，使用共同下降沿触发。",
        "Z 线圈两端接 SDS CH3，SDS CH4 接共同触发参考。",
        "SDS CH3/CH4 使用 1 MOhm、DC、1x；CH4 以 2.5 V 下降沿触发。",
    ),
    safety_notes=(
        "Z 驱动和共同触发写入前执行 safety limit 校验。",
        "正常、取消和异常结束均停止 SDS，并将 Z CH1 与触发 CH2 归零后关闭输出。",
        "该实验观察端电压频率响应，不从单一端电压测量反演唯一电感值。",
    ),
    schema_version=ZCoilInductanceFrequencyResponseParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
