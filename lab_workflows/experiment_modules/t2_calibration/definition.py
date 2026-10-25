"""T2 光学 FID 标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import T2CalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "t2-calibration",
    "T2_Calibration",
    T2CalibrationParams,
    "lab_workflows.experiment_modules.t2_calibration.workflow",
    "lab_workflows.experiment_modules.t2_calibration.analysis",
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
    id="t2-calibration",
    title="T2 标定",
    category="calibration",
    family="relaxation",
    variant="t2",
    description="扫描 Probe 光功率，通过光学 FID 阻尼振荡拟合 T2。运行／历史双 Tab 内按真实节点编辑、勾选和分步执行，完整与分步共用准备、采集和安全收尾；独立分析绑定明确目录。保留原始 NPZ、触发后裁剪与平均、raw/recovery 抢救帧、原始异常及收尾失败语义。配置完成后关闭温控，按 SYSTEM_SETTLE_S 稳定，恢复 5 V ON 后保存；TEC 只写 UI 目标温度。结束保持主场、光功率、Pump 调制、RF 门控及其当前 Burst 和锁相设置，只断开 TEC。",    data_type="T2_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "SDS", "锁相放大器"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/T2_Calibration.py",
    analysis_program="experiments/T2_Calibration_plot.py",
    wiring_notes=("Pump_modulation CH1 提供 AOM 载波，Time_sequence CH2 提供门控并同步触发 SDS CH4；PD 接 SDS CH1。",),
    safety_notes=("正常、异常、取消和 Ctrl+C 均将温度开关恢复为 5V ON，保持主磁场、Pump/Probe 光、Pump 调制、RF 门控输出和全部 锁相放大器 设置；RF 门控保持当前 Burst 状态，其他辅助输出归零关闭；结束时只断开 TEC 通信。",),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
