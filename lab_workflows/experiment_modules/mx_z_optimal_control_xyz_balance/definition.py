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
        "保持 Z 周期最优控制，以 DG4000 DC 蛇形扫描 X/Y、GS200 电流扫描 Z，采集 锁相放大器 Demod0 R，"
        "按 R 最小、采集最早选择实测三维网格最小点，并附逐层一维 V 形拟合工作点诊断。"
        "运行/历史双 Tab 提供 6 个采集及 3 个独立分析节点；顶部保存、预检与完整运行，节点内编辑共享参数，应用草稿后显式重跑。"
        "理论来源冻结并校验内容，分析绑定明确目录且无额外覆盖值。"
        "锁相按设备库选择 HF2/MFLI 和 DAQ/Scope，输入保持高阻、DC 耦合。"
        "配置后关闭温控、按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后写盘；TEC 只写 UI 目标。"
        "结束恢复 GS200 与运行前 X/Y 原态，保持 Z 最优控制与共同触发当前状态。"
    ),
    data_type="Mx_Z_Optimal_Control_XYZ_Balance",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_XYZ_Balance.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py",
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出最优控制；Time_sequence_2 CH2 接该 DG4000 的 Ext Trig，并使用下降沿启动 Burst。",
        "X_magnetic_field 与 Y_magnetic_field 使用同一台 DG4000 的 CH1/CH2 DC 输出。",
        "main_magnetic_field GS200 作为 Z 方向平衡场扫描源，与 DG4000 Z 最优控制叠加。",
        "锁相放大器 Demod0 按 DEMOD_FREQUENCY_HZ 采集 R；软件不能确认实体触发 BNC 接线。",
    ),
    safety_notes=(
        "X/Y/GS200 和最优控制输出均在写入前执行安全限值校验。",
        "扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON，避免跨零切换输出状态。",
        "正常、取消和异常结束均恢复运行前 X/Y 波形及输出状态和 GS200 完整状态。",
        "所有结束路径保持 Time_sequence_2 当前状态，并保留 Z 最优控制波形和 Output ON。",
        "温控恢复为 5 V DC + Output ON；Pump/Probe、Pump 载波/门控和 锁相放大器 设置保持。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
