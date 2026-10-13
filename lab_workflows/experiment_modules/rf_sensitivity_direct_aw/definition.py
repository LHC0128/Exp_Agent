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

ADAPTER = TypedWorkflowAdapter(EXPERIMENT_ID, DATA_TYPE, RFSensitivityDirectAWParams, "lab_workflows.experiment_modules.rf_sensitivity_direct_aw.workflow", "lab_workflows.experiment_modules.rf_sensitivity_direct_aw.analysis")


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
    description='XY 直接任意波控制下扫描有符号 Z RF 幅度，最优控制波形从 theory_agent 的 oc_sens 版本目录选择；相位与幅度扫描每点以定长 DAQ 采集、按温控分组点数分组关温控，以 Demod3 X 响应斜率和 X 噪声 PSD 测量 RF 灵敏度，并用响应色散拟合得到的等效 HWHM 进行洛伦兹频响补偿。XYZ 共用时序信号2主输出触发，HF2 经 Demod0 Y → AuxOut2 → Signal Input2 → Demod3 回环；初始化含四级相位校准：Bell-Bloom（Demod0）、控制载波-Pump（XY DirectAW）、控制 AM 调制-RF 场（扫描 Z RF 触发相位，按温控分组恢复温控）与 Demod3。运行页采用 16 个采集节点、4 个独立分析节点的可执行流程图，按节点编辑参数，共享值与分类同步，草稿先预览重跑范围再应用；分步等待保持设备会话与硬件锁，校相质量失败保存诊断并允许重试。A15 完成本轮安全收尾、A16 结束会话；具体目录的分析覆盖值独立保留，保存默认值只保存下一次实验设置。历史支持多次 X 响应与灵敏度谱对比、自定义英文图例及 PNG/PDF 导出。仪器设置只回读记录，DAQ 使用实际采样率。AuxOut2 使用已确认的 ±10 V 安全规则，缺范围或预计越界禁止启动/写入。当前双主电压回采接线只支持 HF2，选择 MFLI 时预检禁止启动。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。',
    data_type=DATA_TYPE, execution_mode="typed_workflow", schema_version=2,
    required_devices=("GS200", "DG900", "DG4000", "HF2"),
    required_mapping_keys=RF_SENSITIVITY_DIRECT_AW,
    acquisition_program="experiments/RF_Field_Sensitivity_AW.py",
    analysis_program="experiments/RF_Field_Sensitivity_AW_plot.py",
    wiring_notes=("Time_sequence_2 主输出分路连接 X/Y DirectAW 与 Z RF 的 Ext Trig，通道 SYNC 同步开启并在收尾后保持；AuxOut2 经 BNC 接 Signal Input2。",),
    safety_notes=("共同触发在 XY 校相前启动，重装波形或重新 arm Z 时保持连续输出。", "XY 校相未收敛或信号过低时停止测量，保存诊断。", "AuxOut2 按设备库安全规则检查偏置及预计总电压，缺少范围或越界时拒绝启动/写入。", "结束时仅关闭 XY 与 Z RF；时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲按实验要求保持运行；恢复温控为 5 V DC + ON，保留其余标准输出和 HF2 配置。"),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight_runner, derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run, analysis_runner=analyze_runner,
)
