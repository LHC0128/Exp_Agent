# 实验自动化平台

面向 Bell-Bloom 磁力仪实验的自动化仓库，覆盖仪器控制、参数扫描、原始数据采集、离线分析与绘图。仓库同时保留交互式 Notebook 和可重复运行的 Python 脚本，适合实验调试、批量采集和后处理复现。

## 近期更新（2026 年 10 月）

- GUI 支持通过根目录 `start_gui.bat` 双击启动；静磁场与 RF DirectAW 提供可执行交互流程图。静磁场节点支持参数编辑、勾选与依赖检查、分步会话和独立阶段分析，保留逐点 DC 连续波形中位数与分组温控。
- [锁相驱动](src/lockin_amplifier/README.md)拆分为 HF2、MFLI 独立型号模块，共用通信、解调和采集接口；GUI 支持统一设备选择、Signal / Aux 输出及内置 Scope，实验预检检查型号能力与频率范围。
- [信号发生器驱动](src/signal_generator/README.md)拆分为 DG4000、DG900 Pro 独立实现，保留旧导入入口；共同接口覆盖波形、调制、Burst、计数器和截图，Pro 额外支持内置任意波文件。
- 设备库统一管理 USB / 网络 / LabOne 发现与扫描候选地址，设备选择按同机和共享通道约束联动；配置先编辑草稿，再整批保存，同次实验的独立物理量占用同一通道时拒绝启动。
- “参考时钟同步”先写入全部设备，再统一回读；“输出负载初始化”单独设置 Pump 调制 50 Ω、其余高阻。高阻回读标记统一显示和提交为 `INF`，两项工具分别报告每台设备结果。
- 根目录 [AGENTS.md](AGENTS.md) 和 [CLAUDE.md](CLAUDE.md) 随项目同步，维护入口和无硬件验证命令同步更新。

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
docs/                     # 模块文档、实验文档、迁移清单与手册
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
GUI/                      # 本地 Web 实验控制台
  backend/                # FastAPI API、任务队列与 SSE
  frontend/               # React + TypeScript + Vite
  start.ps1               # 后端与前端开发服务启动器
