"""Mx 高主场 Z 磁场频率标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZFieldCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-field-calibration",
    "Mx_Z_Field_Calibration",
    MxZFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_z_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_z_field_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-field-calibration",
    title="Mx 高主场 Z 磁场频率标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-y-rf",
    description="在 9.3 mA 高主场 Mx 构型下，以 Y RF 共振中心随 Z DC 电压的线性移动标定 Z 通道 Hz/V。",
    data_type="Mx_Z_Field_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Field_Calibration.py",
    analysis_program="experiments/Mx_Z_Field_Calibration_plot.py",
    wiring_notes=(
        "主场和 Pump 光沿 Z、Probe 光沿 X；Z_magnetic_field CH1 提供 DC 偏置，rf_coil CH2 提供 Y 向 RF。",
        "HF2 Demod0 以每个 Y RF 扫频点直接解调，只采集 R。",
    ),
    safety_notes=(
        "每个频点关闭温控采集并立即恢复 5 V ON；Z 偏置切换后不设置专用等待。",
        "正常、取消和异常均归零关闭 Z 偏置与 Y RF，保留主场、光功率、Pump 链路和 HF2。",
    ),
    schema_version=MxZFieldCalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)

