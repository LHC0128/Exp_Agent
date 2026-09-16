"""Mx Z 最优控制 RF 灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlRFParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-rf-sensitivity",
    "Mx_Z_Optimal_Control_RF_Sensitivity",
    MxZOptimalControlRFParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_rf_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-rf-sensitivity",
    title="Mx Z 最优控制 RF 灵敏度",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-z-optimal-control",
    description=(
        "按配置启停 GS200 主场，并叠加 Z 向周期最优控制；"
        "以 X DC 和 Y RF 偏置平衡 XY 剩磁场；校相幅度非零时测量 RF "
        "灵敏度，校相幅度为零时保持 Y DC 补偿并扫描 Z 控制相位。"
    ),
    data_type="Mx_Z_Optimal_Control_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program=(
        "experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py"
    ),
    analysis_program=(
        "experiments/Mx_Z_Optimal_Control_RF_Sensitivity_plot.py"
    ),
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出最优控制；X_magnetic_field DG4000 CH1 输出固定 X DC 补偿。",
        "rf_coil DG4000 CH2 输出带 Y DC 补偿偏置的 Y RF Burst；RFY=0 时切换为纯 DC 补偿输出。",
        "Time_sequence_2 CH2 的 100 Hz、5 Vpp 方波需经分配后同时接入 Z 控制与 Y RF 两台 DG4000 的 Ext Trig，两路 Burst 均使用下降沿。",
        "软件只回读 Burst/触发配置，不能确认实体 BNC 分配线是否接通；运行前必须人工核对。",
        "HF2 Demod0 以固定 Y RF 频率直接解调；Y RF 校相同步采集 R/X/Y，正式响应与噪声仍采集 R。",
        "PHASE_CAL_RF_AMPLITUDE_VPP=0 时禁用 RFY 交流分量但保持 Y DC 补偿，共同触发只需驱动 Z 控制 DG4000。",
    ),
    safety_notes=(
        "GS200 主场配置为 0 mA 时归零关闭，非零时设定电流后开启；所有设置先执行安全校验。",
        "正常、取消和异常结束后完整恢复 GS200 运行前源模式、电流、量程、限流和输出状态。",
        "正常、取消和异常结束时保持 Z_magnetic_field 的最优控制波形及 Output 状态不变，不归零也不关闭。",
        "剩磁响应模式在所有退出路径把 Z 控制 Burst 相位恢复为 CONTROL_BURST_PHASE_DEG。",
        "所有结束路径关闭归零共同触发、Y RF 与 X 场，并恢复温控为 5 V DC + Output ON。",
        "Pump/Probe、Pump 载波/门控和 HF2 设置保留；正常结束只断开 TEC。",
        "闭环冻结波形的完整 Z 电压范围、X DC 和 Y RF 的 offset±Vpp/2 包络均在输出前执行安全校验。",
    ),
    schema_version=MxZOptimalControlRFParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
