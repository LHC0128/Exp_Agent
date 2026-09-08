"""Mx Z 最优控制 XY 平衡场 RF 相位响应定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlXYRFPhaseResponseParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-xy-rf-phase-response",
    "Mx_Z_Optimal_Control_XY_RF_Phase_Response",
    MxZOptimalControlXYRFPhaseResponseParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_rf_phase_response.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-xy-rf-phase-response",
    title="Mx Z 最优控制 XY 平衡场 RF 相位响应",
    category="measurement",
    family="rf-phase-response",
    variant="mx-z-optimal-control-xy-balance",
    description=(
        "保持 Z 周期最优控制，在 X/Y DC 平衡场网格上逐点扫描 Y RF Burst 相位，"
        "采集 Demod0 R 并拟合绝对值正弦相位响应。"
    ),
    data_type="Mx_Z_Optimal_Control_XY_RF_Phase_Response",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program=(
        "experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response.py"
    ),
    analysis_program=(
        "experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py"
    ),
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出最优控制；X_magnetic_field 输出 X DC。",
        "Y_magnetic_field 与 rf_coil 共用 DG4000 通道，Y 扫描值作为 RF Burst 的 DC offset。",
        "Time_sequence_2 的共同触发方波需接入 Z 控制和 Y RF 的 Ext Trig，两路均使用下降沿。",
        "HF2 Demod0 按固定 Y RF 频率采集 R；实体触发线缆需要操作者运行前核对。",
    ),
    safety_notes=(
        "X/Y 网格端点、Y offset 加 RF 振幅后的完整包络及 Z 波形均在输出前执行安全校验。",
        "Z 最优控制在扫描期间持续运行；正常、取消和异常结束均保留其波形和 Output 状态。",
        "共同触发、X 场和 Y RF 在结束路径归零关闭，GS200 恢复运行前状态。",
        "温控恢复为 5 V DC + Output ON，Pump/Probe、Pump 载波/门控和 HF2 设置保持。",
    ),
    schema_version=MxZOptimalControlXYRFPhaseResponseParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
