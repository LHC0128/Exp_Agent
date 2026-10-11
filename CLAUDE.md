# CLAUDE.md

本文件为 Claude Code 提供本仓库的工作约定。

## 注意事项

- 不要猜测我的意图，有任何不明确的地方必须向我提问。
- 修改代码、文档或实验流程说明前，先核对仓库中的实际文件与目录，不要沿用过期示例。

## 语言要求

与本仓库相关的一切对话和沟通，请使用**简体中文**。

## 项目概述

这是一个面向 Bell-Bloom 磁力仪实验的自动化项目，覆盖：

- 仪器控制
- 实验参数扫描
- 原始数据采集
- 离线分析与绘图
- 实验文档与 AI 协作

项目由用户主导，AI 负责辅助实现、整理和同步文档。

## 当前仓库结构

```text
src/
  gs200/                  # Yokogawa GS200
  keithley_6221/          # Keithley 6221 电流源与任意波控制
  lockin_amplifier/       # Zurich HF2 / MFLI，独立型号驱动、DAQ 与内置 Scope
  sds_acquisition/        # SDS 示波器采集
  sensitivity_analysis/   # 灵敏度拟合、汇总与分析工具
  signal_generator/       # DG4000 / DG900 Pro 独立驱动与共同接口
  tec_controller/         # TEC103 温控器
  toptica_laser/          # TOPTICA DLC pro Probe 激光

experiments/
  *.ipynb                 # 交互式实验 Notebook
  *.py                    # 数据采集 / 自动化脚本
  *_plot.py               # 离线分析与绘图脚本
  control_waveform*.csv   # 任意波 / 控制包络文件

examples/
  *.ipynb                 # 各设备最小示例
  publication_plot_gallery.py  # 无硬件科研绘图示例
  data/                   # 示例采集结果

docs/
  *.md                    # 模块文档与实验文档
  old/                    # 历史归档文档
  DG900Pro_UserGuide_CN.pdf

params/
  devices.yaml            # 设备 ID、型号、资源、连接参数与参考时钟
  instrument_discovery.yaml # 旧网络扫描候选兼容入口
  mapping.yaml            # 物理量 ↔ 设备 ID 与类型化端点
  safety_limits.yaml      # 安全限值
  experiment_catalog.yaml # GUI 标签、名称、简介和参数布局
  clock_sources.yaml      # 旧入口参考时钟兼容配置
  static_sensitivity.yaml # 静磁场旧参数留档，主实验不再读取
  experiments/*.yaml      # 各正式实验的版本化默认参数

lab_workflows/
  analysis/               # 跨实验共享的离线分析
  experiment_modules/     # 新模式实验的参数模型、采集工作流和分析器
  experiments/            # 统一契约、注册表及新旧适配器
  plotting/               # 论文图与 A0 海报公共绘图接口
  steps/                  # 设备会话、输出策略、校相、温控与安全收尾

GUI/
  backend/                # FastAPI 路由、任务队列与 SSE
  frontend/               # React + TypeScript + Vite
  start.ps1               # 同时启动后端和前端开发服务

start_gui.bat             # Windows 双击启动前后端并打开浏览器
data/                     # 实验原始数据
results/                  # 分析结果图表
总结/                     # 阶段性总结与记录

.agents/skills/expcodegen/SKILL.md
.claude/skills/expcodegen/SKILL.md
.agents/skills/arcgen/SKILL.md
.claude/skills/arcgen/SKILL.md
.agents/skills/archify/SKILL.md
.claude/skills/archify/SKILL.md
```

## 定位与修改入口

