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
                 "与 Z 外触发 ±π 双脉冲配合，使用独立解调器 Demod 4 整周初定并经多阶谐波细化工作点后对齐到 sample.y，"
                 "以 MFLI 指令角加密局部斜率标定 K₀ 并据此更新后续 A05/A06 工作点。开启固定 2° 峰值相干边带 PM 单次顺序扫描基带调制频率，"
                 "再关闭边带保留同一载波按独立温控分段采集无调制噪声（每点/每段均先恢复温控等待后再测下一段），全程仅在 MFLI 锁相采集并用同轮实测相位频响折算输入等效相位噪声。"),
    documentation="docs/experiments/rf_phase_sensitivity.md",
    required_devices=("DG4000", "MFLI", "GS200", "TEC103（可选设温）"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RfPhaseSensitivityParams.schema_version,
    acquisition_program="experiments/RfPhaseSensitivity.py", analysis_program="experiments/RfPhaseSensitivity_plot.py",
    wiring_notes=("Time_sequence_2 接入 Z 外触发端，并与 MFLI 共用 10 MHz 外部参考时钟（不使用 DHO924 示波器）。",
                  "MFLI Signal Output 1 接 Y 方向射频电路（串联大电阻）；不再使用 DG4000 Y 通道。",
                  "Z_magnetic_field 直连 Z 线圈（1200B 已物理旁路），DG 负载配成高阻，回路电阻计入 DG 源阻抗；"
                  "MFLI Signal Input 1 接探测器并用 Demod 4 独立读出（Demod 1–3 由 MOD 占用）。"),
    safety_notes=("每轮预检 Z_PULSE_VPP/2、DG 高阻档上限、按已知回路电阻估算的峰值电流、"
                  "MFLI Signal Output 1 ±10 V 安全包络（含边带与偏置）及电阻平均功耗；强射频不在本方案中。",
                  "线圈电流为按已知回路电阻估算，非直接测量；判据与报告都标注估算值。",
                  "温控窗口内暂存采集，恢复后才落盘。结束 Z/X 归零回读、关 MFLI Out1/MOD；恢复温控与主场。"),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze,
)
