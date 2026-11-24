"""独立射频相位灵敏度的注册定义；导入不连接设备。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import RfPhaseSensitivityParams
from .preparation import preflight


class Adapter(TypedWorkflowAdapter):
    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]
        return []

    def save_defaults(self, values):
        params = self._resolved(values)
        preflight(params, self.root)
        params.save_yaml(self.defaults_path)


MODULE = "lab_workflows.experiment_modules.rf_phase_sensitivity"
ADAPTER = Adapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RfPhaseSensitivityParams,
                  f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    id=run_format.EXPERIMENT_ID, title="射频相位灵敏度", category="measurement", family="z-chain",
    variant="rf-phase-sensitivity", data_type=run_format.DATA_TYPE,
    description=("采用 MFLI Signal Output 1（MF-MOD 相干边带）产生 Y 向 5 kHz 连续载波与可选偏置，"
                 "与 Z 外触发 ±π 双脉冲配合，使用独立解调器 Demod 4 寻找最大局部切线并对齐到 sample.y，"
                 "由 DHO924 同帧标定零频斜率 K₀。开启固定 2° 峰值相干边带 PM 只扫基带调制频率，再关闭边带保留同一载波测噪声，"
                 "用同轮实测相位频响折算输入等效相位噪声。"),
    documentation="docs/experiments/rf_phase_sensitivity.md",
    required_devices=("DG4000", "MFLI", "Aigtek 1200B", "DHO924", "GS200", "TEC103（可选设温）"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RfPhaseSensitivityParams.schema_version,
    acquisition_program="experiments/RfPhaseSensitivity.py", analysis_program="experiments/RfPhaseSensitivity_plot.py",
    wiring_notes=("Time_sequence_2 分路到 Z 外触发端与 DHO924 CH3，并与 MFLI 共用 10 MHz 外部参考。",
                  "MFLI Signal Output 1 接 Y 方向电路（串联大电阻）与 DHO924 CH4（1× 高阻 DC）；不再使用 DG4000 Y 通道。",
                  "Z_magnetic_field 接 1200B 与 Z 线圈双脉冲链；MFLI Signal Input 1 接探测器并用 Demod 4 独立读出（Demod 1–3 由 MOD 占用）。"),
    safety_notes=("每轮预检峰值电流、MFLI Signal Output 1 ±10 V 安全包络（含边带与偏置）及电阻平均功耗；强射频不在本方案中。",
                  "温控窗口内暂存采集，恢复后才落盘。结束先关功放、关 MFLI Out1/MOD 与 Z/X 并归零回读；恢复温控、主场与示波器。"),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze,
)
