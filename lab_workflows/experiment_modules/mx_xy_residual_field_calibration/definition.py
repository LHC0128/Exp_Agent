"""Mx XY 剩磁二维校准实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxXYResidualFieldCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-xy-residual-field-calibration",
    "Mx_XY_Residual_Field_Calibration",
    MxXYResidualFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_xy_residual_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_xy_residual_field_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-xy-residual-field-calibration",
    title="Mx XY 剩磁二维校准",
    category="calibration",
    family="xy-calibration",
    variant="mx-direct-dc-scope",
    description="直接读取 SDS CH1 平衡探测器直流均值，二维扫描 X/Y DC 补偿场并用 Bloch 曲面拟合剩磁平衡点。",
    data_type="Mx_XY_Residual_Field_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "SDS", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_XY_Residual_Field_Calibration.py",
    analysis_program="experiments/Mx_XY_Residual_Field_Calibration_plot.py",
    wiring_notes=(
        "主场和 Pump 光沿 Z、Probe 光沿 X；平衡探测器原始输出连接 SDS CH1。",
        "X_magnetic_field 与 Y_magnetic_field 使用同一台 DG4000 的 CH1/CH2 直流输出。",
    ),
    safety_notes=(
        "每次示波器采集关闭温控，采集后恢复 5 V ON。",
        "结束后恢复 GS200 运行前状态，X/Y/Z 场归零关闭，不保留最佳补偿输出。",
    ),
    schema_version=MxXYResidualFieldCalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
