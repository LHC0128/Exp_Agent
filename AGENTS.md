# AGENTS.md

面向原子磁力仪日用实验系统的仓库约定。对话、注释、docstring、提交信息用简体中文；Python 图中文字用英文。

## 工作边界

- 需求有歧义时先提问，不替用户补全实验意图。
- 工作区常有用户或其他代理的未提交改动：区分本轮与已有改动，只动本轮范围。删除或移动前核实绝对路径并备份；正式回归不因清理而删除。
- 需用户逐次授权：`lab_client run` 真实实验与其 `cancel`、输出类动作、直连仪器（调试窗口、运行目录环境脚本）、`tools/release_to_lab.ps1` 发布、改真实配置（`params/` 设备库/映射/安全限值/实验默认参数）、安装/升级依赖、启停服务、提交/推送、远程发 Issue、启用 Git hook。`cancel` 只用于本次授权启动的实验，不得取消用户自己在 GUI 启动的实验；发现异常立即报告请用户取消。Skill 不授予这些权限。
- 常设授权，不逐次征得同意：`lab_client status`/`show`/`device-read`、**上机前设备连通性自检 `python tools/check_devices.py`（只连接并在结束时断开，不改任何输出）**、离线分析、假仪器测试、按绝对路径读写运行目录 `data/`（含写回分析产物、整理历史运行目录；删除或移动前按上条核实并备份）。`tools/lab_client.py` 只对 `run` 强制 `--allow`/`--allow-param`，只读子命令不被工具拒绝；开始与结束仍告知用户，退出码 2 不绕过、退出码 3 立即请用户核查。细则见[开发定位](.agents/project-guides/development.md)。
- 日常解释器 `agent_exp_env\Scripts\python.exe`（Python `>=3.10`），直接复用，不升级或覆盖；隔离开发安装与依赖版本见 [README](README.md)。
- 跨模块拆分先说明目标接口、调用方影响、兼容和验证并获确认，再一次迁移一个可验证切片。
- 日常小改免 checkpoint：同时满足「限于单文件或单模块、无跨实验影响、无共享接口变更、可一次验收」四条即不建。命中跨模块、多代理并行写、一轮做不完验收（需多次改动或多次跑测试）、可能中断续接、改共享接口或安全/时序路径任一条，按[协作与检查点](.agents/project-guides/collaboration.md)建 checkpoint。
- 本文件随仓库提交；`GUI/AGENTS.md`、`.agents/`、`.codex/`、`.vscode/` 及退役协作资产仅本地维护，不上传 GitHub，也不改忽略规则来提交它们。

## 后台任务与等待

- 长任务（扫描、温控稳定、整套测试、批量分析）默认转后台运行，结束回合、等完成通知再继续；后台任务记录 job ID，有结果通知再取输出。不 busy-poll、不用 sleep 或反复追问当进度、不重复启动已存在的任务。
- 转后台前确认三条：完成或失败必有通知；失败路径已由代码兜底（`try/finally` 与 `safety_shutdown.py`，不依赖代理在场）；产物落盘可核验。任一不满足就不转后台，先补兜底或保持在场。
- 启动时一次说清跑什么、预计多久、怎么取消、失败会怎样；结束回合前写明唤醒后的核验项：退出码、产物、安全状态（输出与温控已恢复）。核验不过按失败处理，不当通过。
- 必须保持在场的例外只有四类：真实仪器异常窗口且无代码兜底、用户明确要求盯着、通知链路不可靠（会话可能被关闭）、需要中途判断的检查点。走例外要在回复里写明理由，不默认陪着聊。
- 被唤醒后先核验再汇报；失败如实报告并请用户决定，不自动重试；不再相关的后台任务主动终止。
- 禁止「陪聊式等待」：用对话代替监控、每隔几秒问一句、把进度播报当工作；也禁止「启动即失联」：没有通知机制的运行不算可以离开。
- 需要周期性进展时用定时任务或低频查询兜底，并预先声明超时后的动作（查状态、请用户或停止）。

## 提交与发布

