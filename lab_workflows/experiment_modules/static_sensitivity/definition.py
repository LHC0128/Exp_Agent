"""静磁场灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import STATIC_SENSITIVITY
from ...experiments.typed import TypedWorkflowAdapter
from .models import StaticSensitivityParams


ADAPTER = TypedWorkflowAdapter(
    "static-sensitivity",
    "Static_Magnetic_Field_Sensitivity",
    StaticSensitivityParams,
    "lab_workflows.experiment_modules.static_sensitivity.workflow",
    "lab_workflows.experiment_modules.static_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="static-sensitivity",
    title="静磁场灵敏度",
    category="measurement",
    family="static-field",
    variant="dispersion-noise",
    description=('Z 直流逐点扫描，连续记录 X/Y/R 波形并取中位数，分组恢复温控；采集 Z 输出关闭时的多量程 Y 噪声，离线拟合与计算静磁场灵敏度。运行页提供采集、分析两列交互流程图：节点编辑参数、显式选择步骤与依赖检查，完整运行和分步会话共用业务代码；历史分析仅独立覆盖 z_v_to_nt。等待下一步持续占用硬件锁，执行中取消、空闲时安全结束；时序 2 始终保持当前状态。锁相设备按当前设备库预检，输入统一使用高阻与 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。'),
    data_type="Static_Magnetic_Field_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "TEC103", "锁相放大器"),
    required_mapping_keys=STATIC_SENSITIVITY,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Static_Magnetic_Field_Sensitivity.py",
    analysis_program="experiments/Static_Magnetic_Field_Sensitivity_plot.py",
    wiring_notes=(
        "Pump DG4000 CH1 为 100 MHz Gated Burst，外部门控输入接同机 CH2 脉冲。",
        "CH2 为 0–5 V 高有效门控脉冲，直接控制 CH1 载波是否送入 AOM。",
        "Z 通道纯 DC 逐点扫描；锁相放大器 DAQ 连续单帧采集解调 X/Y，不需要 SYNC/AUX 触发接线。",
        "时序 2 保持当前状态；固定 X/Y DC 补偿按参数保持，不施加 XY 交流控制。"
    ),
    safety_notes=(
        "Pump、光功率、主磁场、Z 偏置和温控输出均在写入前通过安全限值。",
        "本实验测量期间温控物理输出 OFF（实验专属例外），分组固定等待恢复；结束为 5 V DC + ON。",
        "正常、取消或异常时归零关闭 Z，保持时序 2 当前状态并恢复色散解调配置；保留主场、光功率、Pump 门控和固定 X/Y 补偿。"
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
    auto_analyze=True,
)
