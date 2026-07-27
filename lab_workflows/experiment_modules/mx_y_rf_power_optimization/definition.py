"""Mx Y RF 光功率灵敏度优化实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFPowerOptimizationParams


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
        "在 Mx Y 向 RF 构型下二维扫描 Pump/Probe 光功率，"
        "逐点测量完整灵敏度并寻找最优工作点。"
    ),
    data_type="Mx_Y_RF_Power_Optimization",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Power_Optimization.py",
    analysis_program="experiments/Mx_Y_RF_Power_Optimization_plot.py",
    wiring_notes=(
        "接线与 Mx Y 向 RF 场灵敏度实验相同：主场/Pump 沿 Z、Probe 沿 X、RF 场沿 Y。",
        "DG900 CH1/CH2 分别扫描 Pump/Probe DC 光功率，HF2 Demod0 仅采集 R。",
    ),
    safety_notes=(
        "所有功率写入前按 0–1 V 安全限值校验；质量坏点标记无效后继续，设备错误立即停止。",
        "正常、取消和异常均关闭归零 X/Y 场，并恢复 Pump=0.5 V、Probe=0.3 V 基准输出。",
    ),
    schema_version=MxYRFPowerOptimizationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
