"""Z 线圈常数标定的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...common import WorkflowCancelled, emit
from ...experiments.contracts import ExperimentDefinition, RunHistory
from ...experiments.typed import TypedWorkflowAdapter
from . import run_format
from .models import CoilConstantCalibrationParams
from .preparation import preflight

DESCRIPTION = (
    "Z 线圈驱动链路电学特性的下一步：用原子磁力仪标定 Z 通道 → Aigtek 1200B → 阻尼电阻 → 线圈 → 采样电阻"
    "这条链路的电流→场常数。A04 先测功放零偏与直流 I–V；A05 主磁场可设电流或只关输出；A08 用参考点与斜率探针"
    "两次宽窗扫频给出 f_ref、K_boot 与过零估计；A09 在电流轴逐点实测电流并局部扫描 Y RF，预测 |中心| 低于零场下限"
    "的点只测电流不扫频。离线分析按 f=|K·(I−I0)| 加权拟合，并用过零两侧的支线一致性、往返磁滞与电压—场闭合比"
    "检查质量；结果同时给出 Hz/mA 与 nT/mA。每个原子频点按普通气室六步时序关温控采集、恢复后保存；TEC 只写目标温度。"
    "结束先关 1200B，再把 Z 通道归零关闭、Y RF 关、X 归零、温控恢复 5 V ON、停止 DHO900；保持 GS200 本次设定与光路/Pump。"
)


class CoilConstantAdapter(TypedWorkflowAdapter):
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
        return {"run_dir": str(run_dir),
                "artifacts": [str(path) for path in (run_dir / "results").iterdir() if path.is_file()]}


MODULE = "lab_workflows.experiment_modules.coil_constant_calibration"
ADAPTER = CoilConstantAdapter(
    run_format.EXPERIMENT_ID, run_format.DATA_TYPE, CoilConstantCalibrationParams,
    f"{MODULE}.workflow", f"{MODULE}.analysis")
DEFINITION = ExperimentDefinition(
    documentation="docs/experiments/coil_constant_calibration.md",
    id=run_format.EXPERIMENT_ID,
    title="Z 线圈常数标定",
    category="calibration",
    family="coil-drive",
    variant="atomic-field-calibration",
    description=DESCRIPTION,
    data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "DG4000", "Aigtek 1200B", "DHO900", "DG900", "锁相放大器"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    acquisition_exception=False,
    execution_mode="typed_workflow",
    acquisition_program="experiments/Coil_Constant_Calibration.py",
    analysis_program="experiments/Coil_Constant_Calibration_plot.py",
    wiring_notes=(
        "Z_magnetic_field 通道 → 1200B 输入；A02 按参数写入 1200B 增益与输入/输出阻抗，并把信号源输出负载配成一致。",
        "1200B 正端 → 阻尼电阻 → Z 线圈 → 10 Ω 采样电阻 → 1200B 返回地；DHO900 只开采样电阻通道、AUTO 触发测直流电压，不需要同步通道。",
        "主场由 GS200 电流模式提供，可设已知电流或只关输出；主场电流与对应拉莫尔频率手填，主扫描以该频率为锚点、按预测斜率粗扫找峰后细扫；Pump/Probe、Y RF 与温控按当前物理量映射解析。",
    ),
    safety_notes=(
        "z_pulse_coil_current 必须有有限上下界；计划电流、驱动电平、电阻功耗与 Y RF 在连接前预检。",
        "A02 在功放输出关闭、信号源停输出时写入增益与阻抗；带载改这些设置被安全回调拒绝。",
        "输出写入前仍按 z_pulse_coil_current 与 Z_magnetic_field 限值校验；运行期不设实测电流判据，A04 的零偏与 I–V 只记录 k、偏置与线性。",
        "主磁场开启时按 main_magnetic_field 限值校验；关闭时只关 GS200 输出、不写电流，两种状态都记录。",
    ),
    schema_version=CoilConstantCalibrationParams.schema_version,
    schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run,
    analysis_runner=ADAPTER.analyze,
    run_history=RunHistory(run_format=run_format, historical_parameters=run_format.recorded_parameters),
)
