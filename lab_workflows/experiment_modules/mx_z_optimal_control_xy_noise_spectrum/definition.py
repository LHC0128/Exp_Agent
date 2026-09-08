"""Mx Z 最优控制 XY 偏置噪声谱实验定义。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import OPTIMAL_CONTROL
from ...experiments.typed import TypedWorkflowAdapter
from .models import MxZOptimalControlXYNoiseSpectrumParams


ADAPTER = TypedWorkflowAdapter(
    "mx-z-optimal-control-xy-noise-spectrum",
    "Mx_Z_Optimal_Control_XY_Noise_Spectrum",
    MxZOptimalControlXYNoiseSpectrumParams,
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.workflow",
    "lab_workflows.experiment_modules.mx_z_optimal_control_xy_noise_spectrum.analysis",
)

DEFINITION = ExperimentDefinition(
    id="mx-z-optimal-control-xy-noise-spectrum",
    title="Mx Z 最优控制 XY 噪声谱",
    category="measurement",
    family="noise",
    variant="mx-z-optimal-control-xy-bias",
    description="在 Z 周期最优控制下扫描 X/Y DC 偏置，关闭 Y RF 交流分量，测量 Demod0 R 噪声谱并按指定频段中位数寻找最低噪声点。",
    data_type="Mx_Z_Optimal_Control_XY_Noise_Spectrum",
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=OPTIMAL_CONTROL,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum.py",
    analysis_program="experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py",
    wiring_notes=(
        "Z_magnetic_field DG4000 CH1 输出周期最优控制；Time_sequence_2 CH2 为共同触发。",
        "X_magnetic_field 输出 X DC 偏置；Y_magnetic_field 与 rf_coil 共用通道并输出 Y DC 偏置。",
        "噪声采集时 Y RF Burst 和调制关闭，Y DC 偏置保留；HF2 Demod0 只采集 R。",
    ),
    safety_notes=(
        "每个 X/Y 输出值和 Z 控制波形在下发前通过安全校验。",
        "每个点的温控按 0 V 关闭、采集后恢复 5 V；取消和异常路径执行统一安全停机。",
        "正常、取消和异常结束关闭归零共同触发及 X/Y 场，保持 Z 最优控制输出。",
        "结果坐标单位为信号发生器电压 V，不输出未经标定的 nT 结果。",
    ),
    schema_version=MxZOptimalControlXYNoiseSpectrumParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
)
