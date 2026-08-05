"""Mx 主磁场频率标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxMainFieldCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-main-field-calibration",
    "Mx_Main_Field_Calibration",
    MxMainFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_main_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_main_field_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-main-field-calibration",
    title="Mx 主磁场频率标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-gs200",
    description="在 Mx 构型下扫描 GS200 主场电流，以 Y RF 共振中心标定 Hz/mA 和 nT/mA。",
    data_type="Mx_Main_Field_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Main_Field_Calibration.py",
    analysis_program="experiments/Mx_Main_Field_Calibration_plot.py",
    wiring_notes=(
        "主场和 Pump 光沿 Z、Probe 光沿 X；GS200 提供扫描主场，Z_magnetic_field CH1 保持 0 V OFF。",
        "rf_coil CH2 提供 Y 向 RF，HF2 Demod0 逐频点直接解调 R。",
    ),
    safety_notes=(
        "每个频点关闭温控并统一等待一次；完成后恢复温控 5 V ON。",
        "正常、取消和异常均恢复 GS200 运行前状态，并归零关闭 Z 辅助场、X 场和 Y RF。",
    ),
    schema_version=MxMainFieldCalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
