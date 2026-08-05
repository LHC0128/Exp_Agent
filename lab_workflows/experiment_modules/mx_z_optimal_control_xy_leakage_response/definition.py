"""Mx Z 最优控制 XY 泄露响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlXYLeakageParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-xy-leakage-response",
    "Mx_Z_Optimal_Control_XY_Leakage_Response",
    MxZOptimalControlXYLeakageParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_leakage_response.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-xy-leakage-response",
    title="Mx Z 最优控制 XY 泄露响应",
    category="measurement",
    family="field-response",
    variant="mx-z-optimal-control-xy-direct-aw",
    description=(
        "保持 Z 最优控制，二维扫描同形 X/Y 触发任意波的带符号幅度，"
        "记录 HF2 Demod0 R 并报告实测网格最小点。"
    ),
    data_type="Mx_Z_Optimal_Control_XY_Leakage_Response",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program=(
        "experiments/Mx_Z_Optimal_Control_XY_Leakage_Response.py"
    ),
    analysis_program=(
        "experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py"
    ),
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出最优控制；X_magnetic_field 与 Y_magnetic_field DG4000 CH1/CH2 输出同形任意波。",
        "Time_sequence_2 CH2 需经分配后同时接入 Z 控制和 X/Y 控制两台 DG4000 的 Ext Trig。",
        "软件只验证 Burst/触发配置，不能确认实体 BNC 分配线是否接通。",
        "HF2 Demod0 按独立 DEMOD_FREQUENCY_HZ 采集 R。",
    ),
    safety_notes=(
        "所有 Z/X/Y/触发输出在写入前执行安全限值校验。",
        "正常结束保留 Z 和实测最小 R 网格点的 X/Y 任意波，不额外复测组合状态。",
        "取消或异常时保留当时 Z/X/Y 状态；这些输出可能在失败后继续开启。",
        "所有结束路径关闭归零 Time_sequence_2，恢复温控为 5 V DC + Output ON，并恢复 GS200 运行前完整状态。",
        "Pump/Probe、Pump 载波/门控和 HF2 设置保持，正常结束只断开 TEC。",
    ),
    schema_version=MxZOptimalControlXYLeakageParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
