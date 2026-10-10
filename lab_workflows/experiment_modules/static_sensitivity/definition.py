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
    description='Z 直流逐点扫描，稳定后按采样率和每点采集时间连续记录 X/Y/R 波形并取中位数，分组恢复温控；采集 Z 输出关闭时的多量程 Y 噪声，离线使用全部有效色散点拟合线形并计算静磁场灵敏度。 锁相设备在设备库中全系统统一选择；启动前检查输入通道和工作频率兼容性。',
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
    schema_version=StaticSensitivityParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
    auto_analyze=True,
)
