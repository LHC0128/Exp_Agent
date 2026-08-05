"""Mx Y 向 RF 场灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFParams


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-sensitivity",
    "Mx_Y_RF_Sensitivity",
    MxYRFParams,
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-sensitivity",
    title="Mx Y 向 RF 场灵敏度",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-y-amplitude",
    description="主场与 Pump 光沿 Z、Probe 光沿 X，仅采集 Demod0 R，测量 Y 向 RF 场的绝对值色散响应、线宽与零场噪声灵敏度。",
    data_type="Mx_Y_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Sensitivity.py",
    analysis_program="experiments/Mx_Y_RF_Sensitivity_plot.py",
    wiring_notes=(
        "主磁场和 Pump 光沿 Z，Probe 光沿 X；rf_coil CH2 产生 Y 向 RF 场，X_magnetic_field CH1 保持关闭。",
        "HF2 Demod0 使用 Input 1，并以 Y RF 频率直接解调；DAQ 只订阅 R，不执行 X/Y 相位校准。",
        "时钟按 clock_sources.yaml 设置：Y RF/X 场 DG4000=EXT、Pump DG4000=INT、HF2=EXT，并在输出前回读验证。",
    ),
    safety_notes=(
        "正常、异常和取消均关闭归零 Z 辅助场、Y RF 与 X 场，温控恢复 5V ON；Pump 100MHz 载波与 Time_sequence 5V DC ON 是实验专用保留输出。",
        "正常结束只断开 TEC；主场、Pump/Probe 光功率和 HF2 配置保留。",
        "幅度点和模式1频率点仅在 std(R)≤0.01V 时接受，最多重采3次，连续失败即停止。",
    ),
    schema_version=MxYRFParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
