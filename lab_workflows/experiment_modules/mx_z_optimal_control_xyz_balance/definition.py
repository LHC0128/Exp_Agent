"""Mx Z 最优控制 XYZ 平衡场实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlXYZBalanceParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-xyz-balance",
    "Mx_Z_Optimal_Control_XYZ_Balance",
    MxZOptimalControlXYZBalanceParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_xyz_balance.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-xyz-balance",
    title="Mx Z 最优控制 XYZ 平衡场",
    category="calibration",
    family="field-balance",
    variant="mx-z-optimal-control-xyz-dc",
    description=(
        "保持 Z 周期最优控制，以 DG4000 DC 扫描 X/Y、GS200 电流扫描 Z，"
        "记录 HF2 Demod0 R 并报告实测三维网格最小点。"
    ),
    data_type="Mx_Z_Optimal_Control_XYZ_Balance",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_XYZ_Balance.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py",
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出最优控制；Time_sequence_2 CH2 接该 DG4000 的 Ext Trig。",
        "X_magnetic_field 与 Y_magnetic_field 使用同一台 DG4000 的 CH1/CH2 DC 输出。",
        "main_magnetic_field GS200 作为 Z 方向平衡场扫描源，与 DG4000 Z 最优控制叠加。",
        "HF2 Demod0 按 DEMOD_FREQUENCY_HZ 采集 R；软件不能确认实体触发 BNC 接线。",
    ),
    safety_notes=(
        "X/Y/GS200 和最优控制输出均在写入前执行安全限值校验。",
        "扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON，避免跨零切换输出状态。",
        "正常、取消和异常结束均恢复运行前 X/Y 波形及输出状态和 GS200 完整状态。",
        "所有结束路径关闭归零 Time_sequence_2，并保留 Z 最优控制波形和 Output ON。",
        "温控恢复为 5 V DC + Output ON；Pump/Probe、Pump 载波/门控和 HF2 设置保持。",
    ),
    schema_version=MxZOptimalControlXYZBalanceParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
