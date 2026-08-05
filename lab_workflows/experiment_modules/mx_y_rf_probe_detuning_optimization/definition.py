"""Mx Y RF Probe 光功率与 PZT 失谐优化实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFProbeDetuningOptimizationParams


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
        "在 Mx Y 向 RF 构型下扫描 Probe 光功率和 DLC pro PZT "
        "Scan Offset；Probe 轴也可设为单点，以固定光功率只扫描 PZT。"
    ),
    data_type="Mx_Y_RF_Probe_Detuning_Optimization",
    required_devices=(
        "GS200",
        "DG900",
        "DG4000",
        "HF2",
        "DLC_PRO",
    ),
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
        "也可用单点 Probe 轴保持固定；HF2 Demod0 仅采集 R。",
        "该实验不连接 TEC103；Temp_Switch 仍由独立 DG900 通道控制。",
    ),
    safety_notes=(
        "PZT 和 Probe 功率写入前按全局安全限值校验；实验不远程切换 "
        "Emission、Laser Enabled、电流或激光温度。",
        "不设置或读取 TEC 温度；采集时仍关闭温控开关，点间及结束时"
        "恢复为 5 V DC + ON。",
        "正常、取消和异常均恢复进入实验前的 PZT/扫描状态，关闭归零 "
        "Z 辅助场和 X/Y 场，并恢复 Pump=0.5 V、Probe=0.3 V 基准输出。",
    ),
    schema_version=MxYRFProbeDetuningOptimizationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
