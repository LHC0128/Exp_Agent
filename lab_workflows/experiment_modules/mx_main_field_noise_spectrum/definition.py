"""Mx 主磁场控制噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxMainFieldNoiseSpectrumParams


ADAPTER = TypedWorkflowAdapter(
    "mx-main-field-noise-spectrum",
    "Mx_Main_Field_Noise_Spectrum",
    MxMainFieldNoiseSpectrumParams,
    "lab_workflows.experiment_modules.mx_main_field_noise_spectrum.workflow",
    "lab_workflows.experiment_modules.mx_main_field_noise_spectrum.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-main-field-noise-spectrum",
    title="Mx 主磁场控制噪声谱",
    category="measurement",
    family="noise",
    variant="mx-main-field",
    description="固定 HF2 为 90 kHz，按实测标定降低 GS200 主场电流，并从 Demod0 R 的 PSD 分离 S_beta 与 N_S1。",
    data_type="Mx_Main_Field_Noise_Spectrum",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Main_Field_Noise_Spectrum.py",
    analysis_program="experiments/Mx_Main_Field_Noise_Spectrum_plot.py",
    wiring_notes=(
        "主场与 Pump 光沿 Z、Probe 光沿 X；GS200 单向降电流，Z/X/Y 场保持归零关闭。",
        "HF2 Demod0 使用 Input 1，振荡器固定在 90 kHz，只采集 R。",
    ),
    safety_notes=(
        "每个 GS200 电流点必须同时通过 -10–10 mA 全局安全限值和 3–10 mA 实测标定范围校验。",
        "正常、异常和取消均恢复 GS200 完整运行前状态，归零关闭 Z/X/Y 场并恢复温控 5 V ON。",
    ),
    schema_version=MxMainFieldNoiseSpectrumParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
