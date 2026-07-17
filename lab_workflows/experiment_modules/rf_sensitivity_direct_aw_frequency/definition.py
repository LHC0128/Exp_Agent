"""RF DirectAW 频率响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import RFDirectAWFrequencyParams


ADAPTER = TypedWorkflowAdapter(
    "rf-sensitivity-direct-aw-frequency",
    "RF_Field_Sensitivity_AW_FreqSweep_DirectAW",
    RFDirectAWFrequencyParams,
    "lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency.workflow",
    "lab_workflows.experiment_modules.rf_sensitivity_direct_aw_frequency.analysis",
)

DEFINITION = ExperimentDefinition(
    id="rf-sensitivity-direct-aw-frequency",
    title="RF 灵敏度频率扫描（DirectAW）",
    category="measurement",
    family="rf-sensitivity",
    variant="direct-aw",
    description="扫描 RF 频率的直接任意波方案。",
    data_type="RF_Field_Sensitivity_AW_FreqSweep_DirectAW",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py",
    analysis_program="experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py",
    wiring_notes=("确认 DirectAW 外触发、Z RF 连续正弦与 Demod0 Y 物理回环接线。",),
    safety_notes=("预检波形文件、触发电平和全部输出限值后才允许启动。",),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
