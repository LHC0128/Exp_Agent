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
    description=("扫描 Probe AOM AM 频率并采集锁相 Y 谱峰，检查探测链路响应；输入保持单端、高阻、DC 耦合，零基带点不参与响应与归一化。"
                 "每点配置后以 0 V DC + 输出 ON 关闭温控，按 SYSTEM_SETTLE_S 稳定并采集至内存，恢复温控后保存；异常也先尝试恢复并抢救已读数据。"
                 "TEC 可连接时只写 UI 目标温度。运行页共用 A01–A08 采集和 B01–B03 离线分析流程，支持节点改参、显式重跑与具体历史目录重算；本实验无可调分析覆盖值。分步角色由清单声明，状态与产物以执行记录为准。"),
    data_type="Detection_Chain_Frequency_Response",
    required_devices=("DG4000", "DG900", "锁相放大器"),
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
        "Probe_AOM_Carrier 按映射通道启用外部 AM，Probe_AOM_AM 连接其外部 AM 输入；当前同机背板接线。",
        "锁相主电压输入 0 接 BPD 信号，使用单端、高阻、DC 耦合；采集 lockin_r 映射解调器的 Y 信号。",
        "Pump/Probe 光功率按映射设置；TEC103 可连接时只写 UI 目标温度，不启停或判稳，串口失败告警跳过。",
        "Temp_Switch 每点以 0 V DC + 输出 ON 关闭温控，采集后恢复 5 V DC + 输出 ON，与 Pump 光功率无关。",
    ),
    safety_notes=(
        "Probe AOM 载波幅度受 safety_limits.yaml 限制。",
        "AM 调制频率范围应覆盖关注的基带噪声频率（通过 ±锁相放大器 解调频率映射）。",
        "实验结束后关闭 Probe AOM 载波和 AM 输出。",
        "取消或异常也恢复 Temp_Switch 为 5 V DC，物理输出保持 ON。",
    ),
    schema_provider=ADAPTER.schema, schema_version=ADAPTER.params_type.schema_version,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
