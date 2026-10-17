"""独立注册入口，硬件需求与 DirectAW 共用。"""
from dataclasses import replace
from functools import partial
from ...experiments.typed import TypedWorkflowAdapter
from ..rf_sensitivity_direct_aw import definition as shared
from .models import RFSensitivityDirectAWRParams
from .run_format import EXPERIMENT_ID, DATA_TYPE, analysis_block_reason

ADAPTER = TypedWorkflowAdapter(EXPERIMENT_ID, DATA_TYPE, RFSensitivityDirectAWRParams,
    "lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.workflow",
    "lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.analysis")
analyze_runner = partial(shared.analyze_runner, adapter=ADAPTER, block_reason=analysis_block_reason)
comparison_runner = partial(shared.comparison_runner, adapter=ADAPTER,
    module="lab_workflows.experiment_modules.rf_sensitivity_direct_aw_r.comparison")
preflight_runner = partial(shared.preflight_runner, adapter=ADAPTER)

DEFINITION = replace(shared.DEFINITION,
    id=EXPERIMENT_ID, title="RF 灵敏度（DirectAW R）", variant="direct-aw-r", data_type=DATA_TYPE,
    description=('复用 DirectAW 准备与 XY 校相，保持共同触发和 AW 整周期，跳过 A11。A10 在响应频率同步设置 RF / RF 解调器，A13 扫描有符号 RF 幅度并以 R 的绝对色散拟合底层中心斜率标定；A12 切至独立校相/噪声频率，A14 关闭 Z RF 采集 X 噪声。拟合失败以最大数值梯度估算并关闭线宽补偿；线宽补偿固定为色散等效，不支持扫频线宽模式。运行/历史双 Tab 内提供 15 个采集节点和 4 个独立分析节点，节点展示实际质量门槛并编辑参数、共享分类和值，应用草稿前预览依赖失效；具体运行目录的分析覆盖值独立保留，保存默认值仅保存下一次实验设置。A15 收尾本轮、A16 结束设备会话。独立历史支持多次 R 标定 / X 噪声对比、英文图例与 PNG/PDF 导出。两个 RF 频率的增益一致性及等效线宽补偿待实测验证。HF2/MFLI 可选择辅助通道与 RF 解调器索引，MFLI 另支持内部 AuxOut；所选 AuxOut 使用已确认的 ±10 V 安全规则，缺范围或预计越界禁止启动/写入。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。 解调时序复用设备库自动／DAQ／Scope：HF2 自动用 DAQ，MFLI 请求 >70 kSa/s 自动用 Scope；频谱按最终实际率计算。' + ' 配置完成后关闭温控并按 SYSTEM_SETTLE_S 稳定，采集至内存，恢复后保存；TEC 可连接时仅设 UI 目标温度。 响应与 XY 校相的同名 NPZ 快照以同目录临时文件原子替换；写盘中断保留上一完整快照并清理临时文件，一次性噪声帧沿用原逐帧保存策略。'),
    acquisition_program="experiments/RF_Field_Sensitivity_AW_R.py",
    analysis_program="experiments/RF_Field_Sensitivity_AW_R_plot.py",
    safety_notes=shared.DEFINITION.safety_notes + ("流程节点展示实际预检、时钟、校相、辅助输出、有效数据与安全收尾门槛；R 拟合失败的有效数值斜率仍可导出估算诊断。",),
    schema_provider=ADAPTER.schema, defaults_saver=ADAPTER.save_defaults,
    preflight_runner=preflight_runner, derive_runner=ADAPTER.derive,
    experiment_runner=ADAPTER.run, analysis_runner=analyze_runner,
)
