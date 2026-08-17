"""Mx Keithley 6221 最优控制 XYZ 平衡场实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxKeithley6221OptimalControlXYZBalanceParams

ADAPTER = TypedWorkflowAdapter(
    "mx-keithley-6221-optimal-control-xyz-balance",
    "Mx_Keithley_6221_Optimal_Control_XYZ_Balance",
    MxKeithley6221OptimalControlXYZBalanceParams,
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.workflow",
    "lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_xyz_balance.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-keithley-6221-optimal-control-xyz-balance",
    title="Mx Keithley 6221 最优控制 XYZ 平衡场",
    category="calibration",
    family="field-balance",
    variant="mx-keithley-6221-optimal-control",
    description=(
        "保持 Keithley 6221 外部触发 Z 最优控制，以 DG4000 DC 扫描 X/Y、"
        "GS200 电流扫描 Z，记录 HF2 Demod0 R 并报告实测三维网格最小点。"
    ),
    data_type="Mx_Keithley_6221_Optimal_Control_XYZ_Balance",
    required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py",
    analysis_program="experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance_plot.py",
    wiring_notes=(
        "Keithley 6221 接 Z 小磁场线圈；GS200 接独立的 Z 主磁场线圈，运行前必须人工确认接线正确。",
        "Time_sequence_2 CH2 经转接线接 6221 Trigger Link Line 1；Line 2 是触发输出，本实验不连接。",
        "6221 使用 FAST、Line 1、IGNORE=OFF、inactive 0 mA，下降沿启动单周期任意波。",
        "X_magnetic_field 与 Y_magnetic_field 使用同一台 DG4000 的 CH1/CH2 DC 输出。",
        "HF2 Demod0 按理论 rf 频率采集 R；软件不能确认实体触发 BNC 接线。",
    ),
    safety_notes=(
        "X/Y/GS200 输出和 6221 控制包络均在写入前执行安全限值校验。",
        "6221 任意波完整包络按 -100 到 100 mA 校验，并必须被所选固定电流档位完整覆盖；Compliance 固定 15 V。",
        "扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON，避免跨零切换输出状态。",
        "每个采集点前后检查 6221 Compliance；命中或查询失败时紧急关断 6221。",
        "正常、取消和异常结束均保持 6221 最优控制运行与 Time_sequence_2 触发输出，"
        "并恢复运行前 X/Y 波形及输出状态和 GS200 完整状态。",
        "温控恢复为 5 V DC + Output ON；Pump/Probe、Pump 载波/门控和 HF2 设置保持。",
    ),
    schema_version=MxKeithley6221OptimalControlXYZBalanceParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
