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
        "Y RF 幅度与偏置校验拒绝 NaN/Infinity。"
        '按配置启停 GS200 主场，叠加冻结的 Z 周期最优控制，以 X DC 和 Y RF 偏置补偿 XY 剩磁场。'
        '非零校相幅度执行 180° 成对 R/X/Y 校相、带符号幅度响应及 R 噪声灵敏度；零幅度仅扫描 Z 控制相位并保留弱响应诊断。'
        '运行/历史双 Tab 中使用可勾选流程、节点参数与统一保存/预检；共享参数同步编辑，草稿应用后显式重跑。'
        '独立分析绑定具体目录且无额外覆盖参数，保留原科学图与历史下载。'
        '锁相按设备库选择并检查兼容性，输入保持高阻和 DC 耦合，DAQ/Scope 按最终实际率计算。'
        '配置后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存、恢复温控后保存；TEC 只下发 UI 目标。'
        '安全结束关闭横向场、恢复 GS200 原态，保持 Z 最优控制与共同触发。分步角色由清单声明，状态与产物以执行记录为准。'
    ),
    data_type="Mx_Z_Optimal_Control_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
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
        "锁相放大器 Demod0 以固定 Y RF 频率直接解调；Y RF 校相同步采集 R/X/Y，正式响应与噪声仍采集 R。",
        "PHASE_CAL_RF_AMPLITUDE_VPP=0 时禁用 RFY 交流分量但保持 Y DC 补偿，共同触发只需驱动 Z 控制 DG4000。",
    ),
    safety_notes=(
        "GS200 主场配置为 0 mA 时归零关闭，非零时设定电流后开启；所有设置先执行安全校验。",
        "正常、取消和异常结束后完整恢复 GS200 运行前源模式、电流、量程、限流和输出状态。",
        "正常、取消和异常结束时保持 Z_magnetic_field 的最优控制波形及 Output 状态不变，不归零也不关闭。",
        "剩磁响应模式在所有退出路径把 Z 控制 Burst 相位恢复为 CONTROL_BURST_PHASE_DEG。",
        "所有结束路径保持共同触发当前状态，关闭归零 Y RF 与 X 场，并恢复温控为 5 V DC + Output ON。",
        "Pump/Probe、Pump 载波/门控和 锁相放大器 设置保留；正常结束只断开 TEC。",
        "闭环冻结波形的完整 Z 电压范围、X DC 和 Y RF 的 offset±Vpp/2 包络均在输出前执行安全校验。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
