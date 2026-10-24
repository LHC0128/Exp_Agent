"""BB Burst Pump 功率扫描的强类型定义与无副作用适配器。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...acquisition_policy import migrate_system_settle
from ...common import emit, WorkflowCancelled
from .models import BBPowerScanBurstParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "固定 TTL 占空比与内部触发 Burst，扫描 Pump DC 控制电压，SDS 记录建立与暗弛豫。"
    "运行／历史双 Tab 提供 8 个采集与 3 个独立分析节点；节点内编辑、顶部保存、预览、预检与完整运行。"
    "草稿应用后须显式重跑，分步等待保持连接、输出和全局硬件锁。"
    "每帧配置后关闭温控，按 SYSTEM_SETTLE_S 稳定，读到内存、恢复温控后保存；TEC 只写目标温度。"
    "独立分析保留 Hilbert 包络、建立/暗弛豫拟合、有效泵浦率及原五组科学图，历史分析覆盖按目录隔离。"
    "正常、异常、取消与通信结束均保留 Time_sequence 输出/Burst/SYNC 及最后写入 Pump 电压，恢复温控，只断开 TEC。"
)


class BBPowerAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        migrated = migrate_system_settle(values, self.params_type.system_settle_legacy_defaults, ())
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


ADAPTER = BBPowerAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, BBPowerScanBurstParams,
    "lab_workflows.experiment_modules.bb_power_scan_burst.workflow",
    "lab_workflows.experiment_modules.bb_power_scan_burst.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="BB Pump 功率扫描", category="measurement",
    family="bb-scan", variant="burst", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "信号发生器", "SDS", "TEC103（可选）"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=BBPowerScanBurstParams.schema_version,
    acquisition_program="experiments/BB_Rpump_PowerScan_Burst.py", analysis_program="experiments/BB_Rpump_PowerScan_Burst_plot.py",
    wiring_notes=("Pump_laser_power 扫描 DC 强度；Pump_modulation 提供 AOM 载波；Time_sequence 固定 TTL 门控 RF 开关，SYNC 接 SDS 触发。",
        "SDS PD 通道读取原子信号，触发通道仅作触发；通道由参数指定，仪器型号与端点按当前映射。"),
    safety_notes=("结束后门控输出、Burst、SYNC 以及最后写入 Pump 电压继续保持，仪器不等于已停止输出。",
        "温控恢复为 5 V DC + ON；其他磁场输出关闭，只断开 TEC；分步等待仍占硬件锁。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