- 正式实验清单、稳定 ID 和执行模式以 `lab_workflows/experiments/registry.py` 为准；完整清单见 `docs/experiment_migration_status.md`，不要从脚本数量推断注册实验数量。
- 新模式实验先查看 `lab_workflows/experiment_modules/<模块>/definition.py`，再按需读取 `models.py`、`workflow.py`、`analyzer.py` 和对应的 `experiments/` 薄入口。
- 参数与执行契约：`lab_workflows/experiment_params.py`、`lab_workflows/experiment_runtime.py`、`lab_workflows/experiments/contracts.py`。
- 仪器接入与映射：`lab_workflows/devices.py`、`lab_workflows/instrument_config.py`、`params/devices.yaml`、`params/mapping.yaml`；详细规则见 `docs/instrument_mapping.md`。
- 信号发生器先看 `src/signal_generator/README.md`；型号实现分别在 `dg4000.py`、`dg900.py`，共同类型、接口和 VISA 工具在 `types.py`、`protocol.py`、`_visa.py`，`instrument.py` 保留旧导入兼容。
- 锁相放大器先看 `src/lockin_amplifier/README.md`；型号差异在 `hf2.py`、`mfli.py`，公共通信在 `base.py`，DAQ / Scope 在 `daq.py`、`scope.py`；输出安全和实验兼容检查分别在 `lab_workflows/lockin_control.py`、`lab_workflows/lockin_compatibility.py`。
- 仪器发现与配置保存位于 `lab_workflows/instrument_config.py`；网络候选以设备库顶层 `network_resources` 为准，仅字段缺失时读取 `params/instrument_discovery.yaml`。GUI 设备切换复用 `GUI/frontend/src/components/instruments/mappingDevices.ts` 的联动规则。
- 独立时钟同步与负载初始化分别使用 `lab_workflows/clock_sync.py` 的 `synchronize_clocks()`、`initialize_output_loads()`；批量时钟同步先全部写入再统一回读，不设置输出负载。
- 通用硬件步骤和恢复策略优先复用 `lab_workflows/steps/`；跨实验分析优先复用 `lab_workflows/analysis/`，科研绘图使用 `lab_workflows/plotting/`。
- GUI 开发同时遵守 `GUI/AGENTS.md`；启动和前端验证见 `GUI/README.md`。页面位于 `GUI/frontend/src/pages/`，通用组件位于 `GUI/frontend/src/components/`，后端路由和任务管理分别位于 `GUI/backend/main.py`、`GUI/backend/jobs.py`。

## 实验脚本组织约定

- 当前仓库同时维护 **Notebook** 和 **Python 脚本** 两种实验形态。
- 正式 Python 实验通过 `execution_mode` 明确标记：
  - `typed_workflow`：显式强类型参数模型和薄入口的新模式。
  - `legacy_script`：仍由兼容适配器读取脚本常量的旧模式，仅用于渐进迁移。
- 新的可重复实验，优先采用“采集脚本 + 分析脚本”分离模式：
  - `Experiment_Name.py`：连接仪器、执行扫描、保存 `raw/` 数据。
  - `Experiment_Name_plot.py`：离线加载数据、分析、绘图、保存 `results/`。
- Notebook 主要用于：
  - 交互式调试
  - 参数探索
  - 历史实验复现
- 新增实验或重构实验时，应同步检查：
  - `experiments/` 是否已有对应脚本/Notebook
  - `docs/` 是否已有实验说明
  - `README.md` 是否需要补充入口说明
- 迁移旧实验时保留稳定 ID、`data_type`、历史运行目录和已发布的 GUI/YAML 参数键。

## 实验代码生成 Skill

本仓库包含一个自定义 AI Skill：`.agents/skills/expcodegen/SKILL.md`。

用途：根据自然语言实验方案，自动生成带 `# %%` Cell 结构的 Python 实验脚本，并遵循本仓库的数据采集/分析分离约定。

新增、后续修改或修复正式 Python 实验时均使用 `expcodegen`，不要求用户重复点名。
行为变化须在同一次任务中同步 `docs/` 实验介绍、受影响正文和 GUI 简介（含已有目录覆盖值），不能只追加更新日志。
实验代码始终按职责模块化，采集与分析入口负责编排，不把所有业务写进单个文件。
新增正式实验默认配套「运行 / 历史」双 Tab GUI；新建或整体重构 GUI 时询问是否构建可勾选、带依赖的交互流程图，已有明确选择不重复问。具体规则和模板见 Skill。
测试后删除本次临时测试脚本，保留正式回归测试和原有测试文件。

调用时会重点参考：

- `params/devices.yaml`
- `params/mapping.yaml`
- `params/safety_limits.yaml`
- `docs/*.md`

## 实验架构图生成 Skill

本仓库包含一个自定义 AI Skill：`.agents/skills/arcgen/SKILL.md`。

用途：根据仓库真实实现为单个实验生成 Archify 采集流程架构图（`GUI/frontend/public/architecture/<experiment-id>.json` 与 `.html`，showcase 质量），并在实验专属页面缺少"架构"Tab 时按 MxY 页面模式接线。调研范围固定为五类事实：扫描参数、仪器接入、仪器互连、采集数据、分析流程。图规格生成、校验与交付强制通过 `archify` skill 执行，本 skill 只补充实验领域调研与本仓库踩坑约束。

## 文档维护要求

- `README.md` 应反映当前真实的实验入口、模块能力和目录结构。
- `AGENTS.md` 与 `CLAUDE.md` 的内容应基本一致，只保留与各自 Agent 入口相关的差异。
- 若新增了实验脚本但没有新增文档，应在修改说明中明确指出缺失项，而不是假装文档已经存在。
- 对历史内容请优先增量更新；`docs/old/` 用于归档，不要把它误写成当前主入口。
- GUI 启动方式变化时同步根目录与 `GUI/` 下的 README；实验清单变化时同步迁移清单，避免在多个文档重复维护实验数量。
- 根目录 `AGENTS.md`、`CLAUDE.md` 与 `GUI/AGENTS.md` 纳入版本管理并同步到 GitHub；`.agents/`、`.claude/` 等本地 Skill 与配置继续忽略。本检出的 `.git/info/exclude` 忽略 `docs/` 中未跟踪的文件，已跟踪文档仍正常提交；被忽略的文件需单独检查。

