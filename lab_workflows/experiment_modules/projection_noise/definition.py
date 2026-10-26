"""原子自旋投影噪声 SDS 实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import ProjectionNoiseParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "projection-noise",
    "Projection_noise",
    ProjectionNoiseParams,
    "lab_workflows.experiment_modules.projection_noise.workflow",
    "lab_workflows.experiment_modules.projection_noise.analysis",
)

def preflight(values):
    from .preparation import preflight as check
    try:
        check(ADAPTER._resolved(values), ADAPTER.root)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    return []


DEFINITION = ExperimentDefinition(
    id="projection-noise",
    title="原子自旋投影噪声",
    category="measurement",
    family="fundamental-noise",
    variant="scope-pd",
    description=("以采集／分析双列交互流程图分步执行 SDS PD 自旋噪声测量，可选 GS200 OFF 背景，保留原 Welch 与自由洛伦兹拟合。"
        "参数在节点内编辑，草稿应用后显式重跑；历史目录分析覆盖值独立。每次恢复温控后保存，TEC 只写目标温度；"
        "初始化及所有收尾均不修改 Pump 载波／门控，GS200 恢复运行前状态。分步角色由清单声明，状态与产物以执行记录为准。"),
    data_type="Projection_noise",
    required_devices=("GS200", "DG900", "DG4000", "SDS"),
    execution_mode="typed_workflow",
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
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
        "XY、Z 辅助场在初始化和结束时归零关闭；Pump 载波与门控全程保持当前状态，"
        "不连接其专用设备、不写其通道或参考时钟；运行前由用户确认状态。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
