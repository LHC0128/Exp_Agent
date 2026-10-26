---
title: Z 任意波线圈波形一致性验证
type: Z_AW_Waveform_Scope_Check
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: optimal_control_output
  Time_sequence_2:
    role: external_burst_trigger
  scope_waveform:
    role: CH3_coil_measurement_and_CH4_trigger_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - Time_sequence_2 CH2 需要接入 Z_magnetic_field CH1 Ext Trig，Z 任意波 Burst 使用外部下降沿。
  - SDS CH3 测量 Z 小线圈两端电压，CH4 接同一触发方波并使用下降沿 2.5 V 触发。
  - CH3/CH4 均配置为 1 MΩ、DC、1×；CH3 的量程按实测波形自动调整。
  - Time_sequence_2 显式使用 High-Z 负载标定，确保 SDS 高阻输入上实际为 0--5 V。
  - SDS 先确认 C4 下降沿触发，再等待完整记录窗；空帧或无沿帧会软复位并重试，最多三次。
  - 分析允许理论波形与 CH3 存在固定时间差，并保存最佳延迟和 CH4 触发时刻。
  - 正常、取消和异常结束将 Z CH1 归零并关闭，Time_sequence_2 CH2 保持当前状态。
experiment_id: z-aw-waveform-scope-check
defaults_file: params/experiments/z-aw-waveform-scope-check.yaml
---

# Z 任意波线圈波形一致性验证

