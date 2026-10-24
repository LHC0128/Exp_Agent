"""Mx 主磁场示波器噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxMainFieldScopeNoiseSpectrumParams
from .preparation import preflight as validate_preflight
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-main-field-scope-noise-spectrum",
    "Mx_Main_Field_Scope_Noise_Spectrum",
    MxMainFieldScopeNoiseSpectrumParams,
    "lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.workflow",
    "lab_workflows.experiment_modules.mx_main_field_scope_noise_spectrum.analysis",
)

def preflight(values):
    try:
        validate_preflight(ADAPTER._resolved(values), ADAPTER.root)
        return []
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [str(exc)]


DEFINITION = ExperimentDefinition(
    id="mx-main-field-scope-noise-spectrum",
    title="Mx 主磁场示波器噪声谱",
    category="measurement",
    family="noise",
    variant="mx-main-field-scope",
    description="按主场标定外推扫描 Larmor 控制频率，保持 X/Y DC 剩磁补偿，以 SDS 自适应量程采集 PD 原始波形。运行/历史页提供分步流程图、节点参数与独立分析：保留局部 Lorentzian 诊断及两阶段二维全局噪声分解。配置后关温控、稳定、内存采集、恢复后保存；TEC 仅写目标温度。结束恢复 GS200 和 SDS DC 耦合，归零关闭 Z/XY，保留光路与 Pump。",
    data_type="Mx_Main_Field_Scope_Noise_Spectrum",
    required_devices=("GS200", "DG900", "DG4000", "SDS"),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Main_Field_Scope_Noise_Spectrum.py",
    analysis_program="experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py",
    wiring_notes=(
        "主场与 Pump 光沿 Z、Probe 光沿 X；PD 原始信号接 SDS CH1。",
        "SDS 使用 AUTO 自由运行，不使用 CH4 外部触发；Z 场保持归零关闭，X/Y 场按固定 DC 补偿参数设置。",
    ),
    safety_notes=(
        "主场标定允许外推，但每个 GS200 电流点仍须通过当前 safety_limits.yaml 的主场安全校验。",
        "X/Y DC 补偿为 0 V 时关闭对应输出，非零时在扫描期间保持开启；所有写入均按当前全局安全限值校验。",
        "正常、异常和取消均恢复 GS200 完整运行前状态，停止 SDS、将 PD 通道恢复为 DC 耦合、归零关闭 Z/X/Y 场并恢复温控 5 V ON。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
