"""XY DirectAW Demod3 R 噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import NoiseSpectrumXYDemod3RParams


ADAPTER = TypedWorkflowAdapter(
    "noise-spectrum-xy-demod3-r",
    "Noise_Spectrum_XY_Demod3_R",
    NoiseSpectrumXYDemod3RParams,
    "lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.workflow",
    "lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.analysis",
)

DEFINITION = ExperimentDefinition(
    id="noise-spectrum-xy-demod3-r",
    title="XY 控制噪声谱（Demod3 R）",
    category="measurement",
    family="noise",
    variant="direct-aw-demod3-r",
    description="二维扫描 DirectAW 控制频率和 Demod3 解调频率，连续采集并平均 R。",
    data_type="Noise_Spectrum_XY_Demod3_R",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Noise_Spectrum_XY_Demod3_R.py",
    analysis_program="experiments/Noise_Spectrum_XY_Demod3_R_plot.py",
    wiring_notes=(
        "确认 Demod0 Y → AuxOut2 → Signal Input 2 DC → Demod3 物理回环。",
        "确认 DirectAW X/Y、双路外触发、Pump 门控和 HF2 输入接线。",
    ),
    safety_notes=(
        "控制频率必须能由当前 K/B 标定和固定 AW 输出安全表达。",
        "每批 Demod3 频点结束后恢复温控，取消和异常时执行统一安全收尾。",
    ),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
