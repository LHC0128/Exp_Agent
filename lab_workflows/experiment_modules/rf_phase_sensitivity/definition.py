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
    description=("在固定 Y 向 5 kHz、50 mVpp 载波和 Z 双脉冲条件下，逐轮找到原子相位响应的最大局部切线，"
                 "把切线对准 MFLI 的 Y 正交分量，并用线圈实测相位差标定零频斜率。"
                 "固定原生 PM 深度后用 DHO924 的共同触发与线圈电流监视回调物理相位，只扫基带调相频率；"
                 "再关调制、保留并复核同一载波工作点，记录无调制噪声。"
                 "用同轮实测相位频响折算输入等效噪声，不用幅度响应线宽代替相位频响。"),
    documentation="docs/experiments/rf_phase_sensitivity.md",
    required_devices=("DG4000", "MFLI", "Aigtek 1200B", "DHO924", "GS200", "TEC103（可选设温）"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RfPhaseSensitivityParams.schema_version,
    acquisition_program="experiments/RfPhaseSensitivity.py", analysis_program="experiments/RfPhaseSensitivity_plot.py",
    wiring_notes=("Time_sequence_2 分路到 Z 与 Y 的外触发端及 DHO924 CH3，共用 10 MHz 外部参考。",
                  "DHO924 CH4 接 Y 线圈电流监视，不接 DG 面板输出；CH3/CH4 均为 1× 高阻 DC。",
                  "Z_magnetic_field 接 1200B 与 Z 线圈双脉冲链；MFLI Signal Input 1 接探测器。"),
    safety_notes=("每轮预检峰值电流、输出包络与电阻平均功耗；强射频不在本方案中。",
                  "温控窗口内暂存采集，恢复后才落盘。结束先关功放、Y/X/Z 关闭归零并回读；恢复温控、主场与示波器。"),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze,
)
