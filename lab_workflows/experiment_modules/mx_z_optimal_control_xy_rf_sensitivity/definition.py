"""Mx Z 最优控制 XY 补偿偏置 RF 灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import OPTIMAL_CONTROL
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlXYRFSensitivityParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-xy-rf-sensitivity",
    "Mx_Z_Optimal_Control_XY_RF_Sensitivity",
    MxZOptimalControlXYRFSensitivityParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.analysis",
)


DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-xy-rf-sensitivity",
    title="Mx Z 最优控制 XY 补偿偏置 RF 灵敏度",
    category="optimization",
    family="rf-sensitivity",
    variant="mx-z-optimal-control-xy-bias-grid",
    description=(
        "在 Z 周期最优控制下，中心一次 RF 正交校相后蛇形扫描 X/Y 补偿偏置，逐点测量灵敏度并报告最佳补偿点。"
        "运行/历史双 Tab 提供节点参数与分步会话；保存、预检和完整运行位于流程顶部，共享参数同步，草稿应用后显式重跑。"
        "分析绑定具体目录，无额外覆盖参数；保留原热图、逐点诊断及历史下载。"
        "锁相设备统一选择并预检能力，使用高阻、DC 耦合和最终实际采样率。"
        "配置后关闭温控并按 SYSTEM_SETTLE_S 稳定，恢复温控后保存；TEC 仅下发 UI 目标。"
        "结束恢复 GS200 原态、关闭归零 X/Y，保持 Z 控制和共同触发；最佳点只写结果。"
    ),
    data_type="Mx_Z_Optimal_Control_XY_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=OPTIMAL_CONTROL,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity_plot.py",
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出周期最优控制；Time_sequence_2 CH2 为共同触发。",
        "X_magnetic_field 输出 X DC 补偿；Y_magnetic_field 与 rf_coil 共用通道，Y 网格值作为 RF DC offset。",
        "Time_sequence_2 需分配到 Z 控制和 Y RF DG4000 的 Ext Trig，两路 Burst 使用下降沿。",
        "RF 相位只在 XY 扫描中心校准一次，实体触发线缆需运行前人工核对。",
        "锁相放大器 Demod0 按固定 Y RF 频率采集；幅度扫描和噪声均按每个 XY 点分别保存。",
    ),
    safety_notes=(
        "所有 X/Y offset、Y RF 完整包络、Z 控制波形和触发电平在输出前执行安全校验。",
        "R 点质量失败按配置重试；耗尽后标记该 XY 点无效并继续，其余设备错误立即终止。",
        "正常、取消和异常结束均保持共同触发当前状态，关闭归零 X 场和 Y RF，保持 Z 最优控制输出。",
        "GS200 恢复运行前完整状态，温控恢复为 5 V DC + Output ON，Pump/Probe、Pump 调制和 锁相放大器 设置保留。",
        "分析得到的最佳 XY 补偿点只写入结果，不自动切换硬件。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
