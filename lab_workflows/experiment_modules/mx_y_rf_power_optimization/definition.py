"""Mx Y RF 光功率灵敏度优化实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFPowerOptimizationParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-power-optimization",
    "Mx_Y_RF_Power_Optimization",
    MxYRFPowerOptimizationParams,
    "lab_workflows.experiment_modules.mx_y_rf_power_optimization.workflow",
    "lab_workflows.experiment_modules.mx_y_rf_power_optimization.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-power-optimization",
    title="Mx Y RF 光功率灵敏度优化",
    category="optimization",
    family="rf-sensitivity",
    variant="mx-y-pump-probe-grid",
    description=(
        ('在 Mx Y 向 RF 构型下按蛇形网格扫描 Pump/Probe 光功率，保留拟合导数与零点实测斜率双排名。运行/历史双 Tab 提供可勾选依赖的采集/分析两列流程图，参数在节点编辑，保存、预检、完整运行与任务状态位于流程顶部；应用草稿后显式重跑，具体运行目录的分析覆盖与下一次实验设置隔离。 锁相设备在设备库中全系统统一选择；启动前检查输入通道和工作频率兼容性。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。分步角色由清单声明，状态与产物以执行记录为准。')
    ),
    data_type="Mx_Y_RF_Power_Optimization",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    execution_mode="typed_workflow",
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    acquisition_program="experiments/Mx_Y_RF_Power_Optimization.py",
    analysis_program="experiments/Mx_Y_RF_Power_Optimization_plot.py",
    wiring_notes=(
        "接线与 Mx Y 向 RF 场灵敏度实验相同：主场/Pump 沿 Z、Probe 沿 X、RF 场沿 Y。",
        "DG900 CH1/CH2 分别扫描 Pump/Probe DC 光功率，锁相放大器 Demod0 仅采集 R。",
    ),
    safety_notes=(
        "所有功率写入前按 0–1 V 安全限值校验；质量坏点标记无效后继续，设备错误立即停止。",
        "正常、取消、异常与通信结束均归零关闭 Z 辅助场和 X/Y 场，恢复温控及已执行参数中的 Pump/Probe 基准输出；未重跑的参数草稿不改变收尾配置。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