- 主干一条提交对应一个可验收单元。同一时段内的错别字、文案、颜色、日志措辞、单行修补用 `git commit --fixup=<目标提交>` 记在原提交下，发布前 `git rebase -i --autosquash` 压进去；未推送的连续小改也可 `--amend`。不给一句话改动单开一条主干提交。
- 发布节奏对齐提交：攒到一个可验收的改动批次再跑 `release_to_lab.ps1`，不为一处小改单独发布一次（每次发布都会停/启 GUI、打标签）。
- 主干只留真实改动：`params/` 的界面改动由发布脚本回存成开发目录 master 上的一条提交并丢弃运行目录本地改动，运行目录不提交、不合并，lab 始终是 master 的纯快进。
- 推送与发布分离：发布前 master 必须已推送到 origin（脚本比对开发目录 HEAD 与运行目录刚拉到的 `origin/master`，不一致即停止）；推送仍按需授权，不在发布脚本里自动执行。

## 探索档与正式档

- 代码三档存放：一次性观察放 `.cache/`；新数据来了还会重跑的分析/对比与研究记录放 `scripts/<data_type>/`（进 git，约定与索引见 [scripts/README.md](scripts/README.md)，写新分析前先查索引，改完跑 `python -m tools.gen_script_index`）；给别人用或进论文升正式档。运行目录 `data/` 下不新增代码，`python -m tools.audit_data_tree` 列出违例。
- 探索档：notebook、`.cache/` 或 `scripts/` 下脚本 + 运行目录数据。不进注册表、不接 GUI、不写 `docs/experiments/`、不跑验证器 `--delivery`、单人探索不建 checkpoint（多代理并行写除外）。
- 正式档：模块化 + `experiments/` 薄入口、注册接线、运行/历史双 Tab GUI 与共享流程图、`docs/experiments/` 正文与简介同步、假仪器与 GUI/API 验收；细则见[实验契约](.agents/project-guides/experiments.md)与 `expcodegen`。
- 升级触发条件，满足任一条即转正式档并补齐产物，不由代理自行判断：跨天或跨样品重复运行同一脚本（探索期内连续调参重跑不计）；交给其他同学使用；数据进论文或正式报告。
- 探索档仍守的物理与数据红线：输出量经 `validate_safety_limit()` 按 `params/safety_limits.yaml` 校验；扫描/输出生命周期 `try/finally` 安全收尾并复用 `lab_workflows/steps/safety_shutdown.py`；普通气室六步时序、关温控窗口内不写盘；真实仪器只在带 `.lab_runtime` 的运行目录连接（`hardware_gate`）；数据路径经 `lab_workflows.common.data_root()`。
- 探索档不是降低安全要求，只是把文书义务推迟到需要复现时。「架构与兼容」「代码风格」的模块化与文档要求针对正式档，「硬件安全红线」两档同样适用。

## 架构与兼容

- 模块化单体，依赖方向：GUI/实验入口 → `lab_workflows` → `src`；核心不反向导入 GUI，驱动不依赖具体实验。
- `src/` 管型号驱动/通信；`lab_workflows/steps/` 管通用硬件步骤与安全恢复；`analysis/`、`plotting/` 只做离线计算/绘图，不连接仪器。
- 实验内聚 `lab_workflows/experiment_modules/<模块>/`，`experiments/` 保持薄入口；跨实验复用公共能力，不依赖另一实验的私有状态。
- 正式实验清单和执行模式以 `lab_workflows/experiments/registry.py` 为准，新模式从模块 `definition.py` 读起。
- 兼容：保留稳定实验 ID、`data_type`、公开导入、GUI/YAML 键与数据格式，代码继续能读历史运行，必要时留薄兼容入口。
- 重构保持输出限值、温控门控、分组/配对、取消、安全收尾和部分数据抢救的行为不变，相关安全测试随之保留。

## 代码风格

- 校验放在系统边界：GUI/API 输入、YAML 与参数文件、仪器回读、外部数据文件；内部调用按契约直接使用参数，异常原样上抛给调用方处理。
- 只写需求要求的分支；兼容入口按「架构与兼容」保留，其余旧路径直接替换。
- 「硬件安全红线」中的限值校验、`try/finally` 安全收尾与恢复属于必需边界，始终保留。
- 注释、docstring 和报告只写结论与依据，用肯定句直述；不确定项集中列为“待查”。

## 硬件安全红线