## 代码规范

- Python 绘图中的所有标注（坐标轴标签、图例、标题、注释等）使用**英文**。
- 新增或重构的 Python 实验图统一使用 `lab_workflows.plotting`；默认采用 `paper` 配置，只有明确制作 A0 海报时才使用 `a0_poster`，详见 `docs/plotting_style.md`。
- 其他代码注释、文档字符串、提交信息等使用**中文**。
- 新模式参数模型字段使用 `snake_case`，并用 `external_name` 显式声明稳定的 GUI/YAML 键；为兼容历史配置，外部键可继续使用 `SCAN_RANGE`、`FIXED_PARAMS.temperature` 等旧名称。
- 旧模式脚本仍将实验参数集中在 Cell 或脚本顶部并使用全大写命名，但 GUI 不应把“扫描所有全大写变量”作为新实验的参数来源。
- GUI 只展示强类型模型中显式声明且 `visible=True` 的参数。
- 正式实验用 `create_signal_generator()` / `create_lockin()` 根据当前映射选择型号；保留已发布的 `HF2_*` 参数键、历史变量与数据键，不用重命名破坏旧配置。
- 锁相型号切换前检查选件、频率、解调器和输入来源；MFLI 双主电压回采预检必须拒绝启动，不自动换输入、降频或回退设备。锁相输出缺少明确安全电压上下限时禁止写入。
- 同机与共享通道组切换须保留通道并整批保存；允许跨实验复用信号源通道，但同次实验中独立物理量占用同一通道必须在预检拒绝。连接失败不自动切换仪器。
- 信号源高阻使用 `INF`；`9.9E+37` 等查询标记经 `normalize_output_load()` 归一化，不作为普通阻抗写回。
- 所有输出量设置前调用 `validate_safety_limit()`。
- 扫描循环用 `try/finally` 包裹，确保异常时设备能恢复到安全状态。
- 新模式硬件工作流连接设备后使用 `lab_workflows.steps.synchronize_connected_clocks()`，按设备库配置同步并严格回读参考时钟。
- 安全收尾使用 `lab_workflows/steps/safety_shutdown.py`，保留实验明确要求保持的输出；不要用断开通信代替关闭危险输出。
- GUI 硬件任务复用全局队列与互斥，扫描和等待循环提供协作式取消检查点。
- 幅度扫描中使用 `set_amplitude()`，不要用 `setup_sine()` 反复重绘波形，以避免 Burst 模式退出和相位跳变。
- 离线分析脚本应优先读取 `data/<实验类型>/<时间戳>_<tag>/raw/` 中的原始数据，不应依赖在线仪器连接。

## 实验类型文档规范

`docs/*.md` 中的实验文档使用 YAML frontmatter 记录结构化信息。默认参数引用 GUI 实际保存的参数文件，正文提供链接，不再复制默认数值；历史运行参数以各自的 `experiment_config.yaml` 为准：

```yaml
---
title: 实验名称
type: experiment_type
scan_mode: point_by_point | continuous_ramp | nested_scan
experiment_id: stable-experiment-id
defaults_file: params/experiments/stable-experiment-id.yaml
mapping_keys:
  key_name:
    role: scan | fixed | detection
required_devices:
learned_notes:
---
```

## 单次实验运行目录约定

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

不同实验可以扩展自己的结果文件，但目录层次应尽量保持一致。

## 环境

- Python 版本要求：`>=3.10`
- 包名：`exp-agent`
- 虚拟环境：`agent_exp_env\`
- Python 可执行文件：`agent_exp_env\Scripts\python.exe`
- 激活虚拟环境：
  - PowerShell：`agent_exp_env\Scripts\Activate.ps1`
  - CMD：`agent_exp_env\Scripts\activate.bat`
- 不存在虚拟环境时先执行：`python -m venv agent_exp_env`；已有环境直接复用。
- 安装本地包（可编辑模式）：`agent_exp_env\Scripts\python.exe -m pip install -e .`
- 依赖检查：`agent_exp_env\Scripts\python.exe -m pip check`
- 导出依赖：`agent_exp_env\Scripts\python.exe -m pip freeze > requirements.txt`；保存前将本项目的可编辑安装条目规范为 `-e .`，移除机器路径和带凭据的地址。
- GUI 前端需要 Node.js / npm；当前 Vite 的 Node 版本要求为 `^20.19.0 || >=22.12.0`，升级依赖后以锁文件中的 `engines` 为准。

## GUI 安装与启动

在仓库根目录安装后端依赖，再按前端锁文件安装：

```powershell
.\agent_exp_env\Scripts\python.exe -m pip install -r GUI\requirements.txt
Set-Location GUI\frontend
npm ci
Set-Location ..\..
.\GUI\start.ps1
```

- 浏览器默认访问 `http://127.0.0.1:5173`；Vite 将 `/api` 请求代理到 `http://127.0.0.1:8000`。
- 安装后可双击根目录 `start_gui.bat` 启动并自动打开浏览器；启动器检查依赖与端口，失败时不杀进程或自动换端口，也不自动安装依赖。
- `GUI/start.ps1` 在独立窗口启动后端，在当前窗口运行 Vite。当前窗口按 Ctrl+C 只停止 Vite；后端需要在自己的窗口停止或关闭。
- 开发模式不要求 `npm run build`。生成 `GUI/frontend/dist/` 后，也可只启动后端并在 8000 端口访问构建后的页面。
- 后端没有启用热重载；修改后端代码后需要重启对应进程。

