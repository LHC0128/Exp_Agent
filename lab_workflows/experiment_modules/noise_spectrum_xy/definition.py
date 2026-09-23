"""XY 正弦控制噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import NOISE_SPECTRUM_XY
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYParams


ADAPTER = TypedWorkflowAdapter(
    "noise-spectrum-xy",
    "Noise_Spectrum_XY_Ctrl",
    NoiseSpectrumXYParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy.analysis",
)

DEFINITION = ExperimentDefinition(
    id="noise-spectrum-xy",
    title="XY 控制噪声谱",
    category="measurement",
    family="noise",
    variant="sine",
    description="扫描 X/Y 正弦峰值测量噪声谱；每点关闭两路输出后调幅，按已校准的 Burst 相位重新开启并等待共用外触发。设置每点采集时长和频率格间距，自动计算 Welch 段长。离线跟踪移动峰并验收标定，屏蔽固定窄线，以非负局部拟合和留出验证提取有效频段，不插值外推失败点。测量时温控 Output OFF，恢复等待可设置，结束保留 Pump 载波与门控。",
    data_type="Noise_Spectrum_XY_Ctrl",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=NOISE_SPECTRUM_XY,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Ctrl.py",
    analysis_program="experiments/Noise_Spectrum_XY_Ctrl_plot.py",
    wiring_notes=("Time_sequence_2 CH2 经三通连接 dg_comp CH1/CH2 Ext Trig；同时确认 X/Y 正弦输出、Pump 门控和 HF2 输入接线。",),
    safety_notes=(
        "目标频率必须能由当前 K/B 标定和 X/Y 电压安全限值表达。",
        "校相和采集时关闭温度开关物理输出，测量后恢复开启电平与 Output ON；最终收尾恢复为 5 V DC + ON。",
        "扫描收尾关闭 X/Y、Z 和共用外触发，保留 Pump 载波及 Time_sequence 时序门控输出。",
    ),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
