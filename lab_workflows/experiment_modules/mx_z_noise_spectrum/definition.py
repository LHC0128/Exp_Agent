"""Mx 构型 Z 直流控制噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZNoiseSpectrumParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-noise-spectrum",
    "Mx_Z_Noise_Spectrum",
    MxZNoiseSpectrumParams,
    "lab_workflows.experiment_modules.mx_z_noise_spectrum.workflow",
    "lab_workflows.experiment_modules.mx_z_noise_spectrum.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-noise-spectrum",
    title="Mx Z 直流控制噪声谱",
    category="measurement",
    family="noise",
    variant="mx-z-dc",
    description="在 Mx 高主场构型下扫描 Z 直流偏置，固定 HF2 零偏参考频率并从 Demod0 R 的 PSD 分离 S_beta 与 N_S1。",
    data_type="Mx_Z_Noise_Spectrum",
    required_devices=("GS200", "DG900", "DG4000", "HF2", "TEC103"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Noise_Spectrum.py",
    analysis_program="experiments/Mx_Z_Noise_Spectrum_plot.py",
    wiring_notes=(
        "主场与 Pump 光沿 Z、Probe 光沿 X；Z_magnetic_field CH1 输出逐点恒定 DC，X/Y 场保持归零关闭。",
        "HF2 Demod0 使用 Input 1，振荡器固定在 90 kHz，只采集 R。",
    ),
    safety_notes=(
        "Z 电压必须严格由已保存的 Hz/V 标定式反算，并在每次输出前通过安全限值校验。",
        "正常、异常和取消均归零关闭 Z/X/Y 场，恢复温控 5 V ON，并保持 Pump 100 MHz 与 Time_sequence 5 V ON。",
    ),
    schema_version=MxZNoiseSpectrumParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
