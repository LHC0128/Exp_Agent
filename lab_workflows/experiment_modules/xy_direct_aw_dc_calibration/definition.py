"""XY DirectAW DC 标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import XYDirectAWDCCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "xy-direct-aw-dc-calibration",
    "XY_DirectAW_DC_Calibration",
    XYDirectAWDCCalibrationParams,
    "lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.workflow",
    "lab_workflows.experiment_modules.xy_direct_aw_dc_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="xy-direct-aw-dc-calibration",
    title="XY DirectAW DC 标定",
    category="calibration",
    family="xy-calibration",
    variant="direct-aw",
    description=('标定 DirectAW 方案的 XY DC 响应。 当前双主电压回采接线只支持 HF2，选择 MFLI 时预检禁止启动。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。'),
    data_type="XY_DirectAW_DC_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/XY_DirectAW_DC_Calibration.py",
    analysis_program="experiments/XY_DirectAW_DC_Calibration_plot.py",
    wiring_notes=("运行前按实验文档和 mapping.yaml 核对 DirectAW、Z RF 与物理回环接线。",),
    safety_notes=("预检通过后才允许启动输出；异常和取消时恢复温控并关闭磁场输出。",),
    schema_provider=ADAPTER.schema, schema_version=ADAPTER.params_type.schema_version,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
