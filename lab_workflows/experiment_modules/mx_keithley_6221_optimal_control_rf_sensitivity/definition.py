"""Mx Keithley 6221 主场最优控制 RF 灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxKeithley6221OptimalControlRFParams

ADAPTER = TypedWorkflowAdapter(
    "mx-keithley-6221-optimal-control-rf-sensitivity",
    "Mx_Keithley_6221_Optimal_Control_RF_Sensitivity",
    MxKeithley6221OptimalControlRFParams,
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-keithley-6221-optimal-control-rf-sensitivity",
    title="Mx Keithley 6221 最优控制 RF 灵敏度",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-keithley-6221-optimal-control",
    description="使用 Keithley 6221 在 Z 小磁场线圈输出每个触发对应的一周期最优控制任意波，同时由 GS200 驱动独立的 Z 主磁场线圈。",
    data_type="Mx_Keithley_6221_Optimal_Control_RF_Sensitivity",
    required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py",
    analysis_program="experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py",
    wiring_notes=(
        "Keithley 6221 接 Z 小磁场线圈；GS200 接独立的 Z 主磁场线圈，运行前必须人工确认接线正确。",
        "Time_sequence_2 CH2 经 BNC T 一路接 6221 转接线 Line 1，另一路接 Y RF DG4000 Ext Trig。",
        "6221 Line 2 是触发输出，本实验不连接；使用 FAST、Line 1、IGNORE=OFF、inactive 0 mA。",
        "运行前人工核对接线和 INTERLOCK。",
    ),
    safety_notes=(
        "GS200 主磁场电流可为非零值，程序设置后回读确认；6221 与 GS200 不接同一线圈。",
        "6221 任意波完整包络按 -100 到 100 mA 校验，并必须被所选固定电流档位完整覆盖；Compliance 固定 15 V。",
        "正常、取消和异常结束后 6221 与 GS200 均归零并关闭输出。",
        "IGNORE=OFF 时新触发会立即重启波形，周期略短可能截断尾部采样点。",
    ),
    schema_version=MxKeithley6221OptimalControlRFParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
