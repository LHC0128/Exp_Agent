"""Mx Y RF 灵敏度长飘实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFDriftParams


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-sensitivity-drift",
    "Mx_Y_RF_Sensitivity_Drift",
    MxYRFDriftParams,
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.workflow",
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-sensitivity-drift",
    title="Mx Y RF 灵敏度长飘",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-y-drift",
    description=(
        "按绝对开始时间间隔重复标准 Mx Y RF 子运行，支持半开计划区间与立即首轮开关，超期轮依序补跑。"
        "每轮立即分析并更新灵敏度、幅度等效线宽和拟合质量趋势；R 点质量或拟合失败记无效轮继续，硬件、恢复和保存异常停止。"
        "采集准备、调度与逐轮编排按职责分离，完整与分步共用业务；收尾使用最后已执行的配置。"
        "锁相输入保持高阻、DC 耦合，按设备库选择 DAQ/Scope 并使用实际采样率。"
        "每点配置后温控置 0 V DC 且输出开启，稳定并采集至内存，恢复温控后保存；TEC 仅写 UI 目标温度。分步角色由清单声明，状态与产物以执行记录为准。"
    ),
    data_type="Mx_Y_RF_Sensitivity_Drift",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Sensitivity_Drift.py",
    analysis_program="experiments/Mx_Y_RF_Sensitivity_Drift_plot.py",
    wiring_notes=(
        "接线和单轮测量与 mx-y-rf-sensitivity 相同：主场/Pump 沿 Z，Probe 沿 X，Y RF 使用 rf_coil CH2。",
        "每轮只采集 锁相放大器 Demod0 R；长飘父目录引用标准 Mx_Y_RF_Sensitivity 子运行目录。",
    ),
    safety_notes=(
        "每轮结束关闭并归零 Y RF、恢复温控开关；取消、异常和正常结束均执行标准安全停机。",
        "硬件通信或采集异常停止任务；单轮拟合和质量门限失败记录后继续。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
