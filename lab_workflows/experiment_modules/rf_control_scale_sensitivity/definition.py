"""多控制 Scale RF 响应与灵敏度的正式注册。"""
from ...experiments.contracts import ExperimentDefinition
from ...experiments.typed import TypedWorkflowAdapter
from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from ..rf_sensitivity_direct_aw.definition import analyze_runner
from .models import RFControlScaleParams
from .preparation import preflight
from . import run_format


class ScaleAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        if "CONTROL_SCALES" in values:
            migrated = RFControlScaleParams.migrate_external(values, 1)
            values = values | {name: migrated[name] for name in
                ("CONTROL_SCALE_START", "CONTROL_SCALE_STOP", "CONTROL_SCALE_POINTS")}
        return super()._resolved(values)

    def derive(self, values):
        return RFControlScaleParams.derive_external(values)

    def preflight(self, values):
        try:
            preflight(self._resolved(values), self.root)
            return []
        except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
            return [str(exc)]


ADAPTER = ScaleAdapter(run_format.EXPERIMENT_ID, run_format.DATA_TYPE, RFControlScaleParams,
    "lab_workflows.experiment_modules.rf_control_scale_sensitivity.workflow",
    "lab_workflows.experiment_modules.rf_control_scale_sensitivity.analysis")
DEFINITION = ExperimentDefinition(
    id=run_format.EXPERIMENT_ID, title="多控制 Scale RF 响应与灵敏度", category="measurement",
    family="rf-sensitivity", variant="control-scale",
    description="从理论控制目录读取并勾选多个控制，按起点、终点和点数扫描完整控制包络倍率，支持降序、零和负数，不要求包含 1；各控制可手动设置独立补偿倍率，默认 1，实际包络乘扫描与补偿倍率，图横轴保持扫描倍率；未采集扫描倍率 1 时仅输出绝对指标、不做归一化；每个控制 × Scale 独立执行 Demod0、XY 和 RF 解调器校相；RF 触发相位可逐点扫描，也可在 A13 关闭扫描后全部组合共用本实验手动相位，然后进行响应、可选零 RF 噪声及 R 扫频，其余校相不跨点继承，目标 RF 频率保持固定。支持从已结束运行续测：完整点带来源复制到新目录，中断点按本轮相位设置重新校相并整点重测，扫描模式或手动相位不一致时拒绝续测；可显式跳过被外部软件占用的 TEC 串口，独立温控开关照常执行。每点测有符号 RF 响应、可选零 RF X 噪声及必测 R 扫频，按 RF DirectAW 的四参数色散拟合比较中心斜率 |A|/γ²、色散等效线宽、洛伦兹扫频中心频率和公共基带灵敏度，图例直接使用所选控制波形名称；噪声可按保守组内高值规则剔除个别异常，保留原始 PSD、未剔除结果与清单，支持独立只读诊断区分整组偏差和单次异常；不注入人为噪声，不自动追峰。运行/历史双 Tab 提供可勾选、带依赖的交互流程图、节点参数及独立离线分析。配置后关闭温控，稳定并采集至内存，恢复温控后保存；异常先恢复再抢救数据。灵敏度保留零频增益等效与单极点模型估计，不把 R 线宽当作完整 X 动态频响标定。",
    data_type=run_format.DATA_TYPE, execution_mode="typed_workflow", schema_version=RFControlScaleParams.schema_version,
    required_devices=("GS200", "信号发生器", "锁相放大器"), required_mapping_keys=RF_SENSITIVITY_DIRECT_AW,
    acquisition_program="experiments/RF_Control_Scale_Sensitivity.py",
    analysis_program="experiments/RF_Control_Scale_Sensitivity_plot.py",
    wiring_notes=("沿用 RF DirectAW 的 XY 控制、Z RF、共同外触发及所选锁相回采线路；不接入人为噪声。",),
    safety_notes=("检查所有控制及全部 Scale 的电压极值；结束关闭 XY/Z，恢复温控 5 V，保留 Pump、主场及共同触发。",),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=ADAPTER.preflight, derive_runner=ADAPTER.derive, experiment_runner=ADAPTER.run,
    analysis_runner=lambda directory, progress: analyze_runner(directory, progress, adapter=ADAPTER, block_reason=run_format.analysis_block_reason))
