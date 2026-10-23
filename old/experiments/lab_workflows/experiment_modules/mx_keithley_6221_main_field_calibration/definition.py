"""Mx Keithley 6221 主磁场频率标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxKeithley6221MainFieldCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-keithley-6221-main-field-calibration",
    "Mx_Keithley_6221_Main_Field_Calibration",
    MxKeithley6221MainFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_keithley_6221_main_field_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-keithley-6221-main-field-calibration",
    title="Mx Keithley 6221 主磁场频率标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-keithley-6221",
    description=(
        ('Keithley 6221 独占 Z 主线圈并按可配置电流轴扫描，以 Y RF 共振中心标定自由斜率和截距。 锁相设备在设备库中全系统统一选择；启动前检查输入通道和工作频率兼容性。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。')
    ),
    data_type="Mx_Keithley_6221_Main_Field_Calibration",
    required_devices=("Keithley 6221", "GS200", "DG900", "DG4000", "锁相放大器"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Keithley_6221_Main_Field_Calibration.py",
    analysis_program="experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py",
    wiring_notes=(
        "Keithley 6221 独占连接 Z 主线圈；GS200 必须从该线圈物理断开。",
        "Z_magnetic_field 与 X_magnetic_field 保持 0 且输出关闭；rf_coil 提供 Y 向 RF。",
    ),
    safety_notes=(
        "运行前必须明确确认 GS200 已物理断开；软件同时强制 GS200 为 0 mA 且输出关闭。",
        "6221 使用实验参数选择的固定电流档位与 1 V Compliance；命中 Compliance 立即终止。",
        "正常、取消、异常和配置失败均将 6221 与 GS200 归零并关闭输出。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
