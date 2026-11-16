"""Z 链路零点与脉冲角稳定性的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...common import WorkflowCancelled, emit
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import ZChainStabilityParams
from .preparation import preflight

DESCRIPTION = (
    "π 脉冲准备的电学稳定性标定：只测 Z 通道 → Aigtek 1200B → 阻尼电阻 → 14 匝线圈 → 10 Ω 采样电阻回路，"
    "不连接原子、TEC 或 GS200。A03 在 DG 0 V 下测功放零偏、I–V 小直流与长时间零点；"
    "A04 按目标电流经实测 I–V 反推电压（或直接写电压）后记录工作基线残流；"
    "A05 连续输出 100 µs 周期的 ±π 双脉冲，按间隔捕获短窗并逐脉冲积分，给出 θ=2π·K·∫I dt 的分布。"
    "零点长记把窗口均值追加到 CSV，默认每帧原始波形另存 NPZ，可同时看 20 ms 窗口内噪声与逐帧漂移；"
    "脉冲段分别记录 +π 与 −π 的 Q/θ。"
    "三段都可单独勾选；结束先关闭 1200B，再把 Z 通道归零并关闭同步/输出，停止并断开 DHO900。"
)


class ZChainStabilityAdapter(TypedWorkflowAdapter):
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
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("分析已取消，原始数据保留")
        emit(progress, "analysis", f"分析 {run_dir.name}", 90, estimated_remaining_seconds=None)
        self._execute(self.analysis_module, run_dir=run_dir, progress=progress, cancellation=cancellation)
        return {"run_dir": str(run_dir), "artifacts": [str(p) for p in (run_dir / "results").iterdir() if p.is_file()]}


MODULE = "lab_workflows.experiment_modules.z_chain_stability"
ADAPTER = ZChainStabilityAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, ZChainStabilityParams,
                                 f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/z_chain_stability.md",
    id=run_format.EXPERIMENT_ID, title="Z 链路零点与脉冲角稳定性", category="calibration",
    family="z-chain", variant="stability", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("信号发生器", "Aigtek 1200B", "DHO900"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    acquisition_exception=True,
    execution_mode="typed_workflow", schema_version=ZChainStabilityParams.schema_version,
    acquisition_program="experiments/ZChainStability.py",
    analysis_program="experiments/ZChainStability_plot.py",
    wiring_notes=(
        "Z_magnetic_field 通道 → 1200B 输入；A02 按 AMPLIFIER_INPUT_IMPEDANCE_OHM 写入 1200B 输入阻抗"
        "（1 MΩ 或 50 Ω）并把信号源输出负载配成一致。",
        "1200B 正端 → 阻尼电阻 → 14 匝线圈 → 10 Ω 采样电阻 → 1200B 返回地；1200B 输出须 DC 耦合、单端对地。",
        "DHO900 SCOPE_CURRENT_CHANNEL 正端接线圈与采样电阻之间、地夹接返回地；SCOPE_SYNC_CHANNEL 接 Z 通道同步输出。",
    ),
    safety_notes=(
        "z_pulse_coil_current 与 Z_magnetic_field 必须有有限上下界；计划电流、基线电压、脉冲峰谷与电阻功耗在连接前预检。",
        "A02 在功放输出关闭、信号源停输出时写入 1200B 增益与输入/输出阻抗；带载改这些设置被安全回调拒绝。",
        "每帧实测电流越限、最大量程仍削顶或同步缺失即中止并安全收尾；A04 写入电压前按实测 I–V 重算并校验。",
    ),
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
