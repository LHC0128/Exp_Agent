"""Mx 高主场 Z 磁场频率标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZFieldCalibrationParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-z-field-calibration",
    "Mx_Z_Field_Calibration",
    MxZFieldCalibrationParams,
    "lab_workflows.experiment_modules.mx_z_field_calibration.workflow",
    "lab_workflows.experiment_modules.mx_z_field_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-field-calibration",
    title="Mx 高主场 Z 磁场频率标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-y-rf",
    description=("在固定主场的 Mx 构型下，逐 Z DC 电压扫描局部 Y RF 共振，离线拟合中心与 Hz/V 线性标定。"
        "运行页提供可选步骤、节点参数、共享草稿与显式重跑，分步等待保持设备连接和硬件锁。"
        "每频点按配置、关温控、SYSTEM_SETTLE_S、采入内存、恢复温控、保存执行；TEC 只写目标温度。"
        "独立分析按具体运行目录保存覆盖草稿，不改历史采集参数；锁相型号与 DAQ/Scope 能力在启动前检查。分步角色由清单声明，状态与产物以执行记录为准。"),
    data_type="Mx_Z_Field_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Field_Calibration.py",
    analysis_program="experiments/Mx_Z_Field_Calibration_plot.py",
    wiring_notes=(
        "主场和 Pump 光沿 Z、Probe 光沿 X；Z DC 与 Y RF 的设备及通道由当前物理量映射解析。",
        "锁相放大器使用 DEMOD_IDX 所选解调器，以每个 Y RF 扫频点直接解调，只采集 R。",
    ),
    safety_notes=(
        "每个频点关闭温控采集并立即恢复 5 V ON；Z 偏置切换后不设置专用等待。",
        "正常、取消和异常均归零关闭 Z 偏置与 Y RF，保留主场、光功率、Pump 链路和 锁相放大器。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)

