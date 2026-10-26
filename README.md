# 实验自动化平台

面向 Bell-Bloom 磁力仪实验的自动化仓库，覆盖仪器控制、参数扫描、原始数据采集、离线分析与绘图。仓库同时保留交互式 Notebook 和可重复运行的 Python 脚本，适合实验调试、批量采集和后处理复现。

## 初次安装

适用于第一次从 GitHub 获取本仓库并部署到实验电脑的场景。建议用 `git clone`
而不是下载 ZIP：导航“检查更新”页的 Git 仓库检测和实验验证器都依赖 Git 检出，
ZIP 也能启动 GUI，但更新与自动验证功能不完整。

- Python 要求：`>=3.10`；本地包名：`exp-agent`；虚拟环境：`agent_exp_env\`
- GUI 前端另需 Node.js / npm，当前 Vite 要求 Node.js `^20.19.0 || >=22.12.0`，
  升级依赖后以锁文件中的 `engines` 为准

希望一条命令完成，或由 AI 编码代理代为安装时，直接运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
```

脚本幂等、可重复运行，自动完成下述全部步骤，任一步失败即停止并报告，不自动降级；
`-SkipFrontend` 可跳过前端依赖。让 AI 代理安装时，可让它运行本脚本并原样转述输出，
不要在未告知的情况下改动依赖版本或跳过失败的步骤。

手动逐步安装如下（与脚本等价）；在仓库根目录按顺序执行，已有虚拟环境时跳过创建直接复用：

```powershell
# 1. 虚拟环境、本地包与仪器通信/分析依赖
if (-not (Test-Path .\agent_exp_env\Scripts\python.exe)) {
    python -m venv agent_exp_env
}
.\agent_exp_env\Scripts\python.exe -m pip install -e .
.\agent_exp_env\Scripts\python.exe -m pip install pyvisa pyvisa-py pyserial zhinst numpy scipy matplotlib pyyaml jupyter

# 2. GUI 后端与前端依赖
.\agent_exp_env\Scripts\python.exe -m pip install -r GUI\requirements.txt
.\agent_exp_env\Scripts\python.exe -m pip check
Set-Location GUI\frontend
npm ci
Set-Location ..\..
```

`pip install -e .` 安装 `src/` 下的驱动和根目录的 `lab_workflows`，同时按
`pyproject.toml` 安装 TOPTICA SDK 与进度条；仪器通信、分析和 GUI 依赖需按上面分组安装。
根目录 `requirements.txt` 是历史环境快照，不替代上述说明。

