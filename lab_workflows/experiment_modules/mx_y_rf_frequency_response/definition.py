"""Mx Y 向 RF 频率响应实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFFrequencyResponseParams


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
        ('在 Mx 工作点用模式 1 的固定幅度扫频方式测量 Y RF 频率响应曲线；RF 幅度可作为嵌套扫描轴，分析端用统一布洛赫稳态线形拟合，覆盖弱驱动 Lorentzian 与强驱动 Rabi 劈裂双峰。 锁相设备在设备库中全系统统一选择；启动前检查输入通道和工作频率兼容性。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。')
    ),
    data_type="Mx_Y_RF_Frequency_Response",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Frequency_Response.py",
    analysis_program="experiments/Mx_Y_RF_Frequency_Response_plot.py",
    wiring_notes=(
        "接线与工作点和 mx-y-rf-sensitivity 相同：主场/Pump 沿 Z，Probe 沿 X；rf_coil CH2 产生 Y 向 RF 场，X_magnetic_field CH1 保持关闭。",
        "锁相放大器 Demod0 使用 Input 1，振荡器跟随 Y RF 频率直接解调；DAQ 只订阅 R。",
        "时钟按 devices.yaml 中各设备的 reference_clock 设置，并在输出前回读验证。",
    ),
    safety_notes=(
        "正常、异常和取消均关闭归零 Z 辅助场、Y RF 与 X 场，温控恢复 5V ON；Pump 100MHz 载波与 Time_sequence 5V DC ON 是实验专用保留输出。",
        "每个 (幅度, 频率) 点仅在 std(R)≤0.01V 时接受，最多重采3次，连续失败即停止。",
        "RF 幅度轴范围在写入前按 rf_coil 安全限值校验。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
