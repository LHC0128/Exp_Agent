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


def preflight(values):
    """运行前复用与分步会话相同的无硬件业务校验。"""
    errors = ADAPTER.preflight(values)
    if not errors:
        from .workflow import preflight as check
        try:
            check(ADAPTER._resolved(values), ADAPTER.root)
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
            errors.append(str(exc))
    return errors

DEFINITION = ExperimentDefinition(
    id="xy-direct-aw-dc-calibration",
    title="XY DirectAW DC 标定",
    category="calibration",
    family="xy-calibration",
    variant="direct-aw",
    description="以 DirectAW 恒定包络电压扫描 Z RF 频率，拟合峰位以标定控制。运行／历史双 Tab 提供真实可勾选步骤，节点内编辑、顶部保存／预检／完整运行；完整与分步共用业务，草稿应用后显式重跑。物理回环仅支持 HF2，MFLI 在连接前拒绝，输入高阻／DC；恢复温控后保存并抢救已读样本。独立目录分析保留原 Bloch／Lorentzian 回退、固定杂散排除、边界筛选及正负支路／绝对电压标定，无额外覆盖参数。",    data_type="XY_DirectAW_DC_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/XY_DirectAW_DC_Calibration.py",
    analysis_program="experiments/XY_DirectAW_DC_Calibration_plot.py",
    wiring_notes=("运行前按实验文档和 mapping.yaml 核对 DirectAW、Z RF 与物理回环接线。",),
    safety_notes=("预检通过后才允许启动输出；异常和取消时恢复温控并关闭磁场输出。",),
    schema_provider=ADAPTER.schema, schema_version=ADAPTER.params_type.schema_version,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
