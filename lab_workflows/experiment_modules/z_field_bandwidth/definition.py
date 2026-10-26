"""Z 场带宽的正式定义、强类型适配及无副作用预检。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...acquisition_policy import migrate_system_settle
from ...common import emit, WorkflowCancelled
from .models import ZFieldBandwidthParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "固定小信号 Z 正弦幅度扫描频率，比较主解调 Y 的 FFT、软件锁相与硬件 R，计算 −3 dB 带宽。"
    "运行／历史双 Tab 提供 10 个采集与 3 个独立分析节点；参数在节点内编辑，顶部保存、预览、预检及完整运行。"
    "0 Hz 仅为关闭 Z 的背景，XY 保持关闭；双主电压回环仅支持 HF2，MFLI 在连接前拒绝。"
    "每点配置后关温控、按 SYSTEM_SETTLE_S 稳定、采集至内存、恢复 5 V DC + ON 后保存；TEC 只写目标。"
    "共享过程参数同步编辑，草稿应用后显式重跑；历史分析覆盖按目录隔离，不改采集快照。"
    "等待仍占硬件锁；正常、异常、取消及通信结束统一关闭 XYZ、恢复温控，只断开 TEC，保持已执行主场、光功率、Pump、时序与锁相设置。分步角色由清单声明，状态与产物以执行记录为准。"
)


class ZBandwidthAdapter(TypedWorkflowAdapter):
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


ADAPTER = ZBandwidthAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, ZFieldBandwidthParams,
    "lab_workflows.experiment_modules.z_field_bandwidth.workflow", "lab_workflows.experiment_modules.z_field_bandwidth.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="Z 场带宽", category="measurement",
    family="field-response", variant="standard", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "信号发生器", "HF2", "TEC103"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=ZFieldBandwidthParams.schema_version,
    acquisition_program="experiments/Z_Field_Bandwidth_Measurement.py", analysis_program="experiments/Z_Field_Bandwidth_Measurement_plot.py",
    wiring_notes=("主解调 Y → Aux 输出 → 物理同轴线 → Signal Input 2 → 硬件 R 解调器；两路主电压输入高阻、DC 耦合。",
        "Pump 100 MHz 连续载波经 RF 开关，由 Time_sequence 脉冲门控；Z 连续正弦，不启用 Burst。Time_sequence_2 输出原 10 Hz 零偏置方波。"),
    safety_notes=("0 Hz 是 Z OFF 背景，不是真实直流响应；X/Y 预设后始终关闭。",
        "每点恢复温控后保存；统一关闭 XYZ，只断开 TEC，保持主场、光功率、Pump、时序及锁相配置。等待下一模块仍占硬件锁。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