安装完成后双击根目录 **`start_gui.bat`** 启动 GUI，详见
[GUI 基础使用](#gui-基础使用)。没有连接实验仪器时 GUI 也能正常启动，可浏览界面与
历史数据；运行正式实验前需先在 GUI 中完成设备库与物理量映射配置。

需要导出当前环境时：

```powershell
.\agent_exp_env\Scripts\python.exe -m pip freeze > requirements.txt
```

保存前将本项目的可编辑安装条目规范为 `-e .`，移除机器路径和带凭据的地址。

## 近期更新（2026 年 10 月）

- 新增 `setup.ps1` 一键安装脚本，README 顶部集中「初次安装」说明：幂等完成虚拟环境、本地包、仪器/分析与 GUI 前后端依赖，人类与 AI 编码代理均可直接运行；任一步失败即停止报告，不自动降级。

- [XY 已知可控噪声谱](docs/experiments/noise_spectrum_xy_ctrl_known_noise.md)提供采集/分析两列交互流程：节点编辑、依赖检查、分步会话和重跑；K_Z 手动输入（Hz/V）并保存为本轮快照，增益使用静磁场 v3 历史曲线，未选曲线时完成谱分离并等待标定，保留原脚本和历史数据兼容。
- [XY 已知不可控 Probe AM 噪声谱](docs/experiments/noise_spectrum_xy_ctrl_uncontrolled_probe_am.md)同样提供分步流程图：A08 专属载波节点、A12 双噪声上传、A14 配对扫描可选 Z 持续注入；分析 B01–B04 独立验收背景与可控响应，移动峰不足时按运行 K/B 输出暂定轴并标记质量未通过。
- GUI 支持通过根目录 `start_gui.bat` 双击启动；静磁场与 RF DirectAW 提供可执行交互流程图。静磁场节点支持参数编辑、勾选与依赖检查、分步会话和独立阶段分析，保留逐点 DC 连续波形中位数与分组温控。
- [Mx Z 最优控制 RF 灵敏度](docs/experiments/mx_z_optimal_control_rf_sensitivity.md)支持运行/历史双 Tab、节点参数与分步会话，复用原正交校相、R 灵敏度和零 RF 剩磁模式；结束恢复 GS200 原态并保持 Z 控制/共同触发，历史分析无额外覆盖参数。
- [Mx Z 最优控制 XY 补偿偏置 RF 灵敏度](docs/experiments/mx_z_optimal_control_xy_rf_sensitivity.md)提供 7 个采集/3 个分析节点，保留理论/闭环双来源、中心一次校相及蛇形网格；历史展示原热图与逐点诊断，最佳点只写结果。
- [Mx Z 最优控制 XY 噪声谱](docs/experiments/mx_z_optimal_control_xy_noise_spectrum.md)提供 7 个采集/3 个分析节点，保留 RF-off 蛇形 DC 网格与原 Welch；历史目录支持独立统计频段覆盖及噪声热图。
- [Mx Z 最优控制 XY 泄露响应](docs/experiments/mx_z_optimal_control_xy_leakage_response.md)提供 7 个采集/3 个分析节点，保留同形带符号任意波蛇形扫描、R 质量重试与实测最小点；结束保持 Z/XY 和共同触发，历史目录独立分析且无额外覆盖值。
- [Mx Z 最优控制 XY RF 相位响应](docs/experiments/mx_z_optimal_control_xy_rf_phase_response.md)提供 6 个采集/3 个分析节点，保留蛇形 DC 网格、180° 配对相位、质量重试与非线性色散拟合，不选最佳 XY；历史拟合门槛独立覆盖，结束保持 Z/共同触发并将 X/Y 归零关闭。
- RF 灵敏度（DirectAW）历史 Tab 试点支持星标与筛选、保留时间前缀的目录重命名和确认后永久删除整次数据；已有对比导出保留为生成时的快照。
- [锁相驱动](src/lockin_amplifier/README.md)拆分为 HF2、MFLI 独立型号模块，共用通信、解调和采集接口；设备库统一设置自动／DAQ／Scope。自动模式下 MFLI 请求 >70 kSa/s 使用解调 Scope，HF2 使用 DAQ；预检检查选件与单段内存，分析使用最终实际率。
- [信号发生器驱动](src/signal_generator/README.md)拆分为 DG4000、DG900 Pro 独立实现，保留旧导入入口；共同接口覆盖波形、调制、Burst、计数器和截图，Pro 额外支持内置任意波文件。
- 设备库统一管理 USB / 网络 / LabOne 发现与扫描候选地址，设备选择按同机和共享通道约束联动；配置先编辑草稿，再整批保存，同次实验的独立物理量占用同一通道时拒绝启动。
- “参考时钟同步”先写入全部设备，再统一回读；“输出负载初始化”单独设置 Pump 调制 50 Ω、其余高阻。高阻回读标记统一显示和提交为 `INF`，两项工具分别报告每台设备结果。
- AI 协作说明（`AGENTS.md`、`CLAUDE.md`、`GUI/AGENTS.md`）与 `.agents/`、`.claude/`、`.codex/`、`.vscode/` 等本地配置退出 GitHub 同步，仅本地维护；仓库继续同步 GUI 实验系统等面向使用者的内容。

## 坐标系

| 轴 | 物理量 | 说明 |
|----|--------|------|
| **Z** | 主磁场 $B$ | 由实验指定的 GS200 或 Keithley 6221 独占控制，对应 Larmor 频率主轴 |
| **X** | 光传播方向 | Pump / Probe 光沿 X 传播 |
| **Y** | RF / 横向控制方向 | 用于横向自旋激发、补偿场和调制控制 |

## 仓库结构

```text
src/
  gs200/                  # GS200 控制
  keithley_6221/          # Keithley 6221 电流源与内部波形控制
  lockin_amplifier/       # HF2 / MFLI 控制、DAQ 与内置 Scope
  sds_acquisition/        # SDS 示波器采集
  sensitivity_analysis/   # 灵敏度拟合、汇总与分析
  signal_generator/       # DG4000 / DG900 控制
  tec_controller/         # TEC103 控制
  toptica_laser/          # TOPTICA DLC pro Probe 激光控制

experiments/
  *.ipynb                 # 交互式实验 Notebook
  *.py                    # 采集脚本
  *_plot.py               # 分析与绘图脚本
  control_waveform*.csv   # 任意波 / 控制包络

examples/                 # 设备 Notebook 与无硬件科研绘图示例
docs/                     # 分类文档与公共规范
  README.md               # 文档导航
  experiments/            # 各实验的操作、采集与离线分析手册
  instruments/            # 仪器驱动、接线与编程补充说明
  manuals/                # 厂商说明书、提取文本与厂家示例（仅本地）
  old/                    # 历史归档，不作为当前入口
params/                   # 设备库、物理量映射与安全限值
  devices.yaml            # 设备 ID、型号、资源与参考时钟
  instrument_discovery.yaml # 旧网络扫描候选兼容入口
  mapping.yaml            # 物理量与设备端点绑定
  safety_limits.yaml      # 输出量安全上下限
  experiment_catalog.yaml # GUI 分类、归档状态、名称、简介与参数布局
  experiments/            # 正式实验的版本化默认参数
lab_workflows/            # GUI 与命令行共用的实验契约、参数模型和安全步骤
  analysis/               # 跨实验共享的离线分析
  experiment_modules/     # 新模式实验的模型、采集工作流与离线分析器
  experiments/            # 注册表、执行契约与新旧适配器
  plotting/               # 论文图（默认）与 A0 海报共享绘图规范
  steps/                  # 设备会话、输出策略、校相、温控与安全收尾
  workflow_state.py       # 工作流状态 JSON 的公共原子读写
  workflow_session.py     # 公共串行会话命令循环
  workflow_stages.py      # 公共阶段类型、初始状态与显式依赖规则
  analysis_execution.py   # 分析状态、内容指纹与声明产物归档
GUI/                      # 本地 Web 实验控制台
  backend/                # FastAPI API、任务队列与 SSE
  frontend/               # React + TypeScript + Vite
  start.ps1               # 后端与前端开发服务启动器
start_gui.bat             # Windows 双击启动前后端并打开浏览器
data/                     # 原始实验数据
results/                  # 结果图表
old/experiments/          # 已归档实验的代码、正式测试与恢复说明
总结/                     # 阶段性总结
```

工作流状态使用 `lab_workflows.workflow_state.read_state(path)` 和
`write_state(path, payload)`；原 XY `state_io.py` 仅保留导入兼容，状态格式和写盘时机不变。
公共串行会话使用 `lab_workflows.workflow_session.serve(ctx, stream, *, stages, validate, finish_id)`；
XY 与 Detection Chain 原会话入口保留默认参数和命令行启动，各实验的阶段、校验与结束策略仍由自身提供。
阶段基础类型与初始状态使用 `lab_workflows.workflow_stages.Stage`、`initial_states(stages)`；
公共 `DependencyRules` 计算依赖、参数失效与选择校验，各实验显式提供自己的 `finish_id` 和阶段清单，
不借用其他实验闭包里的结束节点；原阶段模块的函数和导入保持兼容。
闭环波形分析组合 `lab_workflows.analysis_execution.AnalysisRunState`，由公共模块完整拥有分析状态、
内容指纹和按声明归档的产物；额外输入、数据资格、执行顺序、异常分类和科学算法仍由实验自己负责。
其他分析家族逐步迁移，不将现有跨实验继承视为推荐入口，也不引入万能 Context 或统一实验执行循环。
[MxZ 家族初始化](<lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/workflow.py>)通过显式预检、受限身份元数据与不可变 `StageModePolicy` 提供扩展点；XY 噪声子实验调用完整父构造，不再自行拼装父字段。其余子实验与采集内部状态继续按真实能力逐步迁移。
[DirectAW 子控制运行](<lab_workflows/experiment_modules/rf_sensitivity_direct_aw/workflow.py>)由 `create_control_run()` 统一创建：设备仅借用，校准和阶段状态独立复制，目录与有效前置继承沿用原规则。DirectAW、已知噪声和倍率扫描各自声明格式元数据；倍率扫描仍保持逐点独立校准，工厂不接管采集或安全收尾。
[XY 锁相采集模式校验](<lab_workflows/steps/lockin.py>)仅接收仪器和显式速率、时间常数、相位；三个实验在原采集入口调用，DAQ/Scope 差异、容差与错误语义集中维护，旧 `verify_daq_mode(ctx)` 仅保留兼容入口，不作为新调用范式。
[Z 线圈采集配置](<lab_workflows/experiment_modules/z_coil_inductance_frequency_response/workflow.py>)通过 `configure_capture()` 一次接收保护等待、格式元数据和校验器；无效输入在修改配置前拒绝，电流子实验不再逐项改写父采集字段，逐帧配置、保存和恢复顺序不变。
[Scope 波形准备](<lab_workflows/experiment_modules/z_aw_waveform_scope_check/workflow.py>)通过 `prepare_control_source()` 返回完整 `ControlSource`；电流子实验只提供自己的准备结果，父 A03 保持“提交源 → 标记输出已开始 → 设置仪器”的顺序。该结果是已校验的准备输入，不冒充实际仪器回读；采集、失败收尾与部分数据保存仍由原流程负责。
[静态架构约束](<lab_workflows/test_experiment_architecture.py>)从注册表枚举全部当前实验模块，冻结剩余跨实验执行类继承、构造状态重建和可静态追踪的外部状态写入，禁止新增或增加写入次数；纯数值复用不受限。测试不导入实验模块，也不会自动刷新允许清单；动态分发、容器传播和反射仍需人工审查。五个采集簇本轮各迁移一个能力，不代表全部历史继承和内部状态耦合已经消除。
最优控制 Y RF 的包络校验、Burst 恢复与重触发共用 `lab_workflows.steps.mx_z_optimal_control`，旧私有入口保留兼容；扫描策略和采集时序不迁移。
公共 `validate_safety_limit()` 先拒绝 NaN／±Infinity，再按原限值校验有限数值；高阻负载 `INF` 仍走独立归一化入口。
直接调用 `restore_y_rf_burst_sine()` 仍以调用方已完成包络校验为前提，返回时输出关闭。
前端分步流程图共用 `GUI/frontend/src/components/workflow/WorkflowPanel.tsx` 及配套样式；原 XY 路径保留兼容转发。
`pages/WorkflowExperimentPage.tsx` 继续编排运行／历史外壳，专属页面的参数归一化、分析键映射和结果扩展保持原接口。
运行／结束角色由各实验流程清单显式提供，不套用通用节点编号；旧服务缺字段时阻止新分步执行，仍可恢复、取消和安全结束。
状态与产物以执行记录为准，执行完成不等于科学质量通过；具体兼容、草稿与下载规则见 [GUI 分步契约](GUI/README.md#分步流程契约与兼容)。

文档分类入口见 [docs/README.md](docs/README.md)。原根目录 `manuals/` 已合并至
`docs/manuals/`，厂商原始资料与项目内的仪器使用说明分开存放。

## 核心模块

| 模块 | 设备 / 能力 | 说明 |
|---|---|---|
| `sds_acquisition` | SDS 系列示波器 | 波形采集、保存、CLI 支持 |
| `signal_generator` | DG4000 / DG900 Pro | 共同波形、Burst、AM、计数器与截图；Pro 内置任意波文件 |
| `lockin_amplifier` | Zurich Instruments HF2 / MFLI | 统一解调、Signal / Aux 输出、DAQ 与内置 Scope |
| `gs200` | Yokogawa GS200 | 主磁场电流源控制 |
| `keithley_6221` | Keithley 6221 | 精密直流、内部波形与易失性任意波控制 |
| `tec_controller` | TEC103 | 温控器串口控制 |
| `toptica_laser` | TOPTICA DLC pro | Probe 激光电流、温度、PZT 与扫描控制 |
| `sensitivity_analysis` | 分析工具 | 拟合、灵敏度计算、结果汇总 |

常规正式实验将 TEC103 视为可选控制设备：若设备库映射串口已被 TEC 桌面软件占用，
实验会警告并跳过设温与稳定等待，其他采集流程继续运行。此时请在外部软件中
确认温度；TEC/PID 专项实验仍要求独占串口。详见
[`docs/instruments/tec_controller.md`](docs/instruments/tec_controller.md)。

相关说明可见：

- `docs/instruments/sds_acquisition.md`
- `docs/instruments/signal_generator.md`
- [信号发生器源码阅读指南](src/signal_generator/README.md)
- `docs/instruments/lockin_amplifier.md`
- [锁相放大器源码阅读指南](src/lockin_amplifier/README.md)
- `docs/instruments/gs200.md`
- `docs/instruments/keithley_6221.md`
- `docs/instruments/tec_controller.md`
- `docs/instruments/toptica_dlc_pro.md`

## GUI 基础使用

GUI 是运行在实验电脑本机的 Web 控制台，可用于读取和设置 GS200、Keithley 6221 电流源、
TOPTICA DLC pro Probe 激光、DG4000、DG900 Pro、HF2 / MFLI、SDS 示波器等仪器，并运行仓库中
已接入的实验流程。示波器采集位于“实验中心 → 示波器采集”，详见
[示波器采集](docs/experiments/scope_capture.md)。

实验页面仅保留「运行 / 历史」主 Tab；运行页的可执行交互流程图与可编辑原图下载继续使用。
总览页和实验页的静态架构图已撤下，原 HTML/JSON 与验收附件见[架构图归档](docs/old/architecture/README.md)。

### 首次安装

按顶部[初次安装](#初次安装)完成虚拟环境、本地包与 GUI 前后端依赖。
开发模式直接使用前端源码，支持 HMR，无需先运行 `npm run build`。

### 启动 GUI

完成首次安装后，双击仓库根目录的 **`start_gui.bat`**，即可配置 Tailscale 私人 HTTPS
入口、启动前后端，并在 Vite 就绪后自动打开默认浏览器。也可在仓库根目录运行
`.\GUI\start.ps1`（默认不自动打开浏览器，附加 `-OpenBrowser` 可开启；仅本机运行、
无需 Tailscale 时加 `-LocalOnly`）。

启动成功后，浏览器访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)；Vite 在当前窗口
运行，将 `/api` 请求代理到独立窗口中的 FastAPI 后端
[http://127.0.0.1:8000](http://127.0.0.1:8000)，两个服务只监听本机地址。笔记本安装
Tailscale 并加入同一网络后，可用启动窗口显示的 `https://<本机的 Tailscale 域名>` 远程
访问，局域网和外网使用同一地址；具体设置与手动命令见
[GUI 远程访问说明](GUI/README.md#笔记本浏览器访问)。

启动器检查 Python、npm、前端依赖、Tailscale 登录状态和 8000 / 5173 端口；缺失依赖或
端口冲突时报错退出，不杀进程、不自动换端口、不自动安装依赖。按 Ctrl+C 只结束当前
Vite 进程，关闭或刷新网页也不会停止后台实验。停止后端时应在其独立窗口按 Ctrl+C 并等待：
后台停止接纳新任务和短硬件操作，取消排队项，向运行任务请求协作停止，等待现有安全收尾、
部分数据保存和已开始的短操作结束；重复 Ctrl+C 不强退，等待较长时持续报告状态。
修改 Python 后端代码后可在总览页点击“重启后端”（仅在没有运行或排队任务时可用）。
停止服务前仍应优先通过 GUI 正常停止实验或功能模块；退出等待不代表所有仪器输出已确认安全，
强杀进程、直接关闭终端和断电不在保证范围。完整边界见
[正常停止与安全边界](GUI/README.md#正常停止与安全边界)；单独启动前后端、构建 dist 部署等见
[GUI 开发说明](GUI/README.md#启动)。

### 基本操作

仪器选择与日常操作（物理量映射、设备库维护、设备控制读写与安全限值检查、Z 任意波
控制的加载与停止顺序）的完整步骤见 [GUI 使用说明](GUI/README.md#基本操作)。要点：

- 设备选择在“物理量映射”与“设备库”中统一完成：切换联动同机/共享通道组并保留通道，
  目标通道不足时拒绝；均为草稿，点击保存后整批生效。信号源通道允许跨实验复用，
  同一次实验独立使用的物理量占用同一通道时预检拒绝启动。
- 设备写入前会检查当前 mapping key 在 `params/safety_limits.yaml` 的上下限，写入后以
  实际回读值更新页面。
- 实验结束后先执行模块的“停止”，再分别停止 Vite 与后端；后端退出沿用各任务的输出保持
  策略，不替代已启动任意波等独立功能的停止操作，也不代表所有输出已关闭。

“实验中心”默认展示[注册表](lab_workflows/experiments/registry.py)中的未归档正式实验，提供动态参数、
默认值保存、无副作用预检、运行日志、安全停止和离线重新分析。实验卡片仅显示名称、分类和任务状态，
整卡可进入实验；介绍与采集／分析程序路径统一放在实验页的“实验说明”中。左侧导航可隐藏，
内容与卡片随窗口自适应，顶部任务条可跳转对应实验；未保存的实验草稿离开前需确认，断连时明确提示状态未知。
详见 [GUI 布局与任务导航](GUI/README.md#布局与任务导航)。`*.ipynb` 不进入实验中心，`*_plot.py` 只作为
对应实验的分析器。已归档实验只保留不可进入的目录卡片；专属代码与测试保存在
[`old/experiments/`](old/experiments/README.md)，共享依赖、参数及历史数据保持原位。
任意波示波器检查（`aw-waveform-scope-check`）已按原字节归档至
[`AW_Control_Waveform_Scope_Check.py`](old/experiments/experiments/AW_Control_Waveform_Scope_Check.py)，不再迁移；共享波形 CSV 与历史数据保持原位。
全局与局部静磁场优化、恒定 Omega 检查、XY DC／AW 电压标定已归档，源码、正式回归、原图及恢复边界见[归档清单](old/experiments/README.md)。局部优化不再提供 GUI 运行／历史入口，与主实验共用的历史数据和默认参数保持原位；静磁场主实验和 XY DirectAW DC 标定继续维护。
代码已移走的实验需手动接回代码与注册入口，不能一键恢复。仅目录归档的实验仍可在
“管理分类与实验信息”中恢复，状态保存在 `params/experiment_catalog.yaml`；完整清单与执行模式见
[实验注册表](lab_workflows/experiments/registry.py)。环境更新集中在导航“检查更新”页，
详见 [GUI/README.md](GUI/README.md#检查更新)；更详细的开发说明见 [GUI/README.md](GUI/README.md)。

锁相设备采用统一的 Python Core API 函数入口，支持相位、频率、输出设置、解调 X/Y/R 和
内置 Scope 采集。仍要求双主电压输入的实验选择 MFLI 时禁止启动；尚未确认安全电压范围的
锁相输出写入保持禁用。接口、硬件差异与实机验收见
[锁相模块说明](docs/instruments/lockin_amplifier.md)，受限实验清单见
[锁相实验兼容性](docs/legacy_lockin_experiments.md)。

## 实验组织方式

GUI 与命令行薄入口共同调用 `lab_workflows/`：

```text
GUI ───────────┐
               ├─→ 实验注册表 → 共享实验步骤 → src 仪器驱动
experiments ───┘
```

以后修改实验步骤，应修改共享工作流；新模式的 `experiments/*.py` 只保留命令行调用。
新增或修改实验时使用仓库级 `expcodegen` Skill，并运行其中的验证器。
后续修改改变实验行为时，同步更新 `docs/` 中的实验介绍、相关正文和 GUI 简介；新增正式实验默认配套
「运行 / 历史」双 Tab GUI，模板见 `.agents/skills/expcodegen/references/gui-two-tab-template.md`。
代码始终按准备、采集、分析等职责模块化，入口负责编排。新建或整体重构 GUI 时先询问是否
构建可勾选、带依赖的交互流程图；选择需要时按 Skill 的 `gui-workflow-template.md` 实现
分步会话、状态、重跑和安全结束，不需要时仍保留模块化代码及运行/历史界面。
测试完成后清理本次临时测试脚本，保留正式回归测试。
实验说明只链接 GUI 实际保存的默认参数文件，不重复记录可变默认值；历史运行参数以
各自的 `experiment_config.yaml` 为准。新增实验使用 Skill 验证器的 `--delivery` 模式，
检查文档关联并执行前端路由和历史接口测试。
采集修改的回归通过 `ACQUISITION_CASES` 关联稳定实验 ID 和真实业务场景；已有实验也检查
六步时序及故障覆盖。脚手架支持 `--acquisition-mode single|grouped|paired`，复用公共采集
窗口与延迟保存。参数 schema 和原始格式独立版本化，具体约定见 Skill 的实验契约参考。
`.agents/skills/expcodegen` 是维护源，使用其中的 `scripts/sync_skill.py` 同步 `.claude`；
两套 Skill 保持本地忽略，共享验收逻辑与正式测试位于 `lab_workflows/experiment_validation.py`
及 `lab_workflows/test_experiment_validation.py`。

正式实验有两种明确执行模式：

- `typed_workflow`：参数字段由 `ExperimentParams` 子类显式声明，模型内部使用
  `snake_case`；GUI/YAML 通过 `external_name` 继续使用稳定的大写键或
  `FIXED_PARAMS.*` 旧键。新增实验和迁移实验都采用此模式。
- `legacy_script`：过渡期兼容模式，仍从旧脚本常量生成参数并通过隔离适配器执行。
  它不会被当作新实验模板。

各实验的执行模式以注册表为准。GUI 参数表单只展示新模式模型显式声明且
`visible=True` 的字段，不会因为工作流中新增一个全大写运行时常量而增加表单项目。

所有正式实验连接锁相后调用 `initialize_lockin_inputs()`，关闭全部主电压输入（含 Input2 回环）的
50 Ω 终端和 AC 耦合；后续配置保持高阻、DC 耦合。旧参数键保留兼容并归一到关闭值，GUI 隐藏开启选项。

`rf-sensitivity-aw` 使用 schema version 3 的 DirectAW 工作流，兼容已验证的 version 2 运行，保留
`RF_Field_Sensitivity_AW` 数据目录与原脚本入口。以 X 响应斜率和 X 噪声 PSD 计算灵敏度，
线宽补偿可选；最优控制波形支持多选，逐控制独立校相、采集与分析，共用本轮 RF 参数且不自动改频；可按控制名称保存手动 XY 包络补偿系数（未设为 1）。GUI 提供运行 / 历史双 Tab
和可勾选、带依赖的分步流程图。默认值见
[参数文件](params/experiments/rf-sensitivity-aw.yaml)，接线、分步流程、收尾及分析见
[实验说明](docs/experiments/rf_field_measurement.md)。

`rf-sensitivity-aw-r`（RF 灵敏度（DirectAW R））复用上述准备、采集及安全收尾，先采
RF 解调器 R 再独立改频校相；R 标定失败时以数值梯度估算并关闭线宽补偿。使用独立默认
文件与 `RF_Field_Sensitivity_AW_R` 历史。两个 DirectAW 实验的所选辅助输出均按逐设备、
逐通道已确认的 ±10 V 规则检查，缺范围或预计越界时禁止启动/写入。详见
[实验说明](docs/experiments/rf_field_measurement_r.md) 与 [默认参数](params/experiments/rf-sensitivity-aw-r.yaml)。

DirectAW 与已知噪声实验共用可关闭的保守 MAD 异常谱筛选，每个控制独立识别整条高噪声记录，保留原始数据、全部 PSD、未剔除结果与清单。

`rf-sensitivity-aw-known-noise` 在固定 Z 周期伪噪声 ON 下支持单选或多选同 RF 设计频率的
控制版本，各自可保存手动 XY 包络补偿系数（未设为 1，不影响 Z RF/噪声），逐版本独立校相、标定、采集和离线灵敏度谱分析；A11 可跳过 RF 相位扫描，全部版本共用一个手动 RF 相位，不改变注入噪声相位。见
[实验说明](docs/experiments/rf_field_measurement_known_noise.md)与
[默认参数](params/experiments/rf-sensitivity-aw-known-noise.yaml)。

`rf-control-scale-sensitivity` 支持勾选多个控制，以起点/终点/点数扫描整体包络倍率，可降序、含零和负数、不强制含 1；各控制可手动设置独立补偿倍率（未设为 1），图横轴仍为扫描倍率，无倍率 1 时跳过归一化；各点独立校相，RF 相位可逐点扫描或在 A13 关闭扫描后共用一个手动设定，其余校相仍逐点执行，
逐点采集 RF 幅度响应、可选零 RF X 噪声和必测 R 扫频；不注入人为噪声、不自动追峰。
运行/历史双 Tab 提供可执行流程图；沿用 DirectAW 色散模型，比较绝对斜率、色散等效线宽、
洛伦兹中心频率及公共基带灵敏度中位数，图例直接使用所选控制波形名称。
新 v2 原始格式逐点保存校相结果，历史 v1 仍按固定标称相位识别，不混用两种测量条件。
支持保守的单条高噪声筛选并保留未剔除结果；独立只读诊断程序区分整组偏差与少数异常拉高平均 PSD。
见[实验说明](docs/experiments/rf_control_scale_sensitivity.md)与[默认参数](params/experiments/rf-control-scale-sensitivity.yaml)。

`t2-calibration`、`rf-sensitivity-direct-aw-frequency` 和 `xy-direct-aw-dc-calibration` 均提供
运行／历史双 Tab、7 个采集节点和 2 个独立分析节点；节点参数编辑、勾选与完整运行调用同一套
真实业务步骤，分步等待仍占用硬件锁，必须安全结束会话后分析具体目录。原 ID、参数键、历史
NPZ 和数据目录保留，重跑使用独立目录；分析指纹与重算归档不改写历史 raw。T2 继续保留逐帧
抢救、原始异常和收尾失败语义，结束保持 RF 门控当前 Burst 状态。

当前项目有两种实验入口：

1. `experiments/*.ipynb`
   用于交互式调试、人工确认流程、历史实验复现。
2. `experiments/*.py` + `experiments/*_plot.py`
   用于可重复采集和离线分析分离。

推荐约定：

- `Experiment_Name.py`：负责连接仪器、执行扫描、保存 `data/<实验类型>/<时间戳>_<tag>/raw/`
- `Experiment_Name_plot.py`：负责读取原始数据、分析、绘图、写入 `results/`

已采用这一约定的脚本对见下文[主要实验入口](#主要实验入口)表格；
完整清单与各实验执行模式以[实验注册表](lab_workflows/experiments/registry.py)为准。

## 主要实验入口

下表包含常用正式实验与交互式 Notebook；完整正式实验清单见
[实验注册表](lab_workflows/experiments/registry.py)。

| 方向 | 主要入口 | 相关文档 |
|---|---|---|
| 示波器采集（运行 / 历史；5 采集、3 分析节点，保留波形、PSD 和双曲线历史操作） | `experiments/Scope_Capture.py`、`experiments/Scope_Capture_plot.py` | `docs/experiments/scope_capture.md` |
| Mx Y RF 灵敏度长飘（运行 / 历史；11 采集、3 父趋势分析节点，绝对时间调度、逐轮即时分析，保留两张趋势图和无效轮诊断） | `experiments/Mx_Y_RF_Sensitivity_Drift.py`、`experiments/Mx_Y_RF_Sensitivity_Drift_plot.py` | `docs/experiments/mx_y_rf_sensitivity_drift.md` |
| 光散粒噪声（运行／历史，8 采集／3 独立分析节点；逐条恢复温控后保存，保留原 ASD² 加权拟合与四组 PNG/PDF） | `experiments/Photon_shot_noise.py`、`experiments/Photon_shot_noise_plot.py` | `docs/experiments/Photon_shot_noise.md` |
| 原子自旋投影噪声（SDS；运行／历史、9 采集／2 分析节点，保留可选 OFF 背景和原 Welch／洛伦兹拟合，Pump 载波／门控全程不修改） | `experiments/Projection_noise.py`、`experiments/Projection_noise_plot.py` | `docs/experiments/Projection_noise.md` |
| 静磁场灵敏度（Z 直流逐点色散、采样率与时长控制的连续 X/Y/R 波形中位数、全部有效点拟合、分组温控；交互流程图与阶段分析，历史独立覆盖 Z 换算系数，不兼容旧格式重分析） | `experiments/Static_Magnetic_Field_Sensitivity.py`、`experiments/Static_Magnetic_Field_Sensitivity_plot.py`；独立全局优化已归档至 `old/experiments/experiments/Static_Magnetic_Field_Sensitivity_Optimize.py`，原采集不改写 | `docs/experiments/static_mag_sens_v2.md` |
| Z 场带宽（10 采集／3 分析节点、运行／历史双 Tab；FFT／软件锁相／硬件矢量与标量 R、0 Hz 背景及历史独立分析） | `experiments/Z_Field_Bandwidth_Measurement.py`、`experiments/Z_Field_Bandwidth_Measurement_plot.py` | `docs/experiments/z_field_bandwidth.md` |
| Mx Y 向 RF 场灵敏度 | `experiments/Mx_Y_RF_Sensitivity.py`、`experiments/Mx_Y_RF_Sensitivity_plot.py` | `docs/experiments/mx_y_rf_sensitivity.md` |
| Mx Y 向 RF 频率响应（幅度×频率扫描；10 采集、3 分析节点；统一布洛赫拟合与逐幅度诊断，保持历史 γ 定义和原质量门槛） | `experiments/Mx_Y_RF_Frequency_Response.py`、`experiments/Mx_Y_RF_Frequency_Response_plot.py` | `docs/experiments/mx_y_rf_frequency_response.md` |
| Mx Y 最优控制 RF 频率响应（冻结最优控制的相位中位数曲线；可选常数 Z 的 0° 对照与标定外推诊断；专属运行/历史页内嵌依赖流程和目录阶段分析） | `experiments/Mx_Y_Optimal_Control_RF_Frequency_Response.py`、`experiments/Mx_Y_Optimal_Control_RF_Frequency_Response_plot.py` | `docs/experiments/mx_y_optimal_control_rf_frequency_response.md` |
| Mx Z 最优控制 RF 灵敏度（Z 控制波形取自闭环冻结文件；X DC + Y RF 偏置平衡剩磁场；Demod0 X/Y 成对正交校相；噪声采集可开启 RF 并设置幅值，默认 0.002 Vpp） | `experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py`、`experiments/Mx_Z_Optimal_Control_RF_Sensitivity_plot.py` | `docs/experiments/mx_z_optimal_control_rf_sensitivity.md` |
| Mx Z 最优控制 XY 补偿偏置 RF 灵敏度（中心一次校相后扫描 X/Y DC 补偿并生成灵敏度地图） | `experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity.py`、`experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity_plot.py` | `docs/experiments/mx_z_optimal_control_xy_rf_sensitivity.md` |
| Mx Z 最优控制 XY 噪声谱（Z 控制开启、Y RF 交流关闭，扫描 X/Y DC 偏置并统计 R ASD 中位数） | `experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum.py`、`experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py` | `docs/experiments/mx_z_optimal_control_xy_noise_spectrum.md` |
| Mx Z 最优控制 XY 泄露响应（双 Tab 与 7/3 分步节点；二维同形任意波扫描及实测最小 R 网格点，保持 Z/XY 与共同触发） | `experiments/Mx_Z_Optimal_Control_XY_Leakage_Response.py`、`experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py` | `docs/experiments/mx_z_optimal_control_xy_leakage_response.md` |
| Mx Z 最优控制 XY 平衡场 RF 相位响应（双 Tab 与 6/3 分步节点；蛇形 DC 网格及配对相位，历史拟合门槛独立覆盖，不选最佳 XY） | `experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response.py`、`experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py` | `docs/experiments/mx_z_optimal_control_xy_rf_phase_response.md` |
| Mx Z 最优控制 XYZ 平衡场（双 Tab 与 6/3 分步节点；X/Y DG4000 DC + Z GS200 三维蛇形扫描，实测最小 R 点与逐层 V 形拟合诊断，结束恢复 X/Y 与 GS200 原态） | `experiments/Mx_Z_Optimal_Control_XYZ_Balance.py`、`experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py` | `docs/experiments/mx_z_optimal_control_xyz_balance.md` |
| Mx Z 最优控制 DG4000 偏置 XYZ 平衡场（运行/历史双 Tab、6/3 分步节点；GS200 断开，Z 任意波 offset + XY DC 蛇形网格；原复数 Bloch 拟合与静态 Z 标定，结束恢复 Z/触发/XY 原态） | `experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py`、`experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py` | `docs/experiments/mx_z_optimal_control_dg4000_bias_xyz_balance.md` |
| Z 任意波线圈波形一致性验证（SDS CH3 测量、CH4 硬件触发） | `experiments/Z_AW_Waveform_Scope_Check.py`、`experiments/Z_AW_Waveform_Scope_Check_plot.py` | `docs/experiments/z_aw_waveform_scope_check.md` |
| Z 线圈电感效应频率响应（固定幅度正弦扫频、SDS CH3/CH4；CH3 初始量程可调且跨频继承） | `experiments/Z_Coil_Inductance_Frequency_Response.py`、`experiments/Z_Coil_Inductance_Frequency_Response_plot.py` | `docs/experiments/z_coil_inductance_frequency_response.md` |
| Z 线圈实际电流频率响应（对数扫频 100 Hz–500 kHz、CH1 实测驱动电压作相位参考、CH3 低端采样电阻；可靠覆盖驱动闭环插值） | `experiments/Z_Coil_Current_Frequency_Response.py`、`experiments/Z_Coil_Current_Frequency_Response_plot.py` | `docs/experiments/z_coil_current_frequency_response.md` |
| Mx Z 实际电流-耦合强度标定（正反向电流扫描、离线拟合诊断与排除原因） | `experiments/Mx_Z_Current_Coupling_Calibration.py`、`experiments/Mx_Z_Current_Coupling_Calibration_plot.py` | `docs/experiments/mx_z_current_coupling_calibration.md` |
| Z 任意波实际电流波形验证（运行/历史；6 采集、3 分析节点，理论/闭环双来源冻结、CH3 实际电流与 CH4 触发对齐） | `experiments/Z_AW_Current_Waveform_Scope_Check.py`、`experiments/Z_AW_Current_Waveform_Scope_Check_plot.py`（显式传入运行目录） | `docs/experiments/z_aw_current_waveform_scope_check.md` |
| Z 任意波实际电流闭环校正（运行/历史；6 采集、2 分析节点，复用 XY 分步界面，保留三种校正与独立验证） | `experiments/Z_AW_Closed_Loop_Waveform_Correction.py`、`experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py`（显式传入运行目录） | `docs/experiments/z_aw_closed_loop_waveform_correction.md` |
| Mx Y RF 光功率灵敏度优化（运行/历史；10 采集、3 分析节点，蛇形网格、逐点诊断与双斜率排名；离线入口显式传入目录） | `experiments/Mx_Y_RF_Power_Optimization.py`、`experiments/Mx_Y_RF_Power_Optimization_plot.py` | `docs/experiments/mx_y_rf_power_optimization.md` |
| Mx Y RF Probe 光功率与 PZT 失谐灵敏度优化（运行/历史、交互流程图；支持固定 Probe；不连接 TEC） | `experiments/Mx_Y_RF_Probe_Detuning_Optimization.py`、`experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py` | `docs/experiments/mx_y_rf_probe_detuning_optimization.md` |
| Mx 主磁场频率标定 | `experiments/Mx_Main_Field_Calibration.py`、`experiments/Mx_Main_Field_Calibration_plot.py` | `docs/experiments/mx_main_field_calibration.md` |
| Mx Keithley 6221 主磁场频率标定（已归档） | `old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration.py`、`old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py` | `docs/experiments/mx_keithley_6221_main_field_calibration.md` |
| Mx Keithley 6221 最优控制 RF 灵敏度（已归档） | `old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py`、`old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py` | `docs/experiments/mx_keithley_6221_optimal_control_rf_sensitivity.md` |
| Mx Keithley 6221 最优控制 XYZ 平衡场（6221 外部触发最优控制 + X/Y DG4000 DC + Z GS200 三维扫描，以实测 Demod0 R 最小点为结果）（已归档） | `old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py`、`old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance_plot.py` | `docs/experiments/mx_keithley_6221_optimal_control_xyz_balance.md` |
| Mx Keithley 6221 最优控制平衡-灵敏度一体化（已归档） | `old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py`、`old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity_plot.py` | `docs/experiments/mx_keithley_6221_optimal_control_balanced_sensitivity.md` |
| Mx 主磁场控制噪声谱（已归档） | `old/experiments/experiments/Mx_Main_Field_Noise_Spectrum.py`、`old/experiments/experiments/Mx_Main_Field_Noise_Spectrum_plot.py` | `docs/experiments/mx_main_field_noise_spectrum.md` |
| Mx 主磁场示波器噪声谱（运行/历史、10 采集/2 分析节点，固定 X/Y DC 补偿与 AC/DC 耦合） | `experiments/Mx_Main_Field_Scope_Noise_Spectrum.py`、`experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py` | `docs/experiments/mx_main_field_scope_noise_spectrum.md` |
| Mx 高主场 Z 磁场频率标定 | `experiments/Mx_Z_Field_Calibration.py`、`experiments/Mx_Z_Field_Calibration_plot.py` | `docs/experiments/mx_z_field_calibration.md` |
| Bell Bloom Z 磁场频率标定（运行 / 历史双 Tab） | `experiments/Bell_Bloom_Z_Field_Calibration.py`、`experiments/Bell_Bloom_Z_Field_Calibration_plot.py` | `docs/experiments/bell_bloom_z_field_calibration.md` |
| 探测链路频率响应标定（运行 / 历史；运行页为可勾选、带依赖的交互流程图，8 个采集模块与 3 个分析模块，完整运行入口保留；锁相放大器关闭输入 AC 耦合、其余解调设置对照 XY 正弦控制噪声谱；每点关闭并恢复温控；采集 Demod0 Y，排除零基带点；分阶段分析绑定运行目录并按输入指纹失效） | `experiments/Detection_Chain_Frequency_Response.py`、`experiments/Detection_Chain_Frequency_Response_plot.py` | `docs/experiments/detection_chain_frequency_response.md` |
| Mx Z 直流控制噪声谱（已归档） | `old/experiments/experiments/Mx_Z_Noise_Spectrum.py`、`old/experiments/experiments/Mx_Z_Noise_Spectrum_plot.py` | `docs/experiments/mx_z_noise_spectrum.md` |
| Mx XY 剩磁二维校准（SDS；运行/历史双 Tab、10 采集/3 分析节点，保留同向网格与原 Bloch/漂移拟合，薄分析入口显式指定目录） | `experiments/Mx_XY_Residual_Field_Calibration.py`、`experiments/Mx_XY_Residual_Field_Calibration_plot.py` | `docs/experiments/mx_xy_residual_field_calibration.md` |
| T1 标定（模块化，8 采集／3 分析节点，运行／历史与可勾选流程图；CH1 差分、CH4 同步，结束 X/Y/Z 归零关闭） | `experiments/T1_Calibration.py`、`experiments/T1_Calibration_plot.py` | [实验说明](docs/experiments/T1_calibration.md) |
| T2 标定（运行／历史；真实分步流程与独立分析） | `experiments/T2_Calibration.py`、`experiments/T2_Calibration_plot.py` | `docs/experiments/T2_relaxation.md` |
| X/Y 补偿与通道验证 | `experiments/XY_Compensation_Calibration.ipynb`、`experiments/XY_Channel_Calibration.ipynb`、`experiments/XY_AM_Transfer.ipynb`、`experiments/XY_MOD_ZeroOffset.ipynb`、`experiments/XY_Output_Verification.ipynb` | `docs/experiments/XY_Compensation_Calibration.md`、`docs/experiments/z_field_calibration.md` |
| XY 控制噪声谱测量（运行 / 历史；原图五组交互流程，14 个采集模块与 5 个分析模块；保存、预览与运行操作融入流程图；节点编辑基础／高级参数、共享值与分类同步、草稿重跑预览；下一次实验与具体目录分析设置隔离；显式前置、校相门槛、保留输出的硬件会话、空闲改参、独立目录重跑与手动结束；按格间距自动分段、只读平均段数与耗时预览；移动峰标定验收、三实验共享全轴分离与独立背景验收；标定或拟合失败仍保留二维 PSD） | `experiments/Noise_Spectrum_XY_Ctrl.py`、`experiments/Noise_Spectrum_XY_Ctrl_plot.py` | `docs/experiments/noise_spectrum_xy_ctrl.md` |
| XY 控制测量已知可控噪声谱（运行 / 历史；采集与分析两列依赖节点，Z 小线圈注入分段平顶谱伪噪声，手动输入 K_Z 并保存本轮快照，相邻点交替 on/off 并逐段保存；分析选择静磁场 v3 历史曲线拟合检测增益，未选来源时完成谱分离并等待标定；保留不含线圈频响的真值链与独立背景验收） | `experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py`、`experiments/Noise_Spectrum_XY_Ctrl_Known_Noise_plot.py` | `docs/experiments/noise_spectrum_xy_ctrl_known_noise.md` |
| XY 控制测量已知不可控 Probe AM 噪声谱（运行 / 历史；采集与分析两列依赖节点，Probe AOM 外部 AM 的 ON/OFF 成对采集，可选全程叠加独立配置的 Z 已知可控噪声；A08 载波节点、A12 双噪声上传与 A14 配对扫描，分析 B01–B04 共享全轴分析提取锁相端 N_S1 与有符号差分，移动峰不足时输出暂定控制轴并保留质量标记） | `experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM.py`、`experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM_plot.py` | `docs/experiments/noise_spectrum_xy_ctrl_uncontrolled_probe_am.md` |
| Demod3 R 噪声谱测量（运行 / 历史；12 采集 / 3 独立分析节点，批次恢复后保存、节点改参与显式重跑；保留 R 均值、脊线标定和洛伦兹拟合，非 PSD） | `experiments/Noise_Spectrum_XY_Demod3_R.py`、`experiments/Noise_Spectrum_XY_Demod3_R_plot.py` | `docs/experiments/noise_spectrum_xy_demod3_r.md` |
| MORS 相关 | `experiments/MORS_feasibility_test.ipynb`、`experiments/MORS_feasibility_test_v2.ipynb`、`experiments/MORS_polarization_pulsed.ipynb` | `docs/experiments/MORS_polarization.md` |
| RF 场灵敏度（DirectAW，可执行流程图 / 历史） | `experiments/RF_Field_Sensitivity_AW.py`、`experiments/RF_Field_Sensitivity_AW_plot.py` | `docs/experiments/rf_field_measurement.md` |
| RF 场灵敏度（DirectAW R，R 标定 / X 噪声）（已归档） | `old/experiments/experiments/RF_Field_Sensitivity_AW_R.py`、`old/experiments/experiments/RF_Field_Sensitivity_AW_R_plot.py` | `docs/experiments/rf_field_measurement_r.md` |
| 已知噪声下 RF 灵敏度（DirectAW；17 采集、4 分析节点，固定同轮噪声 ON，控制版本多选且分别校相/标定） | `experiments/RF_Field_Sensitivity_AW_Known_Noise.py`、`experiments/RF_Field_Sensitivity_AW_Known_Noise_plot.py` | `docs/experiments/rf_field_measurement_known_noise.md` |
| 多控制 Scale RF 响应与灵敏度（交互流程图；不注入人为噪声） | `experiments/RF_Control_Scale_Sensitivity.py`、`experiments/RF_Control_Scale_Sensitivity_plot.py` | `docs/experiments/rf_control_scale_sensitivity.md` |
| RF 场历史 Notebook（未迁移） | `experiments/RF_Field_Sensitivity.ipynb`、`experiments/RF_Field_Sensitivity_AW.ipynb` | 历史实验入口；当前 Python 流程见 `docs/experiments/rf_field_measurement.md` |
| RF 场频率响应（外部 AM，已归档） | `old/experiments/experiments/RF_Field_Sensitivity_AW_FreqSweep.py`；共用 `experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py` 原位保留 | `docs/legacy_lockin_experiments.md`、`old/experiments/README.md` |
| RF 场频率响应（DirectAW；运行／历史、真实分步流程与独立分析） | `experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_compare.py` | `docs/experiments/rf_field_measurement_2.md` |
| RF 场频率响应（XY 恒定场） | `experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py`、`experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py`（强类型模块化，11 采集／3 分析节点，运行／历史双 Tab 与可勾选流程图；0 Hz 仅关 Z、XY 保持开启，统一安全收尾） | [实验说明](docs/experiments/rf_sensitivity_constxy_frequency.md) |
| BB Pump 占空比 Burst 扫描（8 采集／3 分析节点，运行／历史与交互流程图） | `experiments/BB_Rpump_DutyScan_Burst.py`、`experiments/BB_Rpump_DutyScan_Burst_plot.py` | [实验说明](docs/experiments/bb_duty_scan_burst.md) |
| BB Pump 功率 Burst 扫描（8 采集／3 分析节点，结束保留门控与末点 Pump） | `experiments/BB_Rpump_PowerScan_Burst.py`、`experiments/BB_Rpump_PowerScan_Burst_plot.py` | [实验说明](docs/experiments/bb_power_scan_burst.md) |
| XY DC 电压标定（已归档） | `old/experiments/experiments/XY_DC_Voltage_Calibration.py`、`old/experiments/experiments/XY_DC_Voltage_Calibration_plot.py` | `docs/experiments/xy_dc_voltage_calibration.md` |
| XY DirectAW DC 标定（运行／历史、真实分步流程与独立分析） | `experiments/XY_DirectAW_DC_Calibration.py`、`experiments/XY_DirectAW_DC_Calibration_plot.py` | `docs/experiments/xy_direct_aw_dc_calibration.md` |

## 数据与结果目录

单次实验通常写入：

```text
data/<实验类型>/MMDD_HHMM_tag/
  experiment_config.yaml
  raw/
    *.npz
    *.npy
    *.csv
  results/
    *.npz
    *.yaml
    *.json
    *.png
```

分析脚本应尽量只依赖本地数据，不依赖在线仪器状态。

所有新增或重构的 Python 实验图默认使用 `lab_workflows.plotting` 的 `paper` 配置；
制作 A0 海报面板时显式选择 `a0_poster`。支持毫米图幅以及 `nature` / `aps` 投稿
排版起点；公共保存接口默认输出同名 PDF＋PNG。尺寸、配色、导出与科研表达约定见
[科研绘图规范](docs/plotting_style.md)。运行
`agent_exp_env\Scripts\python.exe -m examples.publication_plot_gallery`
可在 `results/publication_plot_gallery/` 生成四类可复现模拟样图、源数据与图注。

现有 Python 分析及独立绘图入口已统一接入公共绘图接口；历史 Notebook 仅盘点，
未修改。迁移范围、真实数据验证和待审定的统计问题见
[绘图检查报告](docs/plotting_audit_20260920.md)；旧脚本的输入与运行方式见
[历史分析绘图入口](docs/legacy_analysis_plotting.md)。

## 配置管理

全局设备、映射与安全配置由所有实验共享：

| 文件 | 用途 |
|---|---|
| `params/devices.yaml` | 稳定设备 ID、型号、资源、连接参数、能力和参考时钟 |
| `params/devices.yaml` 顶层 `network_resources` | 网络扫描候选；显式空列表清空候选，已入库设备仍参与扫描 |
| `params/instrument_discovery.yaml` | 仅在设备库缺少 `network_resources` 时读取的旧扫描配置 |
| `params/mapping.yaml` | 物理量到 `device_id` 和类型化端点的绑定，以及共享/同机约束 |
| `params/safety_limits.yaml` | 输出量安全上下限 |
| `params/experiment_catalog.yaml` | GUI 标签、归档状态、实验名称、简介与参数分组布局 |
| `params/clock_sources.yaml` | 旧入口的参考时钟兼容回退，不是新模式配置源 |
| `params/experiments/<experiment-id>.yaml` | 正式实验的版本化默认参数，使用稳定外部键 |

信号发生器设备与物理量绑定分别配置：

```yaml
devices:
  dg9q280100002:
    instrument: signal_generator
    model: DG900
    resource: USB0::0x1AB1::0x0646::DG9Q280100002::INSTR
    reference_clock: EXT

mapping:
  Pump_laser_power:
    instrument: signal_generator
    device_id: dg9q280100002
    endpoint: {kind: channel, index: 1}
```

正式 Python 实验统一调用 `lab_workflows.devices.create_signal_generator()`，
可直接传入 `"Z_magnetic_field"` 等物理量键，也兼容配置字典及原 resource 调用，
由当前设备库绑定选择 DG4000 或 DG900 Pro 驱动。更换信号发生器时，在 GUI
整组修改绑定即可；连接失败不自动换设备。新模式不硬编码驱动型号。同一 resource 自动复用连接，
不同 resource 的配对物理量由通道路由代理分别转发。详细规则见
`docs/instrument_mapping.md`。

新模式硬件工作流在设备连接完成后统一调用
`lab_workflows.steps.synchronize_connected_clocks()`，按 `params/devices.yaml`
设置已连接的 DG4000、DG900 和所选 HF2 / MFLI，并通过回读严格验证；任何设备不一致都会在实验正式输出配置前终止实验。

当前系统的 DG4000、DG900 和 HF2 / MFLI 统一使用同一外置 10 MHz 参考源，设备库中的
`reference_clock` 均为 `EXT`，包含 Pump 调制信号源 `DG4E222800868`。
Pump 载波与门控共用该设备的参考时钟。运行前接通外置参考；旧 Python 实验先通过
GUI“功能模块 → 参考时钟同步”或 [统一同步脚本](experiments/Set_All_Clocks_External.py)
完成同步并确认全部成功。接线和锁定确认见 [参考时钟说明](docs/instrument_mapping.md#统一外置参考时钟)。
GUI 和脚本的批量同步先向全部设备写入目标参考源，再统一回读校验，不改动输出负载。
负载使用独立的“功能模块 → 输出负载初始化”：Pump 调制通道为 50 Ω，
其余通道（包括 Probe AOM 载波、加热、温度开关）为高阻，写入后验证回读。
正式实验通过设备工厂连接信号源时仍应用对应物理量的负载策略；同一通道跨实验复用时，
负载以本次物理量及其联动组为准。更改负载后需核对幅度和偏置回读。

所有实验在设置输出量前都应调用 `validate_safety_limit()`。
正式硬件工作流统一通过 `lab_workflows/steps/safety_shutdown.py` 声明安全收尾策略：
需要关闭的 DG 通道会依次关闭 Burst/同步/调制、归零为 0 V DC 并关闭输出，随后恢复
温度开关为 5 V ON 并只断开 TEC 通信。默认只保留主磁场、Pump/Probe 光功率、Pump
调制输出和所选锁相放大器的全部设置；温控硬件保持运行，其他可控输出全部关闭。实验特定例外必须显式声明；
T2 额外保持 RF 门控 CH2 的输出及当前 Burst 状态；不以断开通信代替安全收尾。

## 开发验证

实验注册、离线分析入口和 Skill 资源的基础检查：

```powershell
.\agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root .
```

验证器默认读取 Git 工作区变更，追踪静态导入和已声明的动态源码，执行受影响的业务测试节点。
修改采集业务时场景缺口会使验证失败；未修改实验的缺口保留在报告中。先查看计划与模型/流程
元数据，或对照指定提交检查已提交改动：

```powershell
.\agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root . --plan-only --report .cache/expcodegen/plan.json
.\agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root . --changed-since HEAD --report .cache/expcodegen/validation.json
.\agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\sync_skill.py --check
```

`--changed-file` 可重复指定明确变更；共享步骤的其他调用方不会因指定单个实验 ID 被排除。
`--plan-only` 未执行测试，场景声明也不能代替对真实配置、采集和写盘路径的人工核对。
动态调用及 GUI 关系的未映射文件仍需检查，自动验证不连接真实仪器。

仪器驱动、映射、时钟和锁相 API 的无硬件回归：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest signal_generator.test_interfaces lab_workflows.test_instrument_config lab_workflows.test_output_policies lab_workflows.test_clock_sync lab_workflows.test_lockin GUI.backend.test_instrument_discovery GUI.backend.test_lockin_api
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_workflow lab_workflows.experiment_modules.static_sensitivity.test_dc_scan
```

前端变更在 `GUI/frontend/` 运行 `node node_modules/typescript/bin/tsc --noEmit`、
`npm run build` 和 `npm test`；API schema 变更后，
按 [GUI 开发说明](GUI/README.md#前端结构)重新生成类型。Python 变更运行受影响的
现有无硬件测试，命令示例见本地 `GUI/AGENTS.md`。当前检出没有根目录
`tests/`，不要直接执行依赖该目录的测试发现命令。

## 编码约定

- 图中的坐标轴、图例、标题、注释等统一使用**英文**
- 代码注释、文档字符串、提交信息统一使用**中文**
- 新模式模型字段使用 `snake_case`，通过 `external_name` 保留 GUI/YAML 的稳定旧键
- 旧模式脚本参数仍集中放在脚本或 Cell 顶部并使用全大写命名
- 扫描循环使用 `try/finally`，确保异常时设备恢复安全状态
- 幅度扫描优先使用 `set_amplitude()`，避免 `setup_sine()` 导致 Burst 退出或相位跳变

## AI 协作

仓库已经为不同 Agent 准备了协作说明和实验代码生成 Skill（均为本地文件，不上传 GitHub）：

- `AGENTS.md`
- `CLAUDE.md`
- `GUI/AGENTS.md`
- `.agents/skills/expcodegen/SKILL.md`
- `.claude/skills/expcodegen/SKILL.md`
- `.agents/skills/arcgen/SKILL.md`
- `.claude/skills/arcgen/SKILL.md`
- `.agents/skills/archify/SKILL.md`
- `.claude/skills/archify/SKILL.md`

这些说明会要求 Agent 在生成或修改实验代码时读取：

- `params/devices.yaml`
- `params/mapping.yaml`
- `params/safety_limits.yaml`
- `docs/*.md`

从而尽量保证实验脚本、文档和安全约束保持一致。

用户明确要求独立实验架构图时，使用 `arcgen` 调研真实实现，并由 `archify` 完成图规格、校验和交付；不默认接入 GUI。
`AGENTS.md`、`CLAUDE.md`、`GUI/AGENTS.md` 与 `.agents/`、`.claude/`、`.codex/`、
`.vscode/` 等协作说明、Skill 与 Agent 配置一律只保留在本地，不上传 GitHub；仓库同步
面向其他同学使用的 GUI 实验系统内容。本检出的 `.git/info/exclude` 还忽略 `docs/` 中
未跟踪的文件，已跟踪文档仍正常提交。被忽略的本地文件需单独检查，普通 `git diff`
不会显示其变化。

普通气室实验的采集顺序、`SYSTEM_SETTLE_S` 兼容规则和 TEC 设温边界见[统一采集规范](docs/acquisition_timing.md)。
