"""Mx Z DG4000 偏置 XYZ 平衡场实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlDG4000BiasXYZBalanceParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-dg4000-bias-xyz-balance",
    "Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance",
    MxZOptimalControlDG4000BiasXYZBalanceParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-dg4000-bias-xyz-balance",
    title="Mx Z 最优控制 DG4000 偏置 XYZ 平衡场",
    category="calibration",
    family="field-balance",
    variant="mx-z-optimal-control-dg4000-bias",
    description=(
        "保持 Z 周期最优控制，以同一台 DG4000 的 Z 通道 offset 扫描额外偏置，"
        "并扫描 X/Y 直流补偿场，记录 HF2 Demod0 X/Y/R。"
    ),
    data_type="Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance",
    required_devices=("DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py",
    wiring_notes=(
        "GS200 必须与该 Z 线圈物理断开；DG4000 Z_magnetic_field 同时输出最优控制波形和额外 DC offset。",
        "Time_sequence_2 接同一 DG4000 的 Ext Trig，并使用下降沿启动 Z 控制 Burst。",
        "X_magnetic_field 与 Y_magnetic_field 继续使用 DG4000 DC 输出。",
        "HF2 Demod0 采集同步 X/Y/R；软件不能确认实体触发 BNC 接线。",
    ),
    safety_notes=(
        "运行前必须将 CONFIRM_GS200_DISCONNECTED 设为 true。",
        "每个偏置点同时校验任意波实际最小/最大值和固定输出包络的安全限值。",
        "Z 偏置只调用 DG4000 set_offset，不覆盖任意波形。",
        "正常、取消和异常结束均恢复运行前 Z、X、Y 通道完整状态。",
        "结果主判据是 Demod X/Y 复数稳健拟合；Demod0 R 网格最小点只作诊断。",
        "当前拟合是稳态 Bloch 型二维响应面，不等价于周期最优控制的严格 Bloch/Floquet 解。",
    ),
    schema_version=MxZOptimalControlDG4000BiasXYZBalanceParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
