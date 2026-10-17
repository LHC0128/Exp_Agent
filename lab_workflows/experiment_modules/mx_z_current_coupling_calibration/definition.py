"""Mx Z 实际电流-耦合强度标定实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZCurrentCouplingCalibrationParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-current-coupling-calibration",
    "Mx_Z_Current_Coupling_Calibration",
    MxZCurrentCouplingCalibrationParams,
    "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.workflow",
    "lab_workflows.experiment_modules.mx_z_current_coupling_calibration.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-current-coupling-calibration",
    title="Mx Z 实际电流-耦合强度标定",
    category="calibration",
    family="z-field-calibration",
    variant="mx-current-sense",
    description=('固定主场下逐 Z DC 先采 SDS CH3 电阻电压，再做局部 Y RF/R 共振频扫，重建实际线圈电流并标定 Z 耦合强度；保留正反向扫描。采集按准备、激励、SDS 准备、联合扫描与安全封存拆分，完整和分步执行共用业务。每个电流点及每个频点独立关闭温控，按 SYSTEM_SETTLE_S 稳定，读入内存后恢复温控再保存；TEC 仅写 UI 目标。离线分析按运行资格、共振拟合、实际电流/电阻电压加权标定与科学图导出分步执行，保留磁滞、重复性及失败诊断；目录覆盖只调整原有线性度阈值。运行/历史双 Tab 在流程顶部保存、预检及完整运行，节点内编辑共享参数和分类；草稿应用后显式重跑，历史分析对象独立，等待及安全结束期间持锁。结果依据科学 success 显示质量结论，保留原两图与逐曲线诊断。锁相型号在设备库统一选择，预检检查完整频率窗口，输入保持高阻与 DC 耦合，保存最终实际采样率。'),
    data_type="Mx_Z_Current_Coupling_Calibration",
    required_devices=("GS200", "DG900", "DG4000", "锁相放大器", "SDS"),
    required_mapping_keys=(
        "main_magnetic_field",
        "Z_magnetic_field",
        "Time_sequence_2",
        "rf_coil",
        "scope_waveform",
        "lockin_r",
    ),
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Current_Coupling_Calibration.py",
    analysis_program="experiments/Mx_Z_Current_Coupling_Calibration_plot.py",
    wiring_notes=(
        "Z 线圈串联低端采样电阻，SDS CH3 跨接采样电阻；普通示波器地只接 DG 信号地。",
        "Mx 共振扫描沿用 mx-z-field-calibration 的主场、光路和 RF 接线。",
    ),
    safety_notes=(
        "实测阻值和额定功率未填写时预检失败。",
        "采样电阻电压仅用于实际电流重建，不把示波器地接到浮置高端。",
    ),
    schema_version=ADAPTER.params_type.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
