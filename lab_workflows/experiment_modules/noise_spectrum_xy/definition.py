"""XY DirectAW 噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
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
    variant="direct-aw",
    description="按目标噪声频率范围反算 DirectAW 包络电压并测量噪声谱。",
    data_type="Noise_Spectrum_XY_Ctrl",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Ctrl.py",
    analysis_program="experiments/Noise_Spectrum_XY_Ctrl_plot.py",
    wiring_notes=("确认 DirectAW X/Y、dg_am 双路外触发、Pump 门控和 HF2 输入接线。",),
    safety_notes=("目标频率必须能由当前 K/B 标定和固定 AW 输出安全表达。",),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
