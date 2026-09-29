"""探测链路频率响应标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import DetectionChainFrequencyResponseParams


ADAPTER = TypedWorkflowAdapter(
    "detection-chain-frequency-response",
    "Detection_Chain_Frequency_Response",
    DetectionChainFrequencyResponseParams,
    "lab_workflows.experiment_modules.detection_chain_frequency_response.workflow",
    "lab_workflows.experiment_modules.detection_chain_frequency_response.analysis",
)


def preflight(values: dict) -> list[str]:
    errors = ADAPTER.preflight(values)
    if errors:
        return errors
    from ...common import load_mapping
    try:
        mapping = load_mapping(ADAPTER.root)
        if ADAPTER._resolved(values).hf2_demod_idx != int(mapping["lockin_r"]["demod_idx"]):
            return ["HF2_DEMOD_IDX 与 lockin_r 映射不一致"]
    except (KeyError, ValueError) as exc:
        return [str(exc)]
    return []

DEFINITION = ExperimentDefinition(
    id="detection-chain-frequency-response",
    title="探测链路频率响应标定",
    category="calibration",
    family="frequency",
    variant="baseline",
    description="对照 XY 正弦控制噪声谱的 HF2 解调和采样设置，但关闭输入 AC 耦合；扫描 Probe AOM AM 频率并采集 Demod0 Y 谱峰，零基带点不参与响应及归一化。无论 Pump 光功率多少，每点采集期间都关闭温控并恢复，以检查不可控噪声下降来源。TEC103 不可连接时由外部软件维持温度。",
    data_type="Detection_Chain_Frequency_Response",
    required_devices=("DG4000", "DG900", "HF2"),
    required_mapping_keys=(
        "Probe_AOM_Carrier",
        "Probe_AOM_AM",
        "lockin_r",
        "Pump_laser_power",
        "Probe_laser_power",
        "temperature",
        "Temp_Switch",
    ),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Detection_Chain_Frequency_Response.py",
    analysis_program="experiments/Detection_Chain_Frequency_Response_plot.py",
    wiring_notes=(
        "Probe_AOM_Carrier (CH1) 启用外部 AM，AM 输入连接 Probe_AOM_AM (CH2) 通过背板。",
        "HF2 信号输入 0 接 BPD 信号，采集前关闭 AC 耦合，使用单端、50 Ω；Demod0 Y 用于采集。",
        "程序通过 DG900 设置 Pump/Probe 光功率；TEC103 可连接时设置并等待稳定，否则跳过设温和温度检测。",
        "Temp_Switch 映射设备每点输出 0 V DC 关闭温控，采集后恢复 5 V DC，与 Pump 光功率无关。",
    ),
    safety_notes=(
        "Probe AOM 载波幅度受 safety_limits.yaml 限制。",
        "AM 调制频率范围应覆盖关注的基带噪声频率（通过 ±HF2 解调频率映射）。",
        "实验结束后关闭 Probe AOM 载波和 AM 输出。",
        "取消或异常也恢复 Temp_Switch 为 5 V DC，物理输出保持 ON。",
    ),
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
