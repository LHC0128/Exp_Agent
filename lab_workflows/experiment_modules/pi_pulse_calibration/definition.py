"""π 脉冲面积标定的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import PiPulseCalibrationParams
from .preparation import preflight

DESCRIPTION = (
    "只用电学链路（Z 通道 → 1200B → 阻尼电阻 → 线圈 → 采样电阻）标定 π 脉冲：固定峰值电流，"
    "逐个单脉冲宽度打 ±π 双脉冲，每次捕获存原始波形并逐发积分，由 θ = 2πK∫(I−I_base)dt 得到角度，"
    "线性内插出 θ = 目标角（默认 π）对应的宽度，并与按线圈常数预测的宽度对比。"
    "全程不连接原子、TEC 或 GS200；结束先关闭 1200B，再把 Z 通道归零并关闭同步/输出。"
)


class PiPulseCalibrationAdapter(TypedWorkflowAdapter):
    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]

    def save_defaults(self, values):
        params = self._resolved(values)
        preflight(params, self.root)
        params.save_yaml(self.defaults_path)

    def analyze(self, run_dir, progress=None, *, cancellation=None):
        from ...common import WorkflowCancelled, emit
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("分析已取消，原始数据保留")
        emit(progress, "analysis", f"分析 {run_dir.name}", 90, estimated_remaining_seconds=None)
        self._execute(self.analysis_module, run_dir=run_dir, progress=progress, cancellation=cancellation)
        return {"run_dir": str(run_dir), "artifacts": [str(p) for p in (run_dir / "results").iterdir() if p.is_file()]}


MODULE = "lab_workflows.experiment_modules.pi_pulse_calibration"
ADAPTER = PiPulseCalibrationAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, PiPulseCalibrationParams,
                                    f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/pi_pulse_calibration.md",
    id=run_format.EXPERIMENT_ID, title="π 脉冲面积标定", category="calibration",
    family="z-chain", variant="pi-area", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("信号发生器", "Aigtek 1200B", "DHO900"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    acquisition_exception=True,
    execution_mode="typed_workflow", schema_version=PiPulseCalibrationParams.schema_version,
    acquisition_program="experiments/PiPulseCalibration.py",
    analysis_program="experiments/PiPulseCalibration_plot.py",
    wiring_notes=(
        "Z_magnetic_field 通道 → 1200B 输入；A02 按 AMPLIFIER_INPUT_IMPEDANCE_OHM 写入输入阻抗并把信号源负载配成一致。",
        "1200B 正端 → 阻尼电阻 → 线圈 → 采样电阻 → 1200B 返回地；输出须 DC 耦合、单端对地。",
        "DHO900 SCOPE_CURRENT_CHANNEL 接线圈与采样电阻之间，SCOPE_SYNC_CHANNEL 接 Z 通道同步输出并作触发。",
    ),
    safety_notes=(
        "z_pulse_coil_current 与 Z_magnetic_field 必须有有限上下界；脉冲峰值、偏置与电阻功耗在连接前预检。",
        "A02 在功放输出关闭、信号源停输出时写增益与阻抗；带载改这些设置被安全回调拒绝。",
        "每帧实测电流越限或同步缺失即中止并安全收尾；结束先关 1200B 再归零 Z 通道。",
    ),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
