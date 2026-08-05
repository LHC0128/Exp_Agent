"""Mx 主磁场示波器噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxMainFieldScopeNoiseSpectrumParams


ADAPTER = TypedWorkflowAdapter(
    "mx-main-field-scope-noise-spectrum",
    "Mx_Main_Field_Scope_Noise_Spectrum",
    MxMainFieldScopeNoiseSpectrumParams,
    "lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow",
    "lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-main-field-scope-noise-spectrum",
    title="Mx 主磁场示波器噪声谱",
    category="measurement",
    family="noise",
    variant="mx-main-field-scope",
    description="按主场标定扫描 0–50 kHz Larmor 频率，保持可配置的 X/Y DC 剩磁补偿，使用 SDS 采集 PD 原始波形，并用 2–45 kHz 核心频段确定共同 Lorentzian 响应和秩一背景，再向 500 Hz 投影分离调制噪声与 N_S1。",
    data_type="Mx_Main_Field_Scope_Noise_Spectrum",
    required_devices=("GS200", "DG900", "DG4000", "SDS"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Main_Field_Scope_Noise_Spectrum.py",
    analysis_program="experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py",
    wiring_notes=(
        "主场与 Pump 光沿 Z、Probe 光沿 X；PD 原始信号接 SDS CH1。",
        "SDS 使用 AUTO 自由运行，不使用 CH4 外部触发；Z 场保持归零关闭，X/Y 场按固定 DC 补偿参数设置。",
    ),
    safety_notes=(
        "主场标定允许外推，但每个 GS200 电流点仍必须通过 -10–10 mA 全局安全校验。",
        "X/Y DC 补偿值为 0 V 时关闭对应输出，非零时在扫描期间保持开启；所有写入均受 -10–10 V 全局安全限值保护。",
        "正常、异常和取消均恢复 GS200 完整运行前状态，停止 SDS、将 PD 通道恢复为 DC 耦合、归零关闭 Z/X/Y 场并恢复温控 5 V ON。",
    ),
    schema_version=MxMainFieldScopeNoiseSpectrumParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
