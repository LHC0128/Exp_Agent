"""XY 控制已知噪声注入实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import NOISE_SPECTRUM_XY
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYKnownNoiseParams

ADAPTER = TypedWorkflowAdapter(
    "noise-spectrum-xy-known-noise",
    "Noise_Spectrum_XY_Ctrl_Known_Noise",
    NoiseSpectrumXYKnownNoiseParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.analysis",
)

DEFINITION = ExperimentDefinition(
    id="noise-spectrum-xy-known-noise",
    title="XY 控制测量已知可控噪声谱",
    category="verification",
    family="noise",
    variant="known-injection",
    description=(
        "在 Z 小线圈注入分段平顶谱的已知周期伪噪声（16384 点任意波连续播放），"
        "相邻控制点交替注入开/关顺序，两态稳定等待并逐段保存；以单一参数模型配置采集，脊线按扫描频带重标定后用完整有效控制轴做洛伦兹分离，保留有符号参数差；"
        "背景自由拟合并通过远端可辨识性和留出验证，分别报告注入带内/带外的背景变化；"
        "内置 z 偏置色散扫描对整条线形拟合零交叉点斜率给出增益换算，测得谱换算到 Hz²/Hz 后与"
        "K_Z×线圈频响推算的注入真值逐 bin 比较，带内中位比值作为绝对定量判据。"
    ),
    data_type="Noise_Spectrum_XY_Ctrl_Known_Noise",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=NOISE_SPECTRUM_XY,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py",
    analysis_program="experiments/Noise_Spectrum_XY_Ctrl_Known_Noise_plot.py",
    wiring_notes=(
        "运行前人工确认 Z 链路当前接线与 K_Z 标定/频响测量时一致（重点：闭环校正时期串入的 10 kHz 高通是否仍在）。",
        "Time_sequence_2 CH2 经三通连接 dg_comp CH1/CH2 Ext Trig；Z 噪声由 Z_magnetic_field CH1 连续播放，不参与 Burst。",
    ),
    safety_notes=(
        "噪声波形输出极值、色散偏置步和 X/Y 包络全部先过 validate_safety_limit 再下发。",
        "每点在同一温控窗口内采集两态，相邻点交替先后顺序，每态等待稳定；色散扫描每若干点回插温控恢复；"
        "扫描收尾关闭 X/Y、Z 噪声和外触发，保留 Pump 载波与时序门控。",
    ),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
