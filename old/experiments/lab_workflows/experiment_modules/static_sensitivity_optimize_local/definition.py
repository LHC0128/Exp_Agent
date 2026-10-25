"""局部优化正式定义；采集批次与标准 DC 子运行共享历史目录但严格区分身份。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import STATIC_SENSITIVITY
from ...experiments.typed import TypedWorkflowAdapter
from . import preparation, run_format
from .models import StaticSensitivityOptimizeLocalParams


class LocalAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        # 先迁移显式旧键，再合并已保存默认值，避免默认新键遮蔽旧等待。
        values = self.params_type.migrate_external(values, 1)
        return self.params_type.from_external(self.defaults().to_external() | values)

    def _latest_run(self, before):
        base = self.root / "data" / self.data_type
        runs = [path for path in base.iterdir() if path.is_dir() and not run_format.format_block_reason(run_format.read_config(path))]
        candidates = [path for path in runs if path not in before] or runs
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    def preflight(self, values):
        try:
            preparation.preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]


ADAPTER = LocalAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, StaticSensitivityOptimizeLocalParams,
    "lab_workflows.experiment_modules.static_sensitivity_optimize_local.workflow",
    "lab_workflows.experiment_modules.static_sensitivity_optimize_local.analysis")

DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="静磁场灵敏度局部优化",
    category="optimization", family="static-field", variant="local", data_type=run_format.DATA_TYPE,
    execution_mode="typed_workflow", required_mapping_keys=STATIC_SENSITIVITY, required_devices=("GS200", "信号发生器", "锁相放大器", "TEC103"),
    description="每个候选从同一基准出发，只改变一个参数并重新校相；复用静磁场灵敏度的 Z 直流逐点 X/Y/R 中位数和 Z OFF 的 Y 噪声采集，不使用 RAMP/SYNC。运行/历史双 Tab 提供节点参数、依赖分步执行及独立批次分析，顶部保存、预检与完整运行；应用草稿后显式重跑，等待期间保持连接与硬件锁。每组恢复温控后保存，TEC 仅下发目标温度；结束归零关闭 Z、恢复温控与最后已执行色散配置，保持主场、光功率、Pump、时序2及固定 XY 补偿。单点使用标准 DC 拟合并即时分析；父分析仅重建基准漂移、排名和建议，无历史覆盖参数，不自动施加最佳点。旧 RAMP 批次只读取已有结论。",
    acquisition_program="experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py",
    analysis_program="experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local_plot.py",
    wiring_notes=("Z 通道纯 DC 逐点扫描，锁相连续采集 X/Y 并计算 R，无需 Z→AUX/SYNC 触发接线。",
        "Pump 载波和脉冲门控与静磁场主实验一致；时序2始终保持当前状态。"),
    safety_notes=("全部候选在连接前校验限值、量程及锁相能力；输入高阻/DC，时钟严格回读。",
        "温控关闭为 0 V DC + ON，恢复为 5 V DC + ON；配置完成后统一稳定，恢复后保存。",
        "采集、恢复或写盘异常立即停止并抢救数据；质量坏点不参与最佳点选择；最佳点仅为建议。"),
    schema_version=StaticSensitivityOptimizeLocalParams.schema_version, schema_provider=ADAPTER.schema,
    defaults_saver=ADAPTER.save_defaults, preflight_runner=ADAPTER.preflight, derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run, analysis_runner=ADAPTER.analyze, auto_analyze=True)
