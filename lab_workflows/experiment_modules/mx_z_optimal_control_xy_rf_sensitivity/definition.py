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
        "在 Z 周期最优控制下扫描 X/Y 补偿偏置，先在网格中心完成一次 RF 校相，"
        "再逐点测量 RF 灵敏度并报告最佳补偿点。"
    ),
    data_type="Mx_Z_Optimal_Control_XY_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=OPTIMAL_CONTROL,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity_plot.py",
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出周期最优控制；Time_sequence_2 CH2 为共同触发。",
        "X_magnetic_field 输出 X DC 补偿；Y_magnetic_field 与 rf_coil 共用通道，Y 网格值作为 RF DC offset。",
        "Time_sequence_2 需分配到 Z 控制和 Y RF DG4000 的 Ext Trig，两路 Burst 使用下降沿。",
        "RF 相位只在 XY 扫描中心校准一次，实体触发线缆需运行前人工核对。",
        "HF2 Demod0 按固定 Y RF 频率采集；幅度扫描和噪声均按每个 XY 点分别保存。",
    ),
    safety_notes=(
        "所有 X/Y offset、Y RF 完整包络、Z 控制波形和触发电平在输出前执行安全校验。",
        "R 点质量失败按配置重试；耗尽后标记该 XY 点无效并继续，其余设备错误立即终止。",
        "正常、取消和异常结束均关闭归零共同触发、X 场和 Y RF，保持 Z 最优控制输出。",
        "GS200 恢复运行前完整状态，温控恢复为 5 V DC + Output ON，Pump/Probe、Pump 调制和 HF2 设置保留。",
        "分析得到的最佳 XY 补偿点只写入结果，不自动切换硬件。",
    ),
    schema_version=MxZOptimalControlXYRFSensitivityParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
