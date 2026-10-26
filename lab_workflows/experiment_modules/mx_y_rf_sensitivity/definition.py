"""Mx Y 向 RF 场灵敏度实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxYRFParams
from .run_format import REQUIRED_MAPPING_KEYS


ADAPTER = TypedWorkflowAdapter(
    "mx-y-rf-sensitivity",
    "Mx_Y_RF_Sensitivity",
    MxYRFParams,
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity.workflow",
    "lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-y-rf-sensitivity",
    title="Mx Y 向 RF 场灵敏度",
    category="measurement",
    family="rf-sensitivity",
    variant="mx-y-amplitude",
    description=('Mx 构型只采集 R，不执行相位校准；以带符号 Y RF 幅度的绝对值色散响应、两种线宽和零 RF 噪声测量灵敏度。'
        '运行页提供可勾选的准备、激励、扫频门槛、幅度响应、噪声和安全结束步骤，节点内编辑参数并预览草稿影响。'
        '完整与分步共用采集业务；历史目录的独立分析覆盖值与下一次实验参数隔离。'
        '结束仅保留已执行的 Pump 与响应配置，不下发尚未重跑的参数。'
        '采集完成恢复温控后保存，TEC 仅下发 UI 目标温度；锁相型号和 DAQ／Scope 方式遵循设备库，频谱使用最终实际率。分步角色由清单声明，状态与产物以执行记录为准。'),
    data_type="Mx_Y_RF_Sensitivity",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器"),
    required_mapping_keys=REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Y_RF_Sensitivity.py",
    analysis_program="experiments/Mx_Y_RF_Sensitivity_plot.py",
    wiring_notes=(
        "主磁场和 Pump 光沿 Z，Probe 光沿 X；rf_coil CH2 产生 Y 向 RF 场，X_magnetic_field CH1 保持关闭。",
        "锁相放大器 Demod0 使用 Input 1，并以 Y RF 频率直接解调；DAQ 只订阅 R，不执行 X/Y 相位校准。",
        "时钟按 devices.yaml 中各设备的 reference_clock 设置，并在输出前回读验证。",
    ),
    safety_notes=(
        "正常、异常和取消均关闭归零 Z 辅助场、Y RF 与 X 场，温控恢复 5V ON；Pump 最近成功配置的载波与 Time_sequence 5V DC ON 是实验专用保留输出。",
        "应用参数不下发保留输出；首次 Pump 配置前结束使用会话初始参数，噪声后的响应恢复只使用最近成功的响应配置。",
        "正常结束只断开 TEC；主场、Pump/Probe 光功率和 锁相放大器 配置保留。",
        "幅度点和模式1频率点按 R_BAD_POINT_STD_THRESHOLD_V 与 R_POINT_MAX_ATTEMPTS 重采门槛执行，连续失败即停止后继。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
