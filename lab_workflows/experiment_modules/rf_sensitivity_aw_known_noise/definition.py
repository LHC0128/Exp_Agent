"""已知噪声下的 DirectAW RF 灵敏度正式定义。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from ...experiments.typed import TypedWorkflowAdapter
from ..rf_sensitivity_direct_aw.definition import analyze_runner
from .models import RFSensitivityKnownNoiseParams
from .preparation import preflight
from . import run_format


class KnownNoiseAdapter(TypedWorkflowAdapter):
    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return [str(exc)]


ADAPTER = KnownNoiseAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RFSensitivityKnownNoiseParams,
    "lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.workflow",
    "lab_workflows.experiment_modules.rf_sensitivity_aw_known_noise.analysis")
DEFINITION = ExperimentDefinition(id=run_format.EXPERIMENT_ID, title="已知噪声下 RF 灵敏度（DirectAW）",
    category="measurement", family="rf-sensitivity", variant="direct-aw-known-noise",
    description=("每轮固定一种 Z 周期伪噪声，只测注入 ON；支持选择一个或多个同 RF 设计频率的最优控制版本。Z 噪声与 RF 在物理电压层先求和再逐点归一化，零 RF 保留噪声；逐版本独立校相、标定与采集，输出 RF 解调后基带灵敏度谱。运行/历史双 Tab 使用 DirectAW 分步流程图和节点参数，等待保持硬件锁；离线分析支持本实验参数版本 2、3、4 的完整运行，逐版本展示 X/Y/R 响应、X 拟合与灵敏度完整图，并提供 PNG/PDF 下载；同时保留 RF 响应/灵敏度与 Z RF 相位响应聚合对比图，图例直接使用控制版本名称。" + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。 累积响应和校相 NPZ 经临时文件原子替换，写入失败保留上一份完整快照。'),
    data_type=run_format.DATA_TYPE, required_devices=("GS200", "信号发生器", "锁相放大器"),
    required_mapping_keys=RF_SENSITIVITY_DIRECT_AW, execution_mode="typed_workflow", schema_version=ADAPTER.params_type.schema_version,
    acquisition_program="experiments/RF_Field_Sensitivity_AW_Known_Noise.py",
    analysis_program="experiments/RF_Field_Sensitivity_AW_Known_Noise_plot.py",
    wiring_notes=("Z 噪声与 RF 共用 Z_magnetic_field 的单一物理输出，不增加重复端点映射。Time_sequence_2 主输出触发 XY 与 Z；RF 回采线路同 DirectAW。",),
    safety_notes=("输出前检查叠加极值与仪器能力；结束关闭 XY 与 Z，恢复温控 5 V，保持 Pump 与共同触发；取消和 EOF 同样安全收尾。",),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight, derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run,
    analysis_runner=lambda run_dir, progress: analyze_runner(run_dir, progress, adapter=ADAPTER, block_reason=run_format.analysis_block_reason))
