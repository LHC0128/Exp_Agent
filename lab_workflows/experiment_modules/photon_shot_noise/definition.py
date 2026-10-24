"""光子散粒噪声的正式强类型定义；导入、schema 和预检均不连接硬件。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...acquisition_policy import migrate_system_settle
from ...common import emit, WorkflowCancelled
from .models import PhotonShotNoiseParams
from .preparation import preflight
from . import run_format

DESCRIPTION = (
    "扫描 Probe 控制电压，关闭 Pump 输出并偏置主场，采集同一主电压输入的解调 X/Y 时序。"
    "运行/历史双 Tab 使用 8 个采集与 3 个独立分析节点；参数在节点内编辑，顶部保存、预检和完整运行。"
    "应用草稿后须显式重跑；分步等待保持连接、输出和硬件锁。"
    "每次重复配置后关闭温控，按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复为 5 V DC + ON 后保存；TEC 仅写 UI 目标温度。"
    "锁相由设备库统一选择，使用高阻/DC 输入和自动 DAQ/Scope，频谱按最终实际率计算。"
    "离线保留 Welch PSD→平坦频段 ASD 中位数→重复均值平方的加权线性拟合及四组 PNG/PDF；不执行积分或洛伦兹分离。"
    "历史目录独立保存分析草稿，不改采集快照；正常、取消、异常和通信结束恢复温控，只断开 TEC，保持已执行主场、Probe、Pump OFF 和锁相设置。"
)


class PhotonShotNoiseAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        migrated = migrate_system_settle(values, self.params_type.system_settle_legacy_defaults)
        return self.params_type.from_external(self.defaults().to_external() | migrated)

    def analyze(self, run_dir, progress=None, *, cancellation=None):
        # 分析状态由 analysis_state.json 负责，不改写历史采集配置。
        if cancellation and cancellation.cancelled:
            raise WorkflowCancelled("分析已取消，原始数据保留")
        emit(progress, "analysis", f"分析 {run_dir.name}", 90, estimated_remaining_seconds=None)
        self._execute(self.analysis_module, run_dir=run_dir, progress=progress, cancellation=cancellation)
        results = run_dir / "results"
        return {"run_dir": str(run_dir), "artifacts": [str(p) for p in results.iterdir() if p.is_file()]}

    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]


ADAPTER = PhotonShotNoiseAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, PhotonShotNoiseParams,
    "lab_workflows.experiment_modules.photon_shot_noise.workflow",
    "lab_workflows.experiment_modules.photon_shot_noise.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="光子散粒噪声", category="measurement",
    family="fundamental-noise", variant="photon", description=DESCRIPTION, data_type=run_format.DATA_TYPE,
    required_devices=("GS200", "信号发生器", "锁相放大器", "TEC103"),
    required_mapping_keys=run_format.REQUIRED_MAPPING_KEYS, execution_mode="typed_workflow",
    schema_version=PhotonShotNoiseParams.schema_version,
    acquisition_program="experiments/Photon_shot_noise.py", analysis_program="experiments/Photon_shot_noise_plot.py",
    wiring_notes=("平衡探测器接所选锁相的主电压输入 0；X/Y 为同一输入的正交解调分量，不是两路物理输入。",
        "Pump/Probe、温控开关和主场按当前设备映射接线；EXT 时钟须人工确认参考线缆。"),
    safety_notes=("扫描电压、主场和目标温度在连接前和写入前校验。Pump 输出始终关闭。",
        "每次重复恢复温控后保存；取消不作为坏记录吞掉，恢复/写盘失败抢救已读数据。",
        "收尾保留已执行的主场、Probe、Pump OFF 与锁相设置；温控恢复 5 V DC + ON，只断开 TEC。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight,
    derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze)