start_gui.bat             # Windows 双击启动前后端并打开浏览器
data/                     # 原始实验数据
results/                  # 结果图表
总结/                     # 阶段性总结
```

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
[`docs/tec_controller.md`](docs/tec_controller.md)。

相关说明可见：

- `docs/sds_acquisition.md`
- `docs/signal_generator.md`
- [信号发生器源码阅读指南](src/signal_generator/README.md)
- `docs/lockin_amplifier.md`
- [锁相放大器源码阅读指南](src/lockin_amplifier/README.md)
- `docs/gs200.md`
- `docs/keithley_6221.md`
- `docs/tec_controller.md`
- `docs/toptica_dlc_pro.md`

## GUI 基础使用

示波器采集位于“实验中心 → 示波器采集”，支持通道/模式/边沿/电平触发设置、V/div 与垂直偏置、时域量程与超量程提示、单通道时间/频域显示、软件 Welch PSD、
历史叠加、独立显示/隐藏、颜色和选定历史删除，详见 [示波器采集](docs/scope_capture.md)。

GUI 是运行在实验电脑本机的 Web 控制台，可用于读取和设置 GS200、Keithley 6221 电流源、
TOPTICA DLC pro Probe 激光、DG4000、DG900 Pro、HF2 / MFLI、SDS 示波器等仪器，并运行仓库中
已接入的实验流程。

### 首次安装

先完成下文的[环境准备](#环境准备)。前端需要 Node.js / npm，当前 Vite 要求
Node.js `^20.19.0 || >=22.12.0`；升级依赖后以锁文件中的 `engines` 为准。
在仓库根目录 `D:\Code\exp_agent` 打开 PowerShell，安装 GUI 依赖：

```powershell
.\agent_exp_env\Scripts\python.exe -m pip install -r GUI\requirements.txt
Set-Location GUI\frontend
npm ci
Set-Location ..\..
```

开发模式直接使用前端源码，支持 HMR，无需先运行 `npm run build`。

### 启动 GUI

完成首次安装后，在文件资源管理器中双击仓库根目录的 **`start_gui.bat`**，
即可配置 Tailscale 私人 HTTPS 入口、启动前后端，并在 Vite 就绪后自动打开默认浏览器。
脚本按自身位置定位仓库，不依赖当前工作目录；执行策略仅对启动的 PowerShell 进程生效，
不会修改系统设置，也不会自动安装依赖。

也可以在仓库根目录运行（默认不自动打开浏览器；附加 `-OpenBrowser` 可开启）：

```powershell
.\GUI\start.ps1
```

启动成功后，浏览器访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)。
Vite 在当前窗口运行，将 `/api` 请求代理到独立窗口中的 FastAPI 后端
[http://127.0.0.1:8000](http://127.0.0.1:8000)。两个服务只监听本机地址。
启动器自动读取当前 Tailscale 域名、放行 Vite 的对应域名，并使用 Serve 将私人 HTTPS 请求
转发到 5173。笔记本安装 Tailscale 并加入同一网络后，打开启动窗口显示的
`https://<本机的 Tailscale 域名>` 即可访问，局域网和外网使用同一个地址。
首次启用 HTTPS 时，按 Tailscale 命令显示的链接在账户页面完成一次设置；这一步完成后
后续双击会自动复用配置。具体设置与手动命令见 [GUI 远程访问说明](GUI/README.md#笔记本浏览器访问)。
按 Ctrl+C 只结束当前 Vite 进程；后端需要在自己的窗口停止或关闭。修改 Python 后端代码时，
可在总览页点击“重启后端”；按钮仅在没有运行或排队任务时启用，Vite 和浏览器页面会保持运行，
但后端内存中的任务状态会清空。停止服务前应先通过 GUI 正常停止实验或功能模块，
服务退出不等于仪器安全收尾。

启动器会检查 Python、npm、前端依赖、Tailscale 登录状态以及 8000 / 5173 端口；缺失依赖或端口占用时
报错退出，不会杀进程或自动换端口。后端 20 秒内未监听时不再启动前端。
双击启动失败时窗口会保留错误并等待按键；已启动的后端仍需单独停止。
同一 HTTPS 入口已有其他路径、443 转发或公网 Funnel 配置时也会报错，保留原配置。仅本机运行、无需 Tailscale 时使用：

```powershell
.\GUI\start.ps1 -LocalOnly -OpenBrowser
```

如果需要单独启动后端，在另一个终端执行：

```powershell
Set-Location D:\Code\exp_agent\GUI
..\agent_exp_env\Scripts\python.exe -m backend
```

前端开发服务的独立启动方式见 [GUI 开发说明](GUI/README.md#启动)。若只运行后端，
需先在 `GUI/frontend/` 执行 `npm run build`，才能在 8000 端口访问构建后的页面；
更新前端源码后需要重新构建。

### 基本操作

1. 进入“仪器控制”的“物理量映射”标签，统一选择 USB/IP 设备；设备切换联动同机组及共享通道组并保留通道，目标通道不足时拒绝切换，整批保存。
2. 在“设备库”中维护型号、Resource、连接参数、能力、参考时钟和网络扫描地址（`devices.yaml` 顶层 `network_resources`）；先保存候选地址，再扫描确认型号与序列号并逐台入库。VISA 扫描只执行资源枚举与 `*IDN?` 查询。信号源用途卡片内的 Resource 下拉选择实际仪器，并联动同机/共享通道组、保留通道；单张“锁相放大器”卡片统一选择 HF2 或 MFLI。两类选择均为草稿，点击“保存设备库”后与设备配置一起生效；硬件任务期间可编辑草稿，结束后才可保存。信号源通道允许在不同实验中复用，同一次实验独立使用的物理量占用同一通道时预检拒绝启动。
3. “设备控制”以多列网格展示全部物理量，可逐项“读取”，也可点击“连接并读取全部”；页面打开时不会自动连接硬件。
4. 信号发生器常用波形参数直接显示，波形细节、负载、单位、调制和 Burst 位于折叠的高级区；高阻回读 `9.9E+37` 在负载框显示并提交为 `INF`。`rf_coil` 作为历史别名不单独显示，统一由“Y方向磁场”面板控制。
5. 点击“应用并回读”；写入前会检查当前 mapping key 的 `params/safety_limits.yaml`，写入后以实际回读值更新页面。非 Vpp 状态仅允许回读、关闭输出或切换为 Vpp。
6. 在“功能模块 → Z 任意波控制”选择闭环冻结结果，离线查看 V–t 图，再选择“仅加载，保持关闭”或“加载并启动”。可联动时序信号2并编辑触发参数；Z 面板提供快捷入口。详见 [任意波控制说明](docs/signal_generator.md#gui-z-任意波控制)。
7. 实验结束后，先按需要执行模块的“停止”，再分别停止 Vite 与后端。仅停止服务不会自动关闭已启动的任意波。

“实验中心”默认展示[注册表](lab_workflows/experiments/registry.py)中的未归档正式实验，提供动态参数、默认值保存、
无副作用预检、运行日志、安全停止和离线重新分析。每张实验卡片同时显示对应的
采集程序和独立分析程序；没有独立分析脚本时会明确标注。`*.ipynb` 不进入实验中心，
`*_plot.py` 只作为对应实验的分析器。卡片还会明确显示“新模式”或“旧模式”；完整
清单与执行模式见[实验迁移状态](docs/experiment_migration_status.md)。

在“管理分类与实验信息”中可逐项归档或恢复；归档实验仅在“已归档”入口显示，
保留原分类、配置链接、运行与历史功能，不删除数据或停止活动任务。状态保存到
`params/experiment_catalog.yaml`，重启或换浏览器仍有效。目录加载通过单请求复用设备映射、
其他页面和图表按需加载来减少等待，归档本身不作为提速依据。

启动器会核对 8000 和 5173 端口；重复双击或遇到冲突时，确认占用进程并停止对应旧服务，再重新启动。
总览页的“重启后端”仅适用于由现有启动入口正常运行的后端；若新代码启动失败，需检查后端窗口并人工修复后重启。
更详细的开发说明见 [GUI/README.md](GUI/README.md)。

锁相设备采用相同的 Python Core API 函数入口，支持相位、频率、输出设置、解调 X/Y/R 和内置 Scope 采集。MFLI 已配置为 `dev30661`、`192.168.124.109:8004`、外部参考，启用选件后的频率上限为 5 MHz。双主电压回采实验选择 MFLI 时禁止启动；尚未确认安全电压范围的锁相输出写入保持禁用。接口、硬件差异与实机验收见 [锁相模块说明](docs/lockin_amplifier.md)，受限实验清单见 [锁相实验兼容性](docs/legacy_lockin_experiments.md)。

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

`rf-sensitivity-aw` 已迁移为 schema version 2 的 DirectAW 工作流，保留
`RF_Field_Sensitivity_AW` 数据目录与原脚本入口。XYZ 共用时序信号2主输出触发，
HF2 使用 Demod0 Y → AuxOut2 → Signal Input2 → Demod3 回环，以 X 响应斜率和
X 噪声 PSD 计算灵敏度，并用响应色散拟合得到的等效 HWHM 补偿洛伦兹频响。
GUI 提供运行 / 历史双 Tab，运行页有 16 个采集节点与 4 个分析节点，按依赖显式勾选执行，
在节点内编辑参数并预览草稿的重跑范围；分步等待保持连接与硬件锁，A15 完成本轮安全收尾，
A16 结束会话。历史只接纳新格式；完整分析结果支持跨页多选、
自定义英文图例、X 响应与灵敏度双面板叠加及 PNG/PDF 下载。对比区的灵敏度面板
只显示主谱，拟合失败的数值斜率回退结果会提示未校正。校相失败
或采集未完成时只展示诊断。最优控制波形从
`D:\Code\theory_agent\simulate\results\oc_sens` 的 `vN/waveforms/optimal_control_waveform.csv`
选择，运行前须明确选择有效版本。默认值见
[参数文件](params/experiments/rf-sensitivity-aw.yaml)，接线、收尾及分析见
[实验说明](docs/rf_field_measurement.md)。

`rf-sensitivity-aw-r`（RF 灵敏度（DirectAW R））复用上述准备、采集及安全收尾，
按 A10 → A13 → A12 → A14 顺序先采 Demod3 R，再独立改频校相并采零 RF X 噪声，跳过 A11。
R 标定使用绝对色散底层中心斜率，失败则以数值梯度估算并关闭线宽补偿。
新实验使用独立默认文件与 `RF_Field_Sensitivity_AW_R` 历史，提供运行 / 历史、交互流程图、
多次 R 标定 / X 噪声对比及 PNG/PDF 导出。两个实验的 AuxOut2 均按已确认的 ±10 V
规则检查，缺范围或预计越界时禁止启动/写入；跨频率增益一致性和等效线宽仍待实测。
详见 [实验说明](docs/rf_field_measurement_r.md) 与 [默认参数](params/experiments/rf-sensitivity-aw-r.yaml)。

`t2-calibration` 已迁移为强类型光学 FID 工作流：保持 `T2_Calibration` 数据目录与
历史 NPZ 字段不变，正常完成、异常、取消和 Ctrl+C 都会把温度开关恢复为 5 V ON，
保持主磁场、Pump/Probe 光功率、Pump 调制、RF 门控输出和全部 HF2 设置；RF 门控仅
关闭 Burst，其他辅助输出归零关闭；正常结束只断开 TEC 通信，温控硬件继续运行。

当前项目有两种实验入口：

1. `experiments/*.ipynb`
   用于交互式调试、人工确认流程、历史实验复现。
2. `experiments/*.py` + `experiments/*_plot.py`
   用于可重复采集和离线分析分离。

推荐约定：

- `Experiment_Name.py`：负责连接仪器、执行扫描、保存 `data/<实验类型>/<时间戳>_<tag>/raw/`
- `Experiment_Name_plot.py`：负责读取原始数据、分析、绘图、写入 `results/`

这一组织方式目前已经用于多个实验，例如：

- `Photon_shot_noise.py` / `Photon_shot_noise_plot.py`
- `Projection_noise.py` / `Projection_noise_plot.py`
- `T1_Calibration.py` / `T1_Calibration_plot.py`
- `T2_Calibration.py` / `T2_Calibration_plot.py`
- `RF_Field_Sensitivity_AW.py` / `RF_Field_Sensitivity_AW_plot.py`
- `RF_Field_Sensitivity_AW_FreqSweep.py` / `RF_Field_Sensitivity_AW_FreqSweep_plot.py`
- `RF_Field_Sensitivity_ConstXY_FreqSweep.py` / `RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py`
- `XY_DC_Voltage_Calibration.py` / `XY_DC_Voltage_Calibration_plot.py`
- `XY_DirectAW_DC_Calibration.py` / `XY_DirectAW_DC_Calibration_plot.py`
- `Noise_Spectrum_XY_Demod3_R.py` / `Noise_Spectrum_XY_Demod3_R_plot.py`
- `Mx_Y_RF_Sensitivity.py` / `Mx_Y_RF_Sensitivity_plot.py`
- `Mx_Z_Optimal_Control_RF_Sensitivity.py` / `Mx_Z_Optimal_Control_RF_Sensitivity_plot.py`
- `Mx_Z_Optimal_Control_XY_Leakage_Response.py` / `Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py`
- `Mx_Z_Optimal_Control_XY_RF_Phase_Response.py` / `Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py`
- `Mx_Z_Optimal_Control_XYZ_Balance.py` / `Mx_Z_Optimal_Control_XYZ_Balance_plot.py`
- `Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py` / `Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py`
- `Mx_Y_RF_Power_Optimization.py` / `Mx_Y_RF_Power_Optimization_plot.py`
- `Mx_Y_RF_Probe_Detuning_Optimization.py` / `Mx_Y_RF_Probe_Detuning_Optimization_plot.py`
- `Mx_Main_Field_Calibration.py` / `Mx_Main_Field_Calibration_plot.py`
- `Mx_Keithley_6221_Main_Field_Calibration.py` / `Mx_Keithley_6221_Main_Field_Calibration_plot.py`
- `Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py` / `Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py`
- `Mx_Main_Field_Noise_Spectrum.py` / `Mx_Main_Field_Noise_Spectrum_plot.py`
- `Mx_Main_Field_Scope_Noise_Spectrum.py` / `Mx_Main_Field_Scope_Noise_Spectrum_plot.py`
- `Mx_Z_Field_Calibration.py` / `Mx_Z_Field_Calibration_plot.py`
- `Mx_Z_Noise_Spectrum.py` / `Mx_Z_Noise_Spectrum_plot.py`
- `Mx_XY_Residual_Field_Calibration.py` / `Mx_XY_Residual_Field_Calibration_plot.py`
- `Z_AW_Waveform_Scope_Check.py` / `Z_AW_Waveform_Scope_Check_plot.py`
- `Z_Coil_Inductance_Frequency_Response.py` / `Z_Coil_Inductance_Frequency_Response_plot.py`

## 主要实验入口

下表包含常用正式实验与交互式 Notebook；完整正式实验清单见
[实验迁移状态](docs/experiment_migration_status.md)。

| 方向 | 主要入口 | 相关文档 |
|---|---|---|
| 示波器采集（运行 / 历史双 Tab） | `experiments/Scope_Capture.py`、`experiments/Scope_Capture_plot.py` | `docs/scope_capture.md` |
| Mx Y RF 灵敏度长飘（按总时间和间隔重复测量） | `experiments/Mx_Y_RF_Sensitivity_Drift.py`、`experiments/Mx_Y_RF_Sensitivity_Drift_plot.py` | `docs/mx_y_rf_sensitivity_drift.md` |
| 光散粒噪声 | `experiments/Photon_shot_noise.py` | `docs/Photon_shot_noise.md` |
| 原子自旋投影噪声（SDS） | `experiments/Projection_noise.py`、`experiments/Projection_noise_plot.py` | `docs/Projection_noise.md` |
| 静磁场灵敏度（Z 直流逐点色散、采样率与时长控制的连续 X/Y/R 波形中位数、全部有效点拟合、分组温控；交互流程图与阶段分析，历史独立覆盖 Z 换算系数，不兼容旧格式重分析） | `experiments/Static_Magnetic_Field_Sensitivity.py`、`experiments/Static_Magnetic_Field_Sensitivity_plot.py`；独立旧优化入口 `experiments/Static_Magnetic_Field_Sensitivity_Optimize.py` 不改采集 | `docs/static_mag_sens_v2.md` |
| Mx Y 向 RF 场灵敏度 | `experiments/Mx_Y_RF_Sensitivity.py`、`experiments/Mx_Y_RF_Sensitivity_plot.py` | `docs/mx_y_rf_sensitivity.md` |
| Mx Y 向 RF 频率响应（模式1扫频方式；幅度可嵌套扫描，统一布洛赫线形拟合覆盖 Lorentzian 与 Rabi 劈裂双峰） | `experiments/Mx_Y_RF_Frequency_Response.py`、`experiments/Mx_Y_RF_Frequency_Response_plot.py` | `docs/mx_y_rf_frequency_response.md` |
| Mx Y 最优控制 RF 频率响应（最优控制相位中位数曲线；可选常数 Z 控制对照，恒定电压由电流耦合标定反解并允许范围外推；专属 GUI 含运行/历史 Tab） | `experiments/Mx_Y_Optimal_Control_RF_Frequency_Response.py`、`experiments/Mx_Y_Optimal_Control_RF_Frequency_Response_plot.py` | `docs/mx_y_optimal_control_rf_frequency_response.md` |
| Mx Z 最优控制 RF 灵敏度（Z 控制波形取自闭环冻结文件；X DC + Y RF 偏置平衡剩磁场；Demod0 X/Y 成对正交校相；噪声采集可开启 RF 并设置幅值，默认 0.002 Vpp） | `experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py`、`experiments/Mx_Z_Optimal_Control_RF_Sensitivity_plot.py` | `docs/mx_z_optimal_control_rf_sensitivity.md` |
| Mx Z 最优控制 XY 补偿偏置 RF 灵敏度（中心一次校相后扫描 X/Y DC 补偿并生成灵敏度地图） | `experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity.py`、`experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity_plot.py` | `docs/mx_z_optimal_control_xy_rf_sensitivity.md` |
| Mx Z 最优控制 XY 噪声谱（Z 控制开启、Y RF 交流关闭，扫描 X/Y DC 偏置并统计 R ASD 中位数） | `experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum.py`、`experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum_plot.py` | `docs/mx_z_optimal_control_xy_noise_spectrum.md` |
| Mx Z 最优控制 XY 泄露响应（二维扫描同形 X/Y 触发任意波并报告实测最小 R 网格点） | `experiments/Mx_Z_Optimal_Control_XY_Leakage_Response.py`、`experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py` | `docs/mx_z_optimal_control_xy_leakage_response.md` |
| Mx Z 最优控制 XY 平衡场 RF 相位响应（X/Y DC 网格逐点扫描 Y RF Burst 相位，使用 Demod0 R 非线性色散模型拟合） | `experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response.py`、`experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py` | `docs/mx_z_optimal_control_xy_rf_phase_response.md` |
| Mx Z 最优控制 XYZ 平衡场（X/Y DG4000 DC + Z GS200 三维扫描，以实测 Demod0 R 最小点为结果） | `experiments/Mx_Z_Optimal_Control_XYZ_Balance.py`、`experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py` | `docs/mx_z_optimal_control_xyz_balance.md` |
| Mx Z 最优控制 DG4000 偏置 XYZ 平衡场（GS200 断开；同一 DG4000 Z 任意波 offset 扫描，并采集 Demod0 X/Y/R） | `experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py`、`experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py` | `docs/mx_z_optimal_control_dg4000_bias_xyz_balance.md` |
| Z 任意波线圈波形一致性验证（SDS CH3 测量、CH4 硬件触发） | `experiments/Z_AW_Waveform_Scope_Check.py`、`experiments/Z_AW_Waveform_Scope_Check_plot.py` | `docs/z_aw_waveform_scope_check.md` |
| Z 线圈电感效应频率响应（固定幅度正弦扫频、SDS CH3/CH4；CH3 初始量程可调且跨频继承） | `experiments/Z_Coil_Inductance_Frequency_Response.py`、`experiments/Z_Coil_Inductance_Frequency_Response_plot.py` | `docs/z_coil_inductance_frequency_response.md` |
| Z 线圈实际电流频率响应（对数扫频 100 Hz–500 kHz、CH1 实测驱动电压作相位参考、CH3 低端采样电阻；可靠覆盖驱动闭环插值） | `experiments/Z_Coil_Current_Frequency_Response.py`、`experiments/Z_Coil_Current_Frequency_Response_plot.py` | `docs/z_coil_current_frequency_response.md` |
| Mx Z 实际电流-耦合强度标定（正反向电流扫描、离线拟合诊断与排除原因） | `experiments/Mx_Z_Current_Coupling_Calibration.py`、`experiments/Mx_Z_Current_Coupling_Calibration_plot.py` | `docs/mx_z_current_coupling_calibration.md` |
| Z 任意波实际电流波形验证 | `experiments/Z_AW_Current_Waveform_Scope_Check.py`、`experiments/Z_AW_Current_Waveform_Scope_Check_plot.py` | `docs/z_aw_current_waveform_scope_check.md` |
| Z 任意波实际电流闭环校正（10 kHz 高通下 CH4 固定参考谐波辨识、候选回退、独立重载验证） | `experiments/Z_AW_Closed_Loop_Waveform_Correction.py`、`experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py` | `docs/z_aw_closed_loop_waveform_correction.md` |
| Mx Y RF 光功率灵敏度优化（含逐点诊断图、tqdm ETA、拟合导数与零点实测斜率双排名） | `experiments/Mx_Y_RF_Power_Optimization.py`、`experiments/Mx_Y_RF_Power_Optimization_plot.py` | `docs/mx_y_rf_power_optimization.md` |
| Mx Y RF Probe 光功率与 PZT 失谐灵敏度优化（支持固定 Probe 单点；不连接 TEC，保留温控开关门控） | `experiments/Mx_Y_RF_Probe_Detuning_Optimization.py`、`experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py` | `docs/mx_y_rf_probe_detuning_optimization.md` |
| Mx 主磁场频率标定 | `experiments/Mx_Main_Field_Calibration.py`、`experiments/Mx_Main_Field_Calibration_plot.py` | `docs/mx_main_field_calibration.md` |
| Mx Keithley 6221 主磁场频率标定 | `experiments/Mx_Keithley_6221_Main_Field_Calibration.py`、`experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py` | `docs/mx_keithley_6221_main_field_calibration.md` |
| Mx Keithley 6221 最优控制 RF 灵敏度 | `experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py`、`experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py` | `docs/mx_keithley_6221_optimal_control_rf_sensitivity.md` |
| Mx Keithley 6221 最优控制 XYZ 平衡场（6221 外部触发最优控制 + X/Y DG4000 DC + Z GS200 三维扫描，以实测 Demod0 R 最小点为结果） | `experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py`、`experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance_plot.py` | `docs/mx_keithley_6221_optimal_control_xyz_balance.md` |
| Mx Keithley 6221 最优控制平衡-灵敏度一体化 | `experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py`、`experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity_plot.py` | `docs/mx_keithley_6221_optimal_control_balanced_sensitivity.md` |
| Mx 主磁场控制噪声谱 | `experiments/Mx_Main_Field_Noise_Spectrum.py`、`experiments/Mx_Main_Field_Noise_Spectrum_plot.py` | `docs/mx_main_field_noise_spectrum.md` |
| Mx 主磁场示波器噪声谱（固定 X/Y DC 补偿，可选 AC/DC 耦合） | `experiments/Mx_Main_Field_Scope_Noise_Spectrum.py`、`experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py` | `docs/mx_main_field_scope_noise_spectrum.md` |
| Mx 高主场 Z 磁场频率标定 | `experiments/Mx_Z_Field_Calibration.py`、`experiments/Mx_Z_Field_Calibration_plot.py` | `docs/mx_z_field_calibration.md` |
| Bell Bloom Z 磁场频率标定（运行 / 历史双 Tab） | `experiments/Bell_Bloom_Z_Field_Calibration.py`、`experiments/Bell_Bloom_Z_Field_Calibration_plot.py` | `docs/bell_bloom_z_field_calibration.md` |
| 探测链路频率响应标定（运行 / 历史双 Tab；锁相放大器关闭输入 AC 耦合、其余解调设置对照 XY 正弦控制噪声谱；每点关闭并恢复温控；采集 Demod0 Y，排除零基带点） | `experiments/Detection_Chain_Frequency_Response.py`、`experiments/Detection_Chain_Frequency_Response_plot.py` | `docs/detection_chain_frequency_response.md` |
| Mx Z 直流控制噪声谱 | `experiments/Mx_Z_Noise_Spectrum.py`、`experiments/Mx_Z_Noise_Spectrum_plot.py` | `docs/mx_z_noise_spectrum.md` |
| Mx XY 剩磁二维校准（SDS） | `experiments/Mx_XY_Residual_Field_Calibration.py`、`experiments/Mx_XY_Residual_Field_Calibration_plot.py` | `docs/mx_xy_residual_field_calibration.md` |
| T1 / T2 标定 | `experiments/T1_Calibration.py`、`experiments/T2_Calibration.py` | `docs/T1_calibration.md`、`docs/T2_relaxation.md` |
| X/Y 补偿与通道验证 | `experiments/XY_Compensation_Calibration.ipynb`、`experiments/XY_Channel_Calibration.ipynb`、`experiments/XY_AM_Transfer.ipynb`、`experiments/XY_MOD_ZeroOffset.ipynb`、`experiments/XY_Output_Verification.ipynb` | `docs/XY_Compensation_Calibration.md`、`docs/z_field_calibration.md` |
| XY 控制噪声谱测量（运行 / 历史 / 架构；原图五组交互流程，14 个采集模块与 5 个分析模块；保存、预览与运行操作融入流程图；节点编辑基础／高级参数、共享值与分类同步、草稿重跑预览；下一次实验与具体目录分析设置隔离；显式前置、校相门槛、保留输出的硬件会话、空闲改参、独立目录重跑与手动结束；按格间距自动分段、只读平均段数与耗时预览；移动峰标定验收、三实验共享全轴分离与独立背景验收；标定或拟合失败仍保留二维 PSD） | `experiments/Noise_Spectrum_XY_Ctrl.py`、`experiments/Noise_Spectrum_XY_Ctrl_plot.py` | `docs/noise_spectrum_xy_ctrl.md` |
| XY 控制测量已知可控噪声谱（运行 / 历史 / 架构；Z 小线圈注入分段平顶谱伪噪声，相邻点交替 on/off 顺序、两态等待并逐段保存；色散连续 X/Y/R 波形中位数与全部有效点线形拟合、不含线圈频响的 K_Z 真值链，共享全控制轴分离与独立背景验收） | `experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py`、`experiments/Noise_Spectrum_XY_Ctrl_Known_Noise_plot.py` | `docs/noise_spectrum_xy_ctrl_known_noise.md` |
| XY 控制测量已知不可控 Probe AM 噪声谱（运行 / 历史双 Tab；Probe AOM 外部 AM 的 ON/OFF 成对采集，可选全程叠加独立配置的 Z 已知可控噪声；共享全轴分析提取锁相端 N_S1 与有符号差分） | `experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM.py`、`experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM_plot.py` | `docs/noise_spectrum_xy_ctrl_uncontrolled_probe_am.md` |
| Demod3 R 噪声谱测量 | `experiments/Noise_Spectrum_XY_Demod3_R.py`、`experiments/Noise_Spectrum_XY_Demod3_R_plot.py` | `docs/noise_spectrum_xy_demod3_r.md` |
| MORS 相关 | `experiments/MORS_feasibility_test.ipynb`、`experiments/MORS_feasibility_test_v2.ipynb`、`experiments/MORS_polarization_pulsed.ipynb` | `docs/MORS_polarization.md` |
| RF 场灵敏度（DirectAW，可执行流程图 / 历史） | `experiments/RF_Field_Sensitivity_AW.py`、`experiments/RF_Field_Sensitivity_AW_plot.py` | `docs/rf_field_measurement.md` |
| RF 场灵敏度（DirectAW R，R 标定 / X 噪声） | `experiments/RF_Field_Sensitivity_AW_R.py`、`experiments/RF_Field_Sensitivity_AW_R_plot.py` | `docs/rf_field_measurement_r.md` |
| RF 场历史 Notebook（未迁移） | `experiments/RF_Field_Sensitivity.ipynb`、`experiments/RF_Field_Sensitivity_AW.ipynb` | 历史实验入口；当前 Python 流程见 `docs/rf_field_measurement.md` |
| RF 场频率响应（AW 包络） | `experiments/RF_Field_Sensitivity_AW_FreqSweep.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py` | `docs/rf_field_measurement_2.md` |
| RF 场频率响应（DirectAW） | `experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_compare.py` | `docs/rf_field_measurement_2.md` |
| RF 场频率响应（XY 恒定场） | `experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py`、`experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py` | 暂无独立文档，可参考 `docs/rf_field_measurement_2.md` |
| XY DC 电压标定 | `experiments/XY_DC_Voltage_Calibration.py`、`experiments/XY_DC_Voltage_Calibration_plot.py` | `docs/xy_dc_voltage_calibration.md` |
| XY DirectAW DC 标定 | `experiments/XY_DirectAW_DC_Calibration.py`、`experiments/XY_DirectAW_DC_Calibration_plot.py` | `docs/xy_direct_aw_dc_calibration.md` |

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
T2 额外保持 RF 门控 CH2 Output ON，并只关闭该通道的 Burst。

## 环境准备

- Python 要求：`>=3.10`
- 本地包名：`exp-agent`
- 虚拟环境：`agent_exp_env\`

在仓库根目录运行以下命令；已有虚拟环境直接复用：

```powershell
if (-not (Test-Path .\agent_exp_env\Scripts\python.exe)) {
    python -m venv agent_exp_env
}
.\agent_exp_env\Scripts\python.exe -m pip install -e .
.\agent_exp_env\Scripts\python.exe -m pip install pyvisa pyvisa-py pyserial zhinst numpy scipy matplotlib pyyaml jupyter
.\agent_exp_env\Scripts\python.exe -m pip check
```

`pip install -e .` 安装 `src/` 下的驱动和根目录的 `lab_workflows`，同时按
`pyproject.toml` 安装 TOPTICA SDK 与进度条。仪器通信、分析和 GUI 依赖仍需分别安装。
根目录 `requirements.txt` 是历史环境快照，不替代上述分组安装说明。

需要导出当前环境时：

```powershell
.\agent_exp_env\Scripts\python.exe -m pip freeze > requirements.txt
```

保存前将本项目的可编辑安装条目规范为 `-e .`，移除机器路径和带凭据的地址。

## 开发验证

实验注册、离线分析入口和 Skill 资源的基础检查：

```powershell
.\agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root .
```

仪器驱动、映射、时钟和锁相 API 的无硬件回归：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest signal_generator.test_interfaces lab_workflows.test_instrument_config lab_workflows.test_output_policies lab_workflows.test_clock_sync lab_workflows.test_lockin GUI.backend.test_instrument_discovery GUI.backend.test_lockin_api
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_workflow lab_workflows.experiment_modules.static_sensitivity.test_dc_scan
```

前端变更在 `GUI/frontend/` 运行 `node node_modules/typescript/bin/tsc --noEmit`、
`npm run build` 和 `npm test`；API schema 变更后，
按 [GUI 开发说明](GUI/README.md#前端结构)重新生成类型。Python 变更运行受影响的
现有无硬件测试，命令示例见 [GUI/AGENTS.md](GUI/AGENTS.md#验证)。当前检出没有根目录
`tests/`，不要直接执行依赖该目录的测试发现命令。

## 编码约定

- 图中的坐标轴、图例、标题、注释等统一使用**英文**
- 代码注释、文档字符串、提交信息统一使用**中文**
- 新模式模型字段使用 `snake_case`，通过 `external_name` 保留 GUI/YAML 的稳定旧键
- 旧模式脚本参数仍集中放在脚本或 Cell 顶部并使用全大写命名
- 扫描循环使用 `try/finally`，确保异常时设备恢复安全状态
- 幅度扫描优先使用 `set_amplitude()`，避免 `setup_sine()` 导致 Burst 退出或相位跳变

## AI 协作

仓库已经为不同 Agent 准备了协作说明和实验代码生成 Skill：

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

生成实验架构图时使用 `arcgen` 调研真实实现，并由 `archify` 完成图规格、校验和交付。
根目录 `AGENTS.md`、`CLAUDE.md` 与 `GUI/AGENTS.md` 纳入版本管理并同步到 GitHub；
`.agents/`、`.claude/` 等本地 Skill 与配置继续忽略。本检出的 `.git/info/exclude`
还忽略 `docs/` 中未跟踪的文件，已跟踪文档仍正常提交。被忽略的本地文件需单独检查，
普通 `git diff` 不会显示其变化。
