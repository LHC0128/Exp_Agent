"""Mx Keithley 6221 最优控制 平衡-灵敏度一体化实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxKeithley6221OptimalControlBalancedSensitivityParams

ADAPTER = TypedWorkflowAdapter(
    "mx-keithley-6221-optimal-control-balanced-sensitivity",
    "Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity",
    MxKeithley6221OptimalControlBalancedSensitivityParams,
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_balanced_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-keithley-6221-optimal-control-balanced-sensitivity",
    title="Mx Keithley 6221 最优控制 平衡-灵敏度一体化",
    category="measurement",
    family="sensitivity",
    variant="mx-keithley-6221-optimal-control-balanced",
    description=(
        "保持 Keithley 6221 外部触发 Z 最优控制，先做 XYZ 平衡粗扫（可选细扫）"
        "并由逐层复线性模拟合定位工作点，随即用拟合工作点配置 X/Y/GS200 并"
        "直接完成 Y RF 相位校准、带符号幅度扫描与零 Y RF 噪声采集，"
        "最小化平衡与灵敏度测量之间的剩磁漂移失配。"
    ),
    data_type="Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity",
    required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program=(
        "experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py"
    ),
    analysis_program=(
        "experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity_plot.py"
    ),
    wiring_notes=(
        "Keithley 6221 接 Z 小磁场线圈；GS200 接独立的 Z 主磁场线圈，运行前必须人工确认接线正确。",
        "Time_sequence_2 CH2 经分配后同时接 6221 Trigger Link Line 1 与 Y RF DG4000 Ext Trig；下降沿启动。",
        "X_magnetic_field 与 Y_magnetic_field（rf_coil）使用同一台 DG4000 的 CH1/CH2。",
        "平衡阶段 X/Y 为 DC 输出，RF 阶段 Y 通道切换为带 DC 偏置的外触发 Burst 正弦。",
        "HF2 Demod0 按理论 rf 频率采集 R；软件不能确认实体触发 BNC 接线。",
    ),
    safety_notes=(
        "所有 X/Y/GS200 输出、6221 控制包络与 Y RF 包络均在写入前执行安全限值校验。",
        "6221 任意波完整包络必须被所选固定电流档位覆盖；Compliance 固定 15 V，每个采集点前后检查。",
        "平衡阶段 X/Y 与 GS200 在零设定点也保持 Output ON，避免跨零切换输出状态。",
        "正常、取消和异常结束均恢复运行前 X/Y 波形及输出状态与 GS200 完整状态，"
        "关断 6221（ABORT + 输出关 + 归零）并归零关闭共同触发。",
        "温控恢复为 5 V DC + Output ON；Pump/Probe、Pump 载波/门控和 HF2 设置保持。",
    ),
    schema_version=MxKeithley6221OptimalControlBalancedSensitivityParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
