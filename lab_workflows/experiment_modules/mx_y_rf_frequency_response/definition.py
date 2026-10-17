"""Mx Y 向 RF 频率响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFFrequencyResponseParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-frequency-response",
    "Mx_Y_RF_Frequency_Response",
    MxYRFFrequencyResponseParams,
    "lab_workflows.experiment_modules.mx_y_rf_frequency_response.workflow",
    "lab_workflows.experiment_modules.mx_y_rf_frequency_response.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-frequency-response",
    title="Mx Y 向 RF 频率响应",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-y-frequency",
    description=(
        "在 Mx 工作点嵌套扫描 Y RF 幅度和频率，采集 R(f)，用统一布洛赫稳态线形拟合弱驱动单峰与 Rabi 劈裂双峰。"
        "运行页按节点编辑参数、预检、完整运行或勾选分步执行；应用草稿后显式重跑，等待期间保持设备会话和硬件锁。"
        "每点完成配置后以 0 V DC + ON 关闭温控，按 SYSTEM_SETTLE_S 稳定，采集到内存后恢复温控并保存；TEC 仅写 UI 目标。"
        "分析列可选择下一次实验或历史目录；拟合覆盖值不改历史采集快照。历史页保留响应图、S–Vpp² 诊断、填回和重新分析。"
        "锁相型号、时钟与 DAQ/Scope 由设备库选择并严格预检，输入保持高阻和 DC 耦合。"
    ),
    data_type="Mx_Y_RF_Frequency_Response",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Frequency_Response.py",
    analysis_program="experiments/Mx_Y_RF_Frequency_Response_plot.py",
    wiring_notes=(
        "接线与工作点和 mx-y-rf-sensitivity 相同：主场/Pump 沿 Z，Probe 沿 X；rf_coil 映射通道产生 Y 向 RF 场，X_magnetic_field 映射通道保持关闭。",
        "锁相放大器 Demod0 使用 Input 1，振荡器跟随 Y RF 频率直接解调；DAQ 只订阅 R。",
        "时钟按 devices.yaml 中各设备的 reference_clock 设置，并在输出前回读验证。",
    ),
    safety_notes=(
        "正常、异常和取消均关闭归零 Z 辅助场、Y RF 与 X 场，温控恢复 5 V DC + ON；Pump 载波与 Time_sequence 常开门控是实验专用保留输出，收尾沿用最近成功配置。",
        "每个 (幅度, 频率) 点使用 R_BAD_POINT_STD_THRESHOLD_V 与 R_POINT_MAX_ATTEMPTS 判定、保存和重采，耗尽仍不合格则停止并保留诊断。",
        "RF 幅度轴范围在写入前按 rf_coil 安全限值校验。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
