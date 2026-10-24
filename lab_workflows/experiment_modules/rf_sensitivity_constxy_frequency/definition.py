"""ConstXY 正式强类型定义与无副作用适配器。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...acquisition_policy import migrate_system_settle
from ...common import emit, WorkflowCancelled
from .models import RFConstXYFrequencyParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "固定 XY 外部 AM 载波与 DC 包络，扫描 Z 射频场频率；保留无限 Burst／外触发。"
    "运行／历史双 Tab 提供 11 个采集与 3 个独立分析节点，节点内编辑，顶部保存、派生预览、预检及完整运行；"
    "草稿应用后须显式重跑，等待保持连接、输出和硬件锁。"
    "0 Hz 是关闭 Z 扰动、XY 保持开启的背景，不是直流响应点；仅 FFT 使用原背景扣除。"
    "双主电压物理回环仅支持 HF2，选择 MFLI 时连接前拒绝，不自动切换。"
    "每频点配置后关闭温控，按 SYSTEM_SETTLE_S 稳定，采集到内存，恢复 5 V DC + ON 后保存；TEC 只写目标温度。"
    "离线保留原 FFT／软件锁相／硬件矢量与标量 R、带宽、Lorentzian／Bloch 拟合和诊断图；"
    "历史分析草稿按目录隔离、不改采集快照。正常、异常、取消及通信结束统一关闭 XY／Z、恢复温控，只断开 TEC，"
    "保留主场、光功率、Pump、时序和锁相设置。"
)


class ConstXYAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        migrated = migrate_system_settle(values, self.params_type.system_settle_legacy_defaults, self.params_type.removed_temperature_keys)
        return self.params_type.from_external(self.defaults().to_external() | migrated)

    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
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


ADAPTER = ConstXYAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RFConstXYFrequencyParams,
    "lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.workflow",
    "lab_workflows.experiment_modules.rf_sensitivity_constxy_frequency.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="RF 灵敏度频率扫描（ConstXY）", category="measurement",
    family="rf-sensitivity", variant="const-xy", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "信号发生器", "HF2", "TEC103"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=RFConstXYFrequencyParams.schema_version,
    acquisition_program="experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py",
    analysis_program="experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py",
    wiring_notes=("主解调 Y → Aux 输出 → 物理同轴线 → Signal Input 2 → RF 解调器；两路主电压输入高阻、DC 耦合。",
        "XY 载波接线与 AM DC 包络按映射；Z 无限 Burst 使用外触发；Pump 门控 SYNC 与时序2方波保留原接线协议，运行前人工核对。"),
    safety_notes=("0 Hz 背景只关 Z，XY 保持开启；每频点恢复温控后保存。",
        "统一安全收尾关闭 XY／Z，保留主场、光功率、Pump／时序和锁相设置，只断开 TEC；分步等待仍占硬件锁。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