- 输出量写入前经 `validate_safety_limit()` 按 `params/safety_limits.yaml` 校验；锁相输出没有明确安全电压上下限时不写入。
- 扫描/输出生命周期用 `try/finally`，安全收尾复用 `lab_workflows/steps/safety_shutdown.py`，保留实验明确要求保持的输出；断开通信不等于关闭危险输出。
- GUI 硬件任务走全局队列/互斥，扫描和等待有协作式取消检查点；取消或退出时等真实安全收尾完成后再释放硬件锁。
- 正式实验用 `create_signal_generator()` / `create_lockin()` 选当前映射型号，保留 HF2 已发布参数、变量和数据键。连接或预检失败时停止并报告，不自动换设备、输入、频率或降低安全要求。
- 锁相型号切换先检查选件、频率、解调器、输入来源；MFLI 双主电压回采在预检拒绝。
- 同机/共享通道组切换保留通道并整批保存；同次实验中独立物理量占同一通道在预检拒绝，跨实验可复用通道。
- 信号源高阻写 `INF`；查询返回的 `9.9E+37` 等标记经 `normalize_output_load()` 归一化，不当普通阻抗写回。
- 新模式连接后调用 `lab_workflows.steps.synchronize_connected_clocks()`：先全部写入再统一回读参考时钟并严格校验；时钟同步不设置输出负载。
- 幅度扫描用 `set_amplitude()`；`setup_sine()` 会重绘波形、退出 Burst 并造成相位跳变。
- 真实仪器只在运行目录连接：打开 VISA/LabOne 通信前调用 `hardware_gate.require_hardware_access()`，放行依据是仓库根 `.lab_runtime` 记录的路径与该目录一致。开发目录保持无标记，门控拒绝即停止并报告。
- 新增通信入口（驱动 `connect`、恢复连接、设备发现）先接门控，再登记到 `src/hardware_gate/test_gate.py` 的清单；AST 测试拦截未登记入口。
- 可选设备连接遇到 `HardwareAccessDenied` 原样上抛，与“缺设备继续运行”分开处理。

## 验证

- 测试只用假仪器/依赖注入，不连接硬件；采集脚本（如 `quick_test_scan`）不是无硬件测试。
- 默认只跑[验证约定](.agents/project-guides/validation.md)定义的最小测试集，扩大范围先问用户；改动输出、安全、时序或保存时，最小测试集包含覆盖该安全路径的假仪器用例。只报告实际执行的验证，未验证的硬件行为如实列出。公共安全契约已固定在 `python -m tools.check` 的节点里，跑该入口即覆盖，不再逐次挑选或征得同意；具体实验的业务回归仍按影响定向选择。

## 按触发条件读取

| 任务 | 先读 |
| --- | --- |
| 定位实现、环境、文档 | [开发定位与环境](.agents/project-guides/development.md) |
| 正式实验；仪器、映射、时钟、锁相；参数/数据格式 | 正式实验加载 `expcodegen`；读[实验契约](.agents/project-guides/experiments.md)、[验证约定](.agents/project-guides/validation.md) |
| 普通气室采集；改采集、温控、分组/配对、保存或数据抢救 | [实验契约](.agents/project-guides/experiments.md)「普通气室采集」、expcodegen [共享步骤](.agents/skills/expcodegen/references/shared-steps.md) |
| GUI | [GUI/AGENTS.md](GUI/AGENTS.md)、[GUI/README.md](GUI/README.md) |
| Skill 与协作资产 | [.agents/README.md](.agents/README.md) |
| 命中建 checkpoint 判据（跨模块、多代理并行写、一轮做不完验收、中断续接、改共享接口或安全/时序路径）；日常小改不读 | [协作与检查点](.agents/project-guides/collaboration.md) |
| 工作总结/周报 | [工作总结/agents.md](工作总结/agents.md) |

<!-- CODEGRAPH_START -->
## 代码检索

定位实现与枚举调用点用 `rg`：排除 `agent_exp_env/`、`old/`、`data/`、`results/`，行号以检索结果为准。评估跨模块影响面时手工追调用链，先看模块内 `preparation.py` / `workflow.py` 的封装层。CodeGraph 索引与 CLI 已于 2026-11-17 移除，不再使用其命令或配置。
<!-- CODEGRAPH_END -->
