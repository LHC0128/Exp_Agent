"""Mx XY 剩磁二维校准实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxXYResidualFieldCalibrationParams
from .preparation import preflight as validate_preparation
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-xy-residual-field-calibration",
    "Mx_XY_Residual_Field_Calibration",
    MxXYResidualFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_xy_residual_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_xy_residual_field_calibration.analysis",
)

def preflight(values):
    errors = ADAPTER.preflight(values)
    if not errors:
        try:
            validate_preparation(ADAPTER._resolved(values), ADAPTER.root)
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
            errors.append(str(exc))
    return errors


DEFINITION = ExperimentDefinition(
    id="mx-xy-residual-field-calibration",
    title="Mx XY 剩磁二维校准",
    category="calibration",
    family="xy-calibration",
    variant="mx-direct-dc-scope",
    description=("直接读取 SDS CH1 直流均值，按 X 外层、Y 同向扫描，以二维 Bloch 与线性漂移拟合剩磁平衡点。"
                 "运行/历史双 Tab 提供可勾选依赖流程图、节点参数、草稿应用与显式重跑；独立分析绑定具体目录，无额外覆盖参数。"
                 "逐次温控恢复后保存，TEC 仅设目标温度；结束恢复 GS200，XYZ 归零关闭，不施加拟合补偿值。分步角色由清单声明，状态与产物以执行记录为准。"),
    data_type="Mx_XY_Residual_Field_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "SDS"),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
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
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
