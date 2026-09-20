"""静磁场灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import STATIC_SENSITIVITY
from ...experiments.typed import TypedWorkflowAdapter
from .models import StaticSensitivityParams


ADAPTER = TypedWorkflowAdapter(
    "static-sensitivity",
    "Static_Magnetic_Field_Sensitivity",
    StaticSensitivityParams,
    "lab_workflows.experiment_modules.static_sensitivity.workflow",
    "lab_workflows.experiment_modules.static_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="static-sensitivity",
    title="静磁场灵敏度",
    category="measurement",
    family="static-field",
    variant="dispersion-noise",
    description="采集 Z 扫场色散曲线和多量程噪声，计算静磁场灵敏度。",
    data_type="Static_Magnetic_Field_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "TEC103", "HF2"),
    required_mapping_keys=STATIC_SENSITIVITY,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Static_Magnetic_Field_Sensitivity.py",
    analysis_program="experiments/Static_Magnetic_Field_Sensitivity_plot.py",
    wiring_notes=(
        "Pump DG4000 CH1 为 100 MHz Gated Burst，外部门控输入接同机 CH2 脉冲。",
        "CH2 为 0–5 V 高有效门控脉冲，直接控制 CH1 载波是否送入 AOM。",
        "Z 扫场使用同机 CH1 RAMP、CH2 SYNC 触发 HF2 DAQ。",
    ),
    safety_notes=(
        "Pump、光功率、主磁场、Z 扫场和温控输出均在写入前通过安全限值。",
        "取消或异常时保留 Pump 载波策略，停止 Z 输出并恢复温控。",
    ),
    schema_version=StaticSensitivityParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
    auto_analyze=True,
)
