"""Mx Y RF Probe 光功率与 PZT 失谐优化实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFProbeDetuningOptimizationParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-probe-detuning-optimization",
    "Mx_Y_RF_Probe_Detuning_Optimization",
    MxYRFProbeDetuningOptimizationParams,
    (
        "lab_workflows.experiment_modules."
        "mx_y_rf_probe_detuning_optimization.workflow"
    ),
    (
        "lab_workflows.experiment_modules."
        "mx_y_rf_probe_detuning_optimization.analysis"
    ),
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-probe-detuning-optimization",
    title="Mx Y RF Probe 光功率与失谐灵敏度优化",
    category="optimization",
    family="rf-sensitivity",
    variant="mx-y-probe-power-pzt-grid",
    description=(
        "固定 Pump，按 PZT 外层、Probe 功率内层蛇形扫描；支持固定 Probe 单点。"
        "运行页提供可勾选的采集／独立分析流程，节点编辑参数，顶部保存、预检和完整运行；"
        "应用草稿后须显式重跑。历史目录的分析覆盖独立，不改采集快照。"
        "逐点恢复温控后保存，不连接 TEC；结束恢复初始 PZT／扫描与已执行的光功率基准，"
        "关闭归零辅助磁场，保留主场与 Pump。复用原双斜率灵敏度算法，不自动施加最优点。分步角色由清单声明，状态与产物以执行记录为准。"
    ),
    data_type="Mx_Y_RF_Probe_Detuning_Optimization",
    required_devices=(
        "GS200",
        "DG900",
        "DG4000",
        "锁相放大器",
        "DLC_PRO",
    ),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program=(
        "experiments/Mx_Y_RF_Probe_Detuning_Optimization.py"
    ),
    analysis_program=(
        "experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py"
    ),
    wiring_notes=(
        "接线与 Mx Y 向 RF 场灵敏度实验相同，DLC pro Laser 1 "
        "Scan Offset 作为 Probe 失谐代理轴。",
        "DG900 CH1 固定 Pump 光功率；CH2 可扫描 Probe 光功率，"
        "也可用单点 Probe 轴保持固定；锁相放大器 Demod0 仅采集 R。",
        "该实验不连接 TEC103；Temp_Switch 仍由独立 DG900 通道控制。",
    ),
    safety_notes=(
        "PZT 和 Probe 功率写入前按全局安全限值校验；实验不远程切换 "
        "Emission、Laser Enabled、电流或激光温度。",
        "不设置或读取 TEC 温度；采集时仍关闭温控开关，点间及结束时"
        "恢复为 5 V DC + ON。",
        "正常、取消、异常和通信结束均恢复进入实验前的 PZT/扫描状态，关闭归零 "
        "Z 辅助场和 X/Y 场；恢复已执行配置的 Pump/Probe 基准，保持主场与 Pump。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
