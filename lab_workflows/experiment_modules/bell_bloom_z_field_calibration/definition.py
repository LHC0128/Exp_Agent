"""Bell Bloom Z 磁场频率标定注册。"""

from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...common import load_mapping
from .models import BellBloomZFieldCalibrationParams

EXPERIMENT_ID = "bell-bloom-z-field-calibration"
DATA_TYPE = "Bell_Bloom_Z_Field_Calibration"
MAPPING_KEYS = (
    "main_magnetic_field", "Z_magnetic_field", "Time_sequence_2",
    "X_magnetic_field", "Y_magnetic_field", "Pump_laser_power",
    "Probe_laser_power", "Pump_modulation", "Time_sequence",
    "Temp_Switch", "temperature", "lockin_r",
)
ADAPTER = TypedWorkflowAdapter(
    EXPERIMENT_ID, DATA_TYPE, BellBloomZFieldCalibrationParams,
    "lab_workflows.experiment_modules.bell_bloom_z_field_calibration.workflow",
    "lab_workflows.experiment_modules.bell_bloom_z_field_calibration.analysis",
)


def validate_hardware(mapping):
    for key in ("Z_magnetic_field", "Pump_modulation", "Time_sequence"):
        if mapping[key]["model"] != "DG4000":
            raise ValueError(f"{key} 需要已确认的 DG4000 硬件方案")
    if mapping["lockin_r"]["demod_idx"] != 0:
        raise ValueError("本实验只使用 HF2 Demod0，请将 lockin_r 映射到 Demod0")


def preflight(values):
    errors = ADAPTER.preflight(values)
    try:
        validate_hardware(load_mapping())
    except (KeyError, ValueError) as exc:
        errors.append(str(exc))
    return errors


DEFINITION = ExperimentDefinition(
    id=EXPERIMENT_ID, title="Bell Bloom Z 磁场频率标定",
    category="calibration", family="z-field-calibration", variant="bell-bloom-gated",
    description="扫描 Z DC 与 Pump 调制/解调频率，拟合 R 共振中心并标定带符号的 Hz/V。",
    data_type=DATA_TYPE, required_devices=("GS200", "DG4000", "HF2"),
    required_mapping_keys=MAPPING_KEYS, execution_mode="typed_workflow",
    acquisition_program="experiments/Bell_Bloom_Z_Field_Calibration.py",
    analysis_program="experiments/Bell_Bloom_Z_Field_Calibration_plot.py",
    wiring_notes=("Pump 100 MHz 载波采用 Gated/External Burst，CH2 的 0–5 V 脉冲接外部门控输入。",
                  "Z DC 由映射指定的 DG4000 通道控制；HF2 Demod0 同步解调并只采集 R，X/Y 输出关闭。"),
    safety_notes=("每频点关闭温控采集后恢复 5 V ON；TEC 不可用时由外部软件控温。",
                  "结束时 Z 归零关闭，Pump 脉冲和 HF2 恢复零偏参考频率，保留主场及光功率。"),
    schema_version=1, schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight, experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze, auto_analyze=True,
)
