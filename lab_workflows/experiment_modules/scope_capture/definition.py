"""示波器采集的统一实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...common import load_mapping
from .models import ScopeCaptureParams

ADAPTER = TypedWorkflowAdapter(
    "scope-capture", "Scope_Capture", ScopeCaptureParams,
    "lab_workflows.experiment_modules.scope_capture.workflow",
    "lab_workflows.experiment_modules.scope_capture.analysis",
)


def _preflight(values: dict[str, object]) -> list[str]:
    """在通用参数预检之外，按条件检查温控开关映射。"""
    errors = ADAPTER.preflight(values)
    if errors:
        return errors
    try:
        params = ScopeCaptureParams.from_external({**ADAPTER.defaults().to_external(), **values})
    except (TypeError, ValueError):
        return errors
    if not params.disable_temperature_control:
        return errors
    mapping = load_mapping()
    config = mapping.get("Temp_Switch")
    if (
        not config
        or config.get("instrument") != "signal_generator"
        or not config.get("resource")
        or config.get("channel") is None
    ):
        errors.append("启用关闭温控采集时，Temp_Switch 必须解析为有效信号发生器通道")
    return errors

DEFINITION = ExperimentDefinition(
    id="scope-capture", title="示波器采集", category="measurement",
    family="scope", variant="single-channel",
    description=("单通道时域采集与软件 PSD，支持历史波形比较。" + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。'),
    data_type="Scope_Capture", required_devices=("SDS",),
    required_mapping_keys=("scope_waveform",), execution_mode="typed_workflow",
    acquisition_program="experiments/Scope_Capture.py",
    analysis_program="experiments/Scope_Capture_plot.py",
    wiring_notes=(
        "将待测信号接入选定的 SDS 通道。",
        "启用关闭温控采集时，Temp_Switch 映射通道必须连接温度开关信号。",
    ),
    safety_notes=(
        "结束时停止 SDS 采集并断开连接。",
        "关闭温控采集在正常、异常和取消路径均尝试恢复为 5V ON。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=_preflight, experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
