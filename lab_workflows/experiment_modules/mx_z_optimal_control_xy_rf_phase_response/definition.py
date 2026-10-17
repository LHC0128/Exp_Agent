"""Mx Z 最优控制 XY 平衡场 RF 相位响应定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...experiments.requirements import OPTIMAL_CONTROL
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
        "保持 Z 周期最优控制，在蛇形 X/Y DC 网格按 180° 配对顺序扫描 Y RF Burst 相位，"
        "采集 R、执行质量重试与单轮异常重采，以非线性色散模型报告相位响应；保留历史正弦诊断，不选最佳 XY。"
        "运行页在 XY 分步节点内编辑参数，顶部保存布局、预检和完整运行；草稿应用后显式重跑。"
        "配置完成再关闭温控、等待 SYSTEM_SETTLE_S、采集至内存，恢复温控后保存；TEC 只写 UI 目标。"
        "历史独立分析可覆盖拟合门槛，不改变采集参数。结束保持 Z 和共同触发当前状态，X/Y 归零关闭。"
    ),
    data_type="Mx_Z_Optimal_Control_XY_RF_Phase_Response",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=OPTIMAL_CONTROL,
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
        "锁相放大器 Demod0 按固定 Y RF 频率采集 R；实体触发线缆需要操作者运行前核对。",
    ),
    safety_notes=(
        "X/Y 网格端点、Y offset 加 RF 振幅后的完整包络及 Z 波形均在输出前执行安全校验。",
        "Z 最优控制在扫描期间持续运行；正常、取消和异常结束均保留其波形和 Output 状态。",
        "共同触发保持当前状态，X 场和 Y RF 在结束路径归零关闭，GS200 恢复运行前状态。",
        "温控恢复为 5 V DC + Output ON，Pump/Probe、Pump 载波/门控和 锁相放大器 设置保持。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
