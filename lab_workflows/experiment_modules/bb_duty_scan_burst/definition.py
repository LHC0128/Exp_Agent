"""BB Pump 占空比扫描的正式强类型定义；schema 和预检均无硬件副作用。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...acquisition_policy import migrate_system_settle
from ...common import emit, WorkflowCancelled
from .models import BBDutyScanBurstParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "固定 Pump DC 光功率，扫描有限 Burst 内的脉冲占空比，SDS 采集 Bell-Bloom 瞬态；锁相仅可选连接监控。"
    "运行／历史双 Tab 提供 8 个采集与 3 个独立分析节点；节点内编辑，顶部保存、派生预览、预检及完整运行。"
    "共享参数同步编辑，草稿应用后显式重跑；分步等待保持连接、输出与全局硬件锁。"
    "每帧配置后关温控，按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复 5 V DC + ON 后保存；TEC 只写 UI 目标温度。"
    "每点最多三次探测后锁档重复平均，已读帧独立保留；离线保留 Hilbert 建立、全振荡暗衰减拟合、有效泵浦率与五组诊断图。"
    "具体历史目录仅独立覆盖暗衰减拟合时长，不修改下一次采集参数或历史 raw。"
    "正常、异常、取消及通信结束停止 SDS、恢复温控、关闭辅助场；保留主场、光功率、Pump 载波与 Time_sequence 输出/Burst/SYNC、锁相设置，仅断开 TEC。分步角色由清单声明，状态与产物以执行记录为准。"
)


class BBDutyAdapter(TypedWorkflowAdapter):
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


ADAPTER = BBDutyAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, BBDutyScanBurstParams,
    "lab_workflows.experiment_modules.bb_duty_scan_burst.workflow", "lab_workflows.experiment_modules.bb_duty_scan_burst.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="BB Pump 占空比扫描", category="measurement",
    family="bb-scan", variant="burst", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "信号发生器", "SDS", "TEC103"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=BBDutyScanBurstParams.schema_version,
    acquisition_program="experiments/BB_Rpump_DutyScan_Burst.py", analysis_program="experiments/BB_Rpump_DutyScan_Burst_plot.py",
    wiring_notes=("Pump_modulation 连续载波经过 RF 开关驱动 AOM，Time_sequence 的固定 TTL 0/5 V 脉冲控制 RF 开关。",
        "Time_sequence 的正极性 SYNC → SDS 触发通道，PD → 独立 SDS 检测通道；上升沿定义 t=0，暗段由配置 Burst 时长确定，不读取触发通道波形。"),
    safety_notes=("占空比使用 0～1 比例，逐值换算百分比校验 PUMP_MOD_DUTY；不以 TTL 幅度代替 Pump 光功率。",
        "每帧恢复温控后写盘，异常保留已读数据；结束保留 Time_sequence 输出、Burst 与 SYNC，停止 SDS，仅断开 TEC。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