## 验证与硬件边界

- 自动化验证使用假仪器或依赖注入，不得默认连接真实硬件；不要把 `experiments/quick_test_scan.py` 等采集脚本当作无硬件测试。
- 修改正式实验时运行 `agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root .`，新增实验还需按 Skill 执行 `--delivery` 验证。
- 对修改的 Python 文件运行 `agent_exp_env\Scripts\python.exe -m py_compile <文件>`，并运行受影响的现有回归测试。例如 XY 正弦模式检查：`agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode`。
- 仪器、映射、时钟和锁相 API 回归：`agent_exp_env\Scripts\python.exe -m unittest signal_generator.test_interfaces lab_workflows.test_instrument_config lab_workflows.test_output_policies lab_workflows.test_clock_sync lab_workflows.test_lockin GUI.backend.test_instrument_discovery GUI.backend.test_lockin_api`；RF DirectAW 和静磁场分别运行 `lab_workflows.experiment_modules.rf_sensitivity_direct_aw.test_workflow`、`lab_workflows.experiment_modules.static_sensitivity.test_dc_scan`。
- 前端变更在 `GUI/frontend/` 运行 `node node_modules/typescript/bin/tsc --noEmit`、`npm run build` 和 `npm test`；后端 schema 变更还需按 `GUI/README.md` 重新生成 API 类型。
- 当前检出没有根目录 `tests/`，不要直接执行依赖该目录的发现命令。`.gitignore` 也忽略该目录；已有本地测试时先确认文件及使用的测试工具，再执行针对性测试。
- 文档变更核对引用路径、命令入口和 AGENTS/CLAUDE 一致性，不需要为文字修改新增测试脚本。

## 第三方依赖

- 仪器通信：`agent_exp_env\Scripts\python.exe -m pip install pyvisa pyvisa-py pyserial`
- TOPTICA 激光器与进度条由 `pip install -e .` 按 `pyproject.toml` 安装；当前固定 `toptica-lasersdk==3.3.0`，并要求 `tqdm>=4.67,<5`。
- HF2 / MFLI 锁相放大器：`agent_exp_env\Scripts\python.exe -m pip install zhinst`
- 数据处理与绘图：`agent_exp_env\Scripts\python.exe -m pip install numpy scipy matplotlib pyyaml jupyter`
- GUI 后端：`GUI/requirements.txt`；前端：`GUI/frontend/package.json` 与 `package-lock.json`。
- 根目录 `requirements.txt` 是历史环境快照；`pyproject.toml` 目前只声明上述激光 SDK 和进度条，重建环境还需安装对应的仪器、分析和 GUI 依赖。

## 额外说明

`sds_acquisition`、`signal_generator`、`lockin_amplifier`、`gs200`、`keithley_6221`、`tec_controller`、`toptica_laser`、`sensitivity_analysis` 等包位于 `src/` 下；`lab_workflows` 位于仓库根目录。它们均通过 `pip install -e .` 供实验脚本直接 `import` 使用，无需额外设置 `PYTHONPATH`。

<!-- CODEGRAPH_START -->
## CodeGraph

仓库根目录存在 `.codegraph/` 时，定位或理解代码应先使用 CodeGraph，再使用文本搜索或直接读取：

- **MCP 工具**：优先调用 `codegraph_explore`，查询中写明文件、符号或代码问题，获取源码、调用链和影响范围；工具延迟加载时先按名称发现。
- **命令行**：`codegraph explore "<符号名或问题>"`。
- 工具未收录目标文件或提示索引过期时，直接核对相应实际文件；配置、文档和索引未覆盖的内容使用 `rg` 与文件读取。

没有 `.codegraph/` 时跳过 CodeGraph；是否建立或重建索引由用户决定。
<!-- CODEGRAPH_END -->
