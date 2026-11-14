"""线圈驱动链路电学特性的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...common import WorkflowCancelled, emit
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import CoilDriveCharacterizationParams
from .preparation import preflight

DESCRIPTION = (
    "π 脉冲准备第 1 步：不需要原子，测 Z 通道 → Aigtek 1200B → 阻尼电阻 → 线圈 → 采样电阻回路的电学特性。"
    "A02 按参数写入 1200B 的增益与输入/输出阻抗，并把信号源输出负载配成与输入阻抗一致；"
    "A03 在功放关/开两态、信号源 0 V 下测功放零偏，并用小直流核对实测 I/V 落在按写入增益与回路总电阻"
    "（含功放输出阻抗）预测值的下偏比例与上偏倍数之间；"
    "A04 正弦扫频得到 |I/V_drive|、相对同步沿的相位与 −3 dB 带宽；A05 用 0→I 矩形脉冲测上升/下降时间、过冲与 ∫I dt。"
    "离线分析给出零偏、传递函数，以及面积偏差与平台峰值同时满足容差的最短实测脉宽。"
    "计划电流须在 z_pulse_coil_current 限值内，每帧实测越限即中止；纯电学工具，不连接 TEC、不操作温控。"
    "结束先关闭 1200B，再将 Z 通道 DC 0 V、关闭同步与输出，停止并断开 DHO900。"
)


class CoilDriveAdapter(TypedWorkflowAdapter):
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


MODULE = "lab_workflows.experiment_modules.coil_drive_characterization"
ADAPTER = CoilDriveAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, CoilDriveCharacterizationParams,
                           f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/coil_drive_characterization.md",
    id=run_format.EXPERIMENT_ID, title="线圈驱动链路电学特性", category="calibration",
    family="coil-drive", variant="aigtek-1200b", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("信号发生器", "Aigtek 1200B", "DHO900"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    acquisition_exception=True,
    execution_mode="typed_workflow", schema_version=CoilDriveCharacterizationParams.schema_version,
    acquisition_program="experiments/Coil_Drive_Characterization.py",
    analysis_program="experiments/Coil_Drive_Characterization_plot.py",
    wiring_notes=(
        "Z_magnetic_field 通道 → 1200B 输入；A02 按 AMPLIFIER_INPUT_IMPEDANCE_OHM 写入 1200B 输入阻抗"
        "（1 MΩ 或 50 Ω）并把信号源输出负载配成一致。",
        "1200B 正端 → 阻尼电阻 → 线圈 → 采样电阻 → 1200B 返回地；1200B 输出须 DC 耦合、单端对地。",
        "DHO900 SCOPE_CURRENT_CHANNEL 正端接线圈与采样电阻之间、地夹接返回地；SCOPE_SYNC_CHANNEL 接 Z 通道同步输出。",
    ),
    safety_notes=(
        "z_pulse_coil_current 必须有有限上下界；计划电流、信号源电平和电阻平均功耗在连接前预检。",
        "A02 在功放输出关闭、信号源停输出时写入 1200B 增益与输入/输出阻抗；带载改这些设置被安全回调拒绝。",
        "开启功放前按 1200B safety_check 重新预检；每帧实测越限、增益上偏超过 GAIN_TOLERANCE 或下偏低于 "
        "GAIN_LOWER_RATIO、最大量程仍削顶即中止并安全收尾。",
    ),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
