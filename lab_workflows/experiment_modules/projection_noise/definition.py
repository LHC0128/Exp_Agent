"""原子自旋投影噪声 SDS 实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ProjectionNoiseParams


ADAPTER = TypedWorkflowAdapter(
    "projection-noise",
    "Projection_noise",
    ProjectionNoiseParams,
    "lab_workflows.experiment_modules.projection_noise.workflow",
    "lab_workflows.experiment_modules.projection_noise.analysis",
)

DEFINITION = ExperimentDefinition(
    id="projection-noise",
    title="原子自旋投影噪声",
    category="measurement",
    family="fundamental-noise",
    variant="scope-pd",
    description="采集 GS200 输出打开时的 SDS PD 原始噪声谱，可选测量输出关闭对照组，并在主场标定预测中心附近自由拟合洛伦兹自旋噪声峰。",
    data_type="Projection_noise",
    required_devices=("GS200", "DG900", "DG4000", "SDS"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Projection_noise.py",
    analysis_program="experiments/Projection_noise_plot.py",
    wiring_notes=(
        "PD 原始输出接 SDS CH1，使用 DC、1 MΩ、1×探头和 AUTO 自由运行。",
        "Pump 光功率可在 0–1 V 安全范围内配置；0 V 时通道关闭，"
        "非零时通道打开。Probe 沿 X，主场由 GS200 沿 Z 控制。",
    ),
    safety_notes=(
        "可选对照阶段保持 GS200 输出关闭；主场打开阶段的电流由固定 Mx 主场标定反算并逐次通过全局安全校验。",
        "每帧采集期间温控为 0 V DC + output ON，帧间和结束后恢复 5 V DC + output ON。",
        "正常、异常和取消均停止 SDS，并恢复 GS200 完整运行前状态。",
        "XY、Z 辅助场以及 Pump 载波/门控在初始化和结束时均关闭归零，"
        "避免继承上一实验的残余输出。",
    ),
    schema_version=ProjectionNoiseParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