当前默认参数：[参数文件](../../params/experiments/z-aw-waveform-scope-check.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `z-aw-waveform-scope-check`。运行页提供可勾选的硬件步骤和独立离线分析，完整运行与分步执行共用相同的业务函数。实验从
`CONTROL_RESULTS_ROOT/CONTROL_VERSION/` 读取最优控制波形，并从
`data/Mx_Z_Field_Calibration/<Z_CALIBRATION_SOURCE_RUN>/results/analysis.yaml`
读取 Z 频率标定，按照 Mx Z 最优控制实验的换算关系生成期望 Z 电压：

```text
V_Z(t) = CONTROL_SCALE * Omega_ctrl_Hz(t) / K_Z_Hz_per_V
```

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.z_aw_waveform_scope_check.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## 接线

- `Z_magnetic_field` CH1：Z 小线圈驱动，配置为外部下降沿触发的无限 Burst。
- `Time_sequence_2` CH2：High-Z 负载标定的共同触发方波，频率、幅度、偏置与占空比读取本次参数，接入 Z CH1 Ext Trig。
- SDS CH3：跨接 Z 小线圈两端，保存实际线圈电压。
- SDS CH4：接入共同触发方波，作为示波器 EDGE 下降沿触发源。

SDS CH3/CH4 使用 1 MΩ、DC、1×，CH4 触发电平为 2.5 V。运行前必须人工确认信号地和线圈接法不会造成短路或超出输入额定范围。

## 采集与分析

本实验是纯电学波形工具，属于 `lab_workflows/acquisition_policy.py` 的显式例外；不连接 TEC、温控开关、GS200、激光或锁相，也不加入气室采集的温控门控流程。连接复用当前设备映射和信号源负载策略，随后按设备库配置同步并严格回读参考时钟。Z 任意波与共同触发必须在同一台信号源的不同物理通道。

采集周期数和重复次数读取参数文件。SDS 先进入单次模式并确认 C4 已发生下降沿触发，再等待完整记录窗提交，随后保存 C3/C4 的完整原始时间轴、实际采样率、垂直量程和触发参考波形。空帧或不含下降沿的帧不会进入分析；工作流会软复位 SDS、重放当前采集配置并自动重试，最多尝试三次。

每帧保存的垂直量程和偏置对应实际采集该帧时的设置；即使最后一次自动调整后未再采集，也不会把新设置写入旧帧元数据。

离线分析从 CH4 检测下降沿，以周期相关搜索处理 CH3 与理论波形的固定时间差，并报告归一化形状相关系数、归一化 NRMSE、最佳延迟、仿射增益/偏置和峰峰值比例。实验不设置自动通过阈值，只保存指标和诊断图。

波形形状为常数、触发沿缺失或输入数据不合法时，分析阶段记录错误并阻止后继绘图，不以插值或阈值代替有效数据。饱和、贴边和幅值诊断保留在报告内，不混入形状评分。历史波形缺少量程信息时，对应未知指标使用 `null`，不伪造量程或饱和结论。

## 可执行步骤与参数编辑

步骤、依赖、参数展示归属和失效范围由 [stages.py](../../lab_workflows/experiment_modules/z_aw_waveform_scope_check/stages.py) 提供，GUI 不维护第二套执行顺序。

| 步骤 | 职责和前置 | 参数编辑与结果 |
| --- | --- | --- |
| A01 参数与接线预检 | 无前置；只读检查参数、控制源、标定、映射与安全范围 | 不连接硬件；重复预检不使已完成的硬件步骤失效 |
| A02 连接设备与同步时钟 | A01 完成；复用同机连接并回读参考时钟 | 连接 Z/共同触发信号源和 SDS；不修改其他设备 |
| A03 控制源与任意波触发配置 | A02 完成；读取版本化控制与 Z 标定，配置共同触发和 Z 外部下降沿无限 Burst | 编辑 `CONTROL_*`、`Z_CALIBRATION_SOURCE_RUN`、`Z_AW_OUTPUT_*` 和 `TRIGGER_*`；此时共同触发关闭 |
| A04 Scope 配置与触发就绪 | A03 完成；配置 CH3/CH4，回读实际采样率、长度、量程和触发通道设置 | 编辑 `SCOPE_CYCLES`；Scope 配置完成后开启共同触发 |
| A05 重复采集与原始帧保存 | A04 完成；每次执行创建独立目录并冻结本次源文件和参数 | 编辑 `RUN_TAG`、`SCOPE_REPEATS`；自动量程采集并逐帧保存 |
| A06 安全收尾 / 结束会话 | 无前置，选入批次时必须放最后 | 停止 SDS、Z 归零关闭、保持共同触发最后状态并断开连接 |
| B01 加载配置与原始帧 | 无分析前置；绑定明确的已结束采集目录 | 核对身份、原始格式、理论快照和每帧数据，保存输入清单 |
| B02 触发对齐与波形相似度 | B01 完成 | 计算延迟、形状、仿射增益/偏置、峰峰值比例及饱和诊断，保存对齐缓存 |
| B03 诊断绘图与报告导出 | B02 完成 | 使用 B02 缓存导出 `paper` 图和 JSON/YAML 报告，不重复搜索延迟 |

该实验的过程参数都在实际设置它们的节点编辑，没有需要跨节点重复编辑的共享过程参数。Scope 通道角色、输入阻抗、探头倍率、触发电平和自动量程协议仍是隐藏配置快照，不增加 GUI 编辑入口。

运行页顶部的保存参数与布局、派生预览、仅预检、完整运行及任务状态位于流程容器内。节点基础参数名称常显，展开节点编辑；高级参数可单独折叠，允许同时展开多个节点，基础/高级分类可保存并回读。派生预览读取当前控制版本的重复频率与预计记录时长，不连接仪器。

存活会话改参先形成草稿。修改 A03 参数预计使 A03–A05 失效，修改 Scope 设置使 A04–A05 失效，修改运行标签或重复次数只使 A05 失效；多参数修改取这些范围的并集。还原草稿会清除预览。必须先应用参数，再显式勾选重跑；保存默认值或预检都不会自动执行步骤，应用失败保留草稿。已结束会话的执行记录保留，之后编辑的参数用于下一次实验。

缺少前置的选择会被拒绝，不自动补跑。每次 A05 都创建新目录，同一秒重跑也使用独立后缀，旧 `raw/` 与参数快照不覆盖。Scope 单次模式或触发源被改变后，必须重新执行 A04。控制源在上传后发生变化时拒绝保存与新采集，要求重新配置波形。

分步等待期间连接与输出保持，任务持续占用全局硬件锁。空闲时使用“结束会话”，执行中使用任务取消。取消、进程通信结束和后台关闭均先执行收尾，再释放硬件锁。刷新页面可恢复存活会话，后台重启不会把磁盘记录当作仍连接的硬件。

## 运行 / 历史与独立分析

本实验清单显式声明 `run_step_id=A05`、`finish_step_id=A06`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

“运行”Tab 提供流程操作和最近结果；“历史”Tab 读取本次 `experiment_config.yaml` 参数快照、完成状态和产物，支持填回下一次实验参数及重新分析。填回不保存默认值，也不自动运行。

分析列可选择下一次实验或具体运行目录。本实验没有可调分析覆盖参数；下一次实验的完整运行自动使用相同的 B01–B03，具体目录的分析只读取该目录原始数据，不改写历史采集参数。保存默认值始终只保存下一次实验参数和统一分类。正在写入的目录拒绝分析，分步分析与历史重新分析共用同目录互斥。

`analysis_state.json` 记录分析版本、配置和原始文件内容指纹、阶段状态及产物指纹。输入或前置产物变化时不能复用过期结果。重算时只将受影响阶段及后继产物移入 `results/before_<时间戳>/`；单独重绘 B03 不移动仍在使用的 B01 输入清单或 B02 对齐缓存。旧数据没有阶段记录时从 B01 开始。

新增运行写入独立的 `raw_format_version=1`，参数 `schema_version` 的变化不自动改变格式资格。没有原始格式标记的历史数据只在既有实验 ID、`data_type` 和 `measurement_mode=coil_voltage` 都匹配时按已发布格式读取，不重写历史运行。失败或取消后已保存的完整帧可用于诊断，但缺少源快照或有效帧时分析会明确失败。

采集和分析入口分别为：

```text
experiments/Z_AW_Waveform_Scope_Check.py
experiments/Z_AW_Waveform_Scope_Check_plot.py
```

每次运行保存：

```text
data/Z_AW_Waveform_Scope_Check/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    scope_capture_000.npz ...
  results/
    aligned_waveforms.npz
    similarity_diagnostics.npz
    similarity_report.yaml
    similarity_report.json
    waveform_similarity.png
    waveform_similarity.pdf
    analysis_inputs.json
    similarity_metrics.json
    analysis_summary.json
  acquisition_state.json
  analysis_state.json
```

职责模块位于 [实验包](../../lab_workflows/experiment_modules/z_aw_waveform_scope_check/)：`preparation.py` 负责预检、源准备、连接与结束；`excitation.py` 负责波形与触发配置；`acquisition.py` 负责 Scope 协议、量程、采集和原始保存；`workflow.py` 只编排步骤；`analysis_steps.py` 保存原有科学算法与绘图，`analysis.py` 管理分析阶段、指纹与归档。旧 `run_scope_check` 和 Scope/对齐函数导入路径继续供实际电流和闭环实验复用，不复制采集算法。

可编辑操作原图：[Z 任意波线圈波形核验.excalidraw](../../lab_workflows/experiment_modules/z_aw_waveform_scope_check/Z任意波线圈波形核验.excalidraw)，运行页提供下载入口。

## 安全结束

正常、取消或异常结束时，程序停止 SDS，关闭 Z CH1 Burst 并写入 DC 0 V、Output OFF；共同触发 CH2 保持当前状态，然后断开 DG4000 和 SDS。

首次连接或同步时钟失败且尚未配置输出时只释放已建立的连接；已有输出后再次同步失败仍执行输出收尾。关闭输出或停止 Scope 失败会记录为收尾失败，不宣称安全完成；此前采集异常仍作为原始异常保留，已经保存的帧不删除。所有自动化回归使用假设备，不连接真实实验硬件。
