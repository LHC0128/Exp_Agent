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

DEFINITION = ExperimentDefinition(
    id="t2-calibration",
    title="T2 标定",
    category="calibration",
    family="relaxation",
    variant="t2",
    description="扫描 Probe 光功率并通过光学 FID 阻尼振荡拟合横向弛豫时间 T2。",
    data_type="T2_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "SDS", "HF2"),
    execution_mode="typed_workflow",
    acquisition_program="experiments/T2_Calibration.py",
    analysis_program="experiments/T2_Calibration_plot.py",
    wiring_notes=("Pump_modulation CH1 提供 AOM 载波，Time_sequence CH2 提供门控并同步触发 SDS CH4；PD 接 SDS CH1。",),
    safety_notes=("正常、异常、取消和 Ctrl+C 均将温度开关恢复为 5V ON，保持主磁场、Pump/Probe 光、Pump 调制、RF 门控输出和全部 HF2 设置；RF 门控只关闭 Burst，其他辅助输出归零关闭；结束时只断开 TEC 通信。",),
    schema_version=T2CalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
