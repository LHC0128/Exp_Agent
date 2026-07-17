"""XY DirectAW DC 标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import XYDirectAWDCCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "xy-direct-aw-dc-calibration",
    "XY_DirectAW_DC_Calibration",
    XYDirectAWDCCalibrationParams,
    "lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.workflow",
    "lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="xy-direct-aw-dc-calibration",
    title="XY DirectAW DC 标定",
    category="calibration",
    family="xy-calibration",
    variant="direct-aw",
    description="标定 DirectAW 方案的 XY DC 响应。",
    data_type="XY_DirectAW_DC_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/XY_DirectAW_DC_Calibration.py",
    analysis_program="experiments/XY_DirectAW_DC_Calibration_plot.py",
    wiring_notes=("运行前按实验文档和 mapping.yaml 核对 DirectAW、Z RF 与物理回环接线。",),
    safety_notes=("预检通过后才允许启动输出；异常和取消时恢复温控并关闭磁场输出。",),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
