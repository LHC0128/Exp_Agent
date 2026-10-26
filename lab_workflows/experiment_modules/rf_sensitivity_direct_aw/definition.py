"""RF 灵敏度（DirectAW）的正式注册定义。"""

from pathlib import Path
import json
import yaml

from ...common import CancellationToken, ProgressCallback
from ...experiments.contracts import ExperimentDefinition
from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from ...experiments.typed import TypedWorkflowAdapter
from .models import RFSensitivityDirectAWParams
from .run_format import DATA_TYPE, EXPERIMENT_ID, analysis_block_reason
from .preparation import preflight

class DirectAWAdapter(TypedWorkflowAdapter):
    def _resolved(self, values):
        return super()._resolved(self.params_type.normalize_control_selection(values))


ADAPTER = DirectAWAdapter(EXPERIMENT_ID, DATA_TYPE, RFSensitivityDirectAWParams, "lab_workflows.experiment_modules.rf_sensitivity_direct_aw.workflow", "lab_workflows.experiment_modules.rf_sensitivity_direct_aw.analysis")


def preflight_runner(values, *, adapter=ADAPTER):
    try:
        preflight(adapter._resolved(values), adapter.root, experiment_id=adapter.id)
        return []
    except (OSError, KeyError, TypeError, ValueError, PermissionError) as exc:
        return [str(exc)]


def analyze_runner(run_dir: Path, progress: ProgressCallback | None, *, adapter=ADAPTER, block_reason=analysis_block_reason) -> dict:
    # 在通用适配器写分析状态前拒绝旧格式，保持历史目录只读。
    config = yaml.safe_load((Path(run_dir) / "experiment_config.yaml").read_text(encoding="utf-8"))
    reason = block_reason(config)
    if reason:
        raise ValueError(reason)
    return adapter.analyze(run_dir, progress)


def comparison_runner(runs: list[dict], output_dir: Path, progress: ProgressCallback | None = None, cancellation: CancellationToken | None = None, *, adapter=ADAPTER, module="lab_workflows.experiment_modules.rf_sensitivity_direct_aw.comparison") -> dict:
    if output_dir.exists():
        raise ValueError("对比输出目录已存在，请使用新的目录")
    try:
        adapter._execute(module,
                         parameters={"runs": runs}, run_dir=output_dir, progress=progress, cancellation=cancellation)
        return json.loads((output_dir / "comparison.json").read_text(encoding="utf-8"))
    except BaseException:
        # 子进程已导出但父进程收到取消时，也撤回下载完成标记。
        (output_dir / "comparison.json").unlink(missing_ok=True)
        raise

DEFINITION = ExperimentDefinition(
    id=EXPERIMENT_ID, title="RF 灵敏度（DirectAW）", category="measurement",
    family="rf-sensitivity", variant="direct-aw",
    description=('XY 直接任意波控制下扫描有符号 Z RF 幅度，最优控制波形从 theory_agent 的 oc_sens 版本目录多选；各控制独立 XY/RF 校相、响应、零 RF 噪声与可选扫频，共用本轮 RF 参数且不自动改频；A09 按“波形名称、勾选框、补偿系数”逐行展示，各控制可手动保存补偿倍率，未设为 1，只缩放 XY 完整物理包络后再按 K/B 转电压，不改变主场与 Z RF；相位与幅度扫描每点以定长 解调时序采集、按温控分组点数分组关温控，以 RF 解调器 X 响应斜率和 X 噪声 PSD 测量 RF 灵敏度；PSD 按控制独立在可配置频带以中位 ASD 评分，至少 5 条且有正基线才启用保守 MAD 高值整条剔除，可关闭；保留全部原始记录、逐条 PSD、未剔除结果及清单，频带未完整覆盖时明确保留全部记录；在 A14 零 RF X 噪声采集后可选执行 A13B，恢复与 A11 相同的响应滤波，以校相幅度同步扫描 Z RF 与 RF 解调频率，每点定长采集 R 时序，扫频步进由窗口与点数确定，无共同触发频率整数倍限制；R 均值洛伦兹拟合线宽用于灵敏度谱频响补偿，未启用时回退幅度扫描色散拟合的等效 HWHM。XYZ 共用时序信号2主输出触发，HF2/MFLI 经 Demod0 Y → 所选 AuxOut → 所选物理输入或 MFLI 内部回采 → RF 解调器，索引可在 GUI 设置；初始化含四级相位校准：Bell-Bloom（Demod0）、控制载波-Pump（XY DirectAW）、控制 AM 调制-RF 场（A11 可跳过 Z RF 触发相位扫描，改用手动输入的正 RF Burst 相位；启用扫描时按温控分组恢复温控）与 RF 解调器。运行页采用 17 个采集节点、4 个独立分析节点的可执行流程图，按节点编辑参数，共享值与分类同步，草稿先预览重跑范围再应用；分步等待保持设备会话与硬件锁，校相质量失败保存诊断并允许重试。A15 完成本轮安全收尾、A16 结束会话；具体目录的分析覆盖值独立保留，保存默认值只保存下一次实验设置。各控制独立保存并生成汇总图；响应、灵敏度和相位图例采用英文，中文控制名按选择顺序显示 Control N，目录和元数据保留原名；历史兼容旧单控制参数与结果；跨次 X 响应与灵敏度对比按控制展开曲线，保留自定义英文运行图例及 PNG/PDF 导出。本实验历史试点支持星标筛选、保留或恢复原始时间前缀的目录重命名，以及确认后永久删除整次数据；既有对比导出保留为生成时快照。仪器设置只回读记录，DAQ 使用实际采样率。所选 AuxOut 使用已确认的 ±10 V 安全规则，缺范围或预计越界禁止启动/写入。两款设备均可选 AuxOut1–4 与 Aux Input1–2，HF2 保留 Signal Input2，MFLI 支持内部 AuxOut；输出前核对真实选件，不支持的组合禁止启动。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复温控后保存；TEC 可连接时仅下发 UI 目标温度。 累积响应和校相 NPZ 经临时文件原子替换，写入失败保留上一份完整快照。分步角色由清单声明，状态与产物以执行记录为准。'),
    data_type=DATA_TYPE, execution_mode="typed_workflow", schema_version=ADAPTER.params_type.schema_version,
    required_devices=("GS200", "DG900", "DG4000", "lockin_amplifier"),
    required_mapping_keys=RF_SENSITIVITY_DIRECT_AW,
    acquisition_program="experiments/RF_Field_Sensitivity_AW.py",
    analysis_program="experiments/RF_Field_Sensitivity_AW_plot.py",
    wiring_notes=("Time_sequence_2 主输出分路连接 X/Y DirectAW 与 Z RF 的 Ext Trig，通道 SYNC 同步开启并在收尾后保持；物理线路将所选 AuxOut 经 BNC 接所选 Aux Input（HF2 可选 Signal Input2）；MFLI 内部线路不需要 BNC 回环线。",),
    safety_notes=("共同触发在 XY 校相前启动，重装波形或重新 arm Z 时保持连续输出。", "XY 校相未收敛或信号过低时停止测量，保存诊断。", "所选 AuxOut 按设备库安全规则检查偏置及预计总电压，缺少范围或越界时拒绝启动/写入。", "结束时仅关闭 XY 与 Z RF；时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲按实验要求保持运行；恢复温控为 5 V DC + ON，保留其余标准输出和锁相配置。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight_runner, derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run, analysis_runner=analyze_runner,
)
