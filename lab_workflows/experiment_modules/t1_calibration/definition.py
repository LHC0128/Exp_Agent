"""T1 强类型定义、旧参数迁移与无硬件预检接线。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...common import emit, WorkflowCancelled
from .models import T1CalibrationParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "低频方波开关 Pump，CH1 采集 PDB 差分、CH4 同步下降沿触发；支持单点与 Probe 控制电压扫描。"
    "运行／历史双 Tab 提供 8 个采集与 3 个独立分析节点，节点编辑参数，顶部保存、预览、预检与完整运行。"
    "草稿应用后显式重跑；等待保留连接、输出和全局硬件锁。每帧配置后关闭温控，按 SYSTEM_SETTLE_S 稳定，"
    "采集至内存、恢复温控后保存；TEC 只写 UI 目标。离线保留指数衰减、倒数弛豫率与旧数据诊断，历史分析覆盖按目录隔离。"
    "正常、异常、取消与通信结束均将 X/Y/Z 归零关闭，恢复温控 5 V + ON；保留 Probe、Pump 载波与门控，只断开 TEC。分步角色由清单声明，状态与产物以执行记录为准。"
)


class T1Adapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        migrated = T1CalibrationParams.migrate_external(values, 1)
        return self.params_type.from_external(self.defaults().to_external() | migrated)

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
        return {"run_dir": str(run_dir), "artifacts": [str(p) for p in (run_dir/"results").iterdir() if p.is_file()]}


ADAPTER = T1Adapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, T1CalibrationParams,
    "lab_workflows.experiment_modules.t1_calibration.workflow", "lab_workflows.experiment_modules.t1_calibration.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="T1 标定", category="calibration",
    family="relaxation", variant="t1", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("信号发生器", "SDS", "TEC103（可选）"), required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS,
    execution_mode="typed_workflow", schema_version=T1CalibrationParams.schema_version,
    acquisition_program="experiments/T1_Calibration.py", analysis_program="experiments/T1_Calibration_plot.py",
    wiring_notes=("PDB 差分输出接 SDS CH1；Time_sequence 同步输出接 CH4，下降沿为 Pump OFF 的 t=0。",
        "Pump_modulation 为 100 MHz 载波，Time_sequence 为低频 50% 方波；本实验不采集双 PD、不实现缓变沿关断。"),
    safety_notes=("结束时 X/Y/Z 场归零关闭；Pump/Probe、载波和门控保持，温控恢复 5 V DC + ON，只断开 TEC。",
        "分步等待仍保持输出并占用硬件锁；执行中取消，空闲时显式结束会话。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
