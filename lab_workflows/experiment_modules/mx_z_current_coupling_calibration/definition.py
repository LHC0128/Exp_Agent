"""Mx Z 实际电流-耦合强度标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZCurrentCouplingCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-current-coupling-calibration",
    "Mx_Z_Current_Coupling_Calibration",
    MxZCurrentCouplingCalibrationParams,
    "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.workflow",
    "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-current-coupling-calibration",
    title="Mx Z 实际电流-耦合强度标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-current-sense",
    description="以采样电阻电压重建实际线圈电流，并标定其对应的 Z 控制耦合强度。",
    data_type="Mx_Z_Current_Coupling_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "SDS"),
    required_mapping_keys=(
        "main_magnetic_field",
        "Z_magnetic_field",
        "Time_sequence_2",
        "rf_coil",
        "scope_waveform",
    ),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Current_Coupling_Calibration.py",
    analysis_program="experiments/Mx_Z_Current_Coupling_Calibration_plot.py",
    wiring_notes=(
        "Z 线圈串联低端采样电阻，SDS CH3 跨接采样电阻；普通示波器地只接 DG 信号地。",
        "Mx 共振扫描沿用 mx-z-field-calibration 的主场、光路和 RF 接线。",
    ),
    safety_notes=(
        "实测阻值和额定功率未填写时预检失败。",
        "采样电阻电压仅用于实际电流重建，不把示波器地接到浮置高端。",
    ),
    schema_version=MxZCurrentCouplingCalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
