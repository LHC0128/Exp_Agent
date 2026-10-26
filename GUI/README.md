# Bell-Bloom 实验控制台

本地 Web GUI，用于读取和设置实验仪器（包括 GS200 主磁场电流源和 TOPTICA DLC pro Probe 激光）、同步参考时钟、校准 Demod0，
以及从统一“实验中心”配置、预检、运行和重新分析正式 Python 实验。

总览页和实验页的架构图展示已撤下，实验页保留「运行 / 历史」主 Tab；运行页可执行流程图
和可编辑原图下载继续使用。原静态图及验收附件见[架构图归档](../docs/old/architecture/README.md)，
不再放入前端 `public/` 或构建产物，旧 `/architecture/` 地址不再提供原图。

## 安装

先按[仓库初次安装说明](../README.md#初次安装)安装本地包、仪器、分析与 GUI 依赖。
前端需要 Node.js / npm，完整依赖要求 `^22.22.2 || ^24.15.0 || >=26.0.0`，推荐 Node.js 24 LTS；
该范围同时考虑 Vite 与 jsdom。依赖版本由 `frontend/package-lock.json` 锁定。
开发/CI 的隔离安装及统一快检见[开发安装与无硬件检查](../README.md#开发安装与无硬件检查)，
不要在正在采集的环境中自动升级依赖。

```powershell
.\agent_exp_env\Scripts\python.exe -m pip install -r GUI\requirements.txt
Set-Location GUI\frontend
npm ci
```

`npm run build` 不再是必备步骤；`GUI\start.ps1` 会以 Vite dev 模式直接 serve 源码并提供 HMR。
只有在准备发布或不便启动 Vite dev（例如纯命令行环境）时才需要 `npm run build`。

## 启动

完成安装后，在文件资源管理器中双击仓库根目录的 **`start_gui.bat`**。
它会配置 Tailscale 私人 HTTPS 入口、启动前后端，并在 Vite 就绪后自动打开默认浏览器。
入口使用自身目录定位仓库，支持从其他工作目录运行；PowerShell 执行策略仅对该进程生效，
不修改系统设置，不自动安装依赖。

也可从仓库根目录用命令行启动；默认不自动打开浏览器，附加 `-OpenBrowser` 可开启：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\start.ps1
# 自动打开浏览器
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\start.ps1 -OpenBrowser
# 仅启动本机服务，不查询或修改 Tailscale
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\start.ps1 -LocalOnly -OpenBrowser
```

启动器会同时拉起两个进程：

- **FastAPI 后端** `http://127.0.0.1:8000`，提供 `/api/`；存在 `frontend/dist/` 时也提供构建后的页面
- **Vite dev server** `http://127.0.0.1:5173`，serve 前端源码并把 `/api` 转发到 FastAPI

页面地址为 `http://127.0.0.1:5173`。修改前端源码后，Vite 会自动 HMR 推送变更到浏览器，
不需要再 build。启动器在独立窗口运行后端，当前窗口按 Ctrl+C 只停止 Vite；
关闭或刷新浏览器不会停止后台实验。后端应在自己的窗口按 Ctrl+C 并等待收尾，不直接关闭窗口。
后端没有启用自动热重载；修改 Python 代码后可在总览页点击“重启后端”，该按钮只在没有任何
采集、排队任务或分析运行时可用，会保留 Vite 和当前页面，但清空后端内存中的任务状态。
正常停止的保证与限制见[正常停止与安全边界](#正常停止与安全边界)。

启动器检查虚拟环境 Python、`node_modules`、`npm.cmd` 和 8000 / 5173 端口。
缺失依赖或端口占用时直接报错，不会自动安装、结束已有进程或改用其他端口。
默认还检查 Tailscale 安装与登录状态、读取当前设备的域名并配置私人 Serve；
Serve 配置失败或回读不匹配时不启动前后端；同一 HTTPS 入口已有其他路径、443 转发或公网 Funnel 配置时会报错并保留原配置。
后端 20 秒内未监听时取消前端启动；前端失败时返回非零退出码。
双击失败后窗口保留错误并等待按键，已启动的后端需手动停止。
重复双击会提示端口占用，请先停止旧服务。

启动脚本语法检查（在仓库根目录运行，不启动服务）：

```powershell
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PWD 'GUI\start.ps1'), [ref]$tokens, [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
```

启动器的无硬件回归覆盖域名白名单、重复配置、本机模式、端口冲突、Tailscale 失败、
转发冲突、私人 HTTPS 回读以及前端退出码；所有外部命令都使用替身：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\test_start.ps1
```

如需手动分别启动：

```powershell
# 终端 1：FastAPI 后端
Set-Location GUI
..\agent_exp_env\Scripts\python.exe -m backend

# 终端 2：Vite dev server
Set-Location GUI\frontend
npm run dev
```

只运行后端时，先在 `GUI/frontend/` 执行 `npm run build`，再访问
`http://127.0.0.1:8000`；此方式需要重新构建才能显示前端源码更新。

### 正常停止与安全边界

以下行为适用于上述启动器和 `python -m backend` 入口：

- 关闭或刷新网页、停止 Vite，只影响访问，不停止后台任务。停止一个分步会话仍只收尾该会话，
  不取消其他排队任务；需要停止后端时，在后端窗口按 Ctrl+C。
- 后端收到正常退出请求后，停止接纳新的采集、分析、工具任务和短硬件操作；取消尚未开始的
  排队项，向运行任务请求协作停止。新任务或短操作被拒绝时返回 HTTP 503，
  `X-Error-Code: shutting_down`；读取已有任务状态不因此改成“硬件忙”。
- 等待真实任务线程、子进程与已开始的短操作结束，沿用各实验既有的温控恢复、部分数据保存
  和输出保持策略。不能立即取消的工具先完成当前操作；重复 Ctrl+C 不强杀、不跳过收尾。
  等待较长时持续报告状态，没有“等够时长就视为安全”的超时。
- 优先在 GUI 正常停止实验或独立功能，再停止服务。收尾失败会照实记录；进程退出、取消请求
  发出或界面断连都不代表仪器已确认安全。保持型输出和已启动的独立任意波仍需按对应模块停止，
  不因退出后台就统一关闭所有输出。typed 子进程在取消时非零退出会保留取消状态并告警，
  同时给出退出码；正常取消也可能非零，不能仅凭退出码认定失败或已安全。
- 直接关闭终端、任务管理器强杀、系统崩溃和断电无法保证软件收尾。不要用反复 Ctrl+C
  代替查看收尾日志和检查设备。当前终端信号由受控入口消费，完成收尾后正常返回；
  不依赖原框架的 KeyboardInterrupt 或信号退出码判断仪器状态。

开发说明：受控入口按 Uvicorn 0.51.0 的信号捕获和 `_serve` / `shutdown` 顺序验证，
升级后需重跑 `GUI/backend/test_server_runtime.py` 的无硬件契约；直接使用 Uvicorn CLI
绕过该入口不具备长连接等待前封口及重复 Ctrl+C 保护。运行中的旧后端不会因修改源码自动更新，
应在当前实验正常结束后再按上述方式重启加载。

### 笔记本浏览器访问

实验电脑和笔记本均需安装 Tailscale 并加入同一个私人网络。默认一键启动从
`tailscale status --json` 读取实验电脑的域名，通过
`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` 放行该域名，并后台开启 HTTPS Serve。
前端就绪后，在笔记本自己的浏览器打开启动窗口显示的 `https://<本机的 Tailscale 域名>`。
两个服务仍监听 `127.0.0.1`，网页和 `/api` 共用入口；局域网和外网使用同一个地址。
笔记本需保持 Tailscale 连接，实验电脑需保持 GUI 服务运行。

**Serve 配置在实验电脑的 PowerShell 中执行**，由 Tailscale 客户端保存。
首次若需要启用 HTTPS，命令会显示设置链接，在 Tailscale 官网账户页面完成一次设置；
需要账户有相应权限。若本机命令提示访问被拒绝，在管理员 PowerShell 中重试。
账户设置流程见 [Tailscale Serve 官方说明](https://tailscale.com/docs/features/tailscale-serve)。

需要手动配置或核查时，在实验电脑执行：

```powershell
tailscale status
tailscale serve --bg --https=443 --set-path=/ http://127.0.0.1:5173
tailscale serve status
# 停止该 HTTPS 根路径的转发，保留其他路径
tailscale serve --https=443 --set-path=/ off
```

手动启动 Vite 时也需设置域名白名单；从 `GUI/frontend/` 执行：

```powershell
$tailscaleInfo = tailscale status --json | ConvertFrom-Json
$env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = $tailscaleInfo.Self.DNSName.TrimEnd('.')
npm run dev -- --strictPort
```

已运行的前端也可从 `GUI/frontend/.env` 读取同名变量（逗号分隔域名），修改后由 Vite 自动重载。
该本机配置不纳入 Git；默认一键启动仍以实时读取的 Tailscale 域名为准。

Serve 在后台保存配置，退出 GUI 或重启电脑不会自动清除转发，但网页仍依赖前后端服务运行。
启动器只设置私人 Serve，不启用公网 Funnel；GUI 没有独立登录，应限制私人网络的访问权限。
`-LocalOnly` 不修改已有 Serve 配置，如需停止已有远程入口，先执行上面的 `off` 命令。

## 布局与任务导航

- 左上角可“隐藏导航／显示导航”，桌面选择保存在当前浏览器；窄屏使用覆盖菜单，可用 Esc 关闭。
- 内容区随窗口宽度伸缩，实验中心、仪器与功能卡片自动换列；实验流程保留采集／分析逻辑分区，窄容器自动换行，不通过放大字体或拉高卡片填满屏幕。
- 实验中心采用简洁入口卡：名称、分类与有任务时的状态，整张卡片可点击；介绍、采集／分析程序路径统一放在实验页默认收起的“实验说明”中，安全提示仍常显。管理模式保留介绍编辑、分类和归档操作；搜索仍匹配介绍，返回目录恢复本标签页的筛选与滚动位置。
- 顶部任务条随页面滚动保持可见，显示具体实验、进度与预计剩余时间，多任务可展开。“前往实验”只读恢复指定任务，不启动采集；普通运行、分步会话、排队、分析与对比任务均可定位，分步等待仍占用硬件锁。任务结束后显示可关闭的通知。
- 实验页和实验中心有未保存的实验参数、布局、分析覆盖值或实验信息时，离开前需要确认，刷新／关闭也有浏览器提示；同页任务跳转不重置草稿。确认离开不会保存草稿，也不会停止实验。后端断连时明确标记任务和仪器状态未知，保留最后一次任务摘要，不用旧状态宣称仪器正常。

## 基本操作

日常仪器选择与控制的推荐顺序：

1. 进入“仪器控制”的“物理量映射”标签，统一选择 USB/IP 设备；设备切换联动同机组及共享通道组并保留通道，目标通道不足时拒绝切换，整批保存。
2. 在“设备库”中维护型号、Resource、连接参数、能力、参考时钟和网络扫描地址（`devices.yaml` 顶层 `network_resources`）；先保存候选地址，再扫描确认型号与序列号并逐台入库。VISA 扫描只执行资源枚举与 `*IDN?` 查询。信号源用途卡片内的 Resource 下拉选择实际仪器，并联动同机/共享通道组、保留通道；单张“锁相放大器”卡片统一选择 HF2 或 MFLI。两类选择均为草稿，点击“保存设备库”后与设备配置一起生效；硬件任务期间可编辑草稿，结束后才可保存。信号源通道允许在不同实验中复用，同一次实验独立使用的物理量占用同一通道时预检拒绝启动。
3. “设备控制”以多列网格展示全部物理量，可逐项“读取”，也可点击“连接并读取全部”；页面打开时不会自动连接硬件。
4. 信号发生器首行按三栏显示：DC 为“波形 / 偏置 / V（固定）”，交流为“波形 / 幅度 / 单位”，频率、偏置和相位位于下一行。正弦、方波和斜波可选 Vpp / Vrms / dBm；dBm 需先在高级区设置数值负载，高阻下禁用。脉冲和任意波暂只开放 Vpp。波形细节、负载、调制和 Burst 仍位于高级区；高阻回读 `9.9E+37` 显示并提交为 `INF`。`rf_coil` 作为历史别名不单独显示，统一由“Y方向磁场”面板控制。
5. 点击“应用并回读”才写入仪器；幅度数值按所选单位解释，偏置始终为 V。后端先换算为 Vpp，按当前 mapping key 的 `params/safety_limits.yaml` 校验完整电压包络，再写入并恢复所选幅度单位，以实际回读值更新页面。单位回读未知时仅允许关闭输出或恢复 Vpp 后重新读取。
6. 在“功能模块 → Z 任意波控制”选择闭环冻结结果，离线查看 V–t 图，再选择“仅加载，保持关闭”或“加载并启动”。可联动时序信号2并编辑触发参数；Z 面板提供快捷入口。详见 [任意波控制说明](../docs/instruments/signal_generator.md#gui-z-任意波控制)。
7. 实验结束后，先按需要执行模块的“停止”，再分别停止 Vite 与后端。仅停止服务不会自动关闭已启动的任意波。

## 分步流程契约与兼容

- 每个实验的 `stages.definition()` 显式提供 `run_step_id`、`finish_step_id`；公共页面不再配置角色或套用 A12/A14。角色编号保持各实验原值，运行角色用于关联本轮目录，不替代目录封存与分析资格校验。
- 流程 API 校验节点唯一、依赖存在且同列有序、角色有效且结束节点无前置。显式选择仍走各实验既有分派，不自动补选或引入通用调度器。
- 旧服务缺少角色、编辑关系或影响表时明确提示并阻止新的分步执行／参数应用；仍恢复已有任务和会话，执行中可取消、空闲时可通过独立结束接口安全收尾。先安全结束，再由操作者重启后端加载新代码，不自动重启。
- `display_parameters` 只决定编辑位置；`parameter_impacts` 来自实际失效规则。共享参数保留最早失效起点，多参数影响合并，草稿不改执行记录，应用后显式重跑；历史目录分析草稿与下一次实验隔离。
- “执行完成”只表示步骤完成，不代表科学质量合格。“检查与质量说明”可能包含诊断或真实门槛；是否阻断后继以实际状态和后端校验为准，不把诊断升级为新的硬件条件。质量失败仍展示执行记录中已有的诊断产物。
- 产物链接只来自本轮执行记录的 `artifacts` 或有指纹的 `files`；预期文件名不是生成证据。旧记录缺清单时明确说明，不补造下载、不回写历史元数据，也不回退 `latest`。SSE 只接纳同会话、不倒退的 revision，命令回执只确认匹配的当前请求。

## 多控制 Scale RF 实验

实验中心的 `rf-control-scale-sensitivity` 复用 RF DirectAW 接线，控制波形读取后勾选，不注入人为噪声。
A13 通过起点、终点和点数设置扫描倍率，支持降序、零和负数，不要求包含 1；各控制按名称手动填写补偿倍率，未设为 1。
列表与 RF DirectAW、已知噪声实验统一为「波形名称｜勾选框｜补偿倍率」三列；两者的补偿在 A09 编辑，三项均支持多选，各控制独立保留补偿。系数按名称绑定，可在勾选前预填，波形与系数一起移动分类；旧历史缺系数时填回单位补偿，不继承当前草稿中的额外补偿。
实际 XY 包络乘扫描与补偿两种倍率，图横轴仍显示扫描倍率，结果表另列补偿和实际总倍率；无有效倍率 1 参考时保留绝对指标、跳过归一化。电压安全与校相质量检查不变。
每个控制 × Scale 先独立执行 Demod0、当前倍率 XY 及 RF 解调器校相；RF 相位可逐点扫描，也可在 A13 关闭扫描后全部组合共用一个手动值。
其余校相不跨点继承，再采幅度响应、可选零 RF X 噪声和必测 R 扫频；设备连接、时钟及固定条件共享。
运行/历史双 Tab 提供采集/分析两列交互流程图、节点参数、依赖检查、会话恢复与取消。
A13 是逐点完整校相/测量节点，校相参数集中在该节点，不再单独执行外层 A08–A12。
支持历史填回、重新分析和 PNG/PDF/CSV 下载；新 v2 逐点校相与历史 v1 固定相位明确区分，旧数据不改写。
四项比较依次为 DirectAW 色散拟合绝对斜率、
色散等效线宽、洛伦兹中心频率和设定公共频带的灵敏度谱中位数；图例保留所选波形原名。
HWHM 不作为统计频带硬截止，超出时仅提示；旧局部拟合点数参数仅兼容读取，不再显示或参与拟合。
B03 支持保守高值筛选或保留全部记录，频带/策略变更从 B03 失效；结果表显示剔除/总条数，
逐条评分、剔除清单和未剔除指标均保留。数据目录另有只读诊断入口，区分整组偏差与单次异常，不自动删组。
下一次实验与具体历史目录的分析草稿隔离。
见[实验说明](../docs/experiments/rf_control_scale_sensitivity.md)和[独立默认参数](../params/experiments/rf-control-scale-sensitivity.yaml)。

## BB Pump 占空比 Burst 扫描

`/experiments/bb-duty-scan-burst` 提供 8 个采集／3 个独立分析节点及运行／历史双 Tab。
节点内编辑参数，顶部保存布局、派生预览、预检与完整运行；共享占空比和暗段参数同步，草稿应用后显式重跑。
分步等待保持连接、输出与硬件锁；历史仅独立覆盖暗段拟合时长，支持快照填回、重分析和可编辑原图下载。
每帧恢复温控后保存，结束停止 SDS、关闭辅助场、恢复温控，仅断开 TEC；保留主场、光功率、Pump 与 Time_sequence 的输出/Burst/SYNC。
新接口需先安全结束已有会话，再由操作者重启后端加载。详见[实验说明](../docs/experiments/bb_duty_scan_burst.md)。

## 固定 XY 的 RF 频率响应

`rf-sensitivity-constxy-frequency` 已接入 11 采集／3 独立分析节点的运行／历史双 Tab。节点内编辑与共享参数同步，顶部保存参数／布局、预览、预检和完整运行；草稿应用后显式重跑，分步等待保持输出与硬件锁。历史目录的分析覆盖值独立，支持填回、重分析与原图下载；可空拟合参数设置后可“恢复自动”。

0 Hz 背景只关闭 Z，XY 保持开启；双主电压物理回环仅允许 HF2，选择 MFLI 时连接前拒绝。逐频点恢复温控后保存，结束按统一策略关闭 XY／Z、恢复温控，保留主场、光功率、Pump／时序和锁相设置，仅断开 TEC。新接口需安全结束已有会话后重启后端加载；迁移不会自动操作服务。详见[实验说明](../docs/experiments/rf_sensitivity_constxy_frequency.md)。

## Z 场带宽分步运行

`/experiments/z-field-bandwidth` 提供运行／历史双 Tab、10 个采集／3 个独立分析节点和可编辑原图下载。
50 个可见参数在节点内编辑，保存布局、预览、预检、完整运行与日志在流程顶部；系统稳定和温控恢复等待在校相／扫描节点共享，草稿应用后须显式重跑。
固定小信号 Z 连续正弦扫频，0 Hz 为 Z OFF 背景，XY 保持关闭；双主电压物理回环只允许 HF2，MFLI 连接前拒绝。
每频点恢复 5 V DC + ON 后保存，取消／恢复失败抢救已读 Y 和 R；重跑新建目录。A09 收尾封存后再次采集须重做 A06–A08，A10 无前置结束并释放硬件锁。
结束关闭 XYZ，保持已执行主场、光功率、Pump、时序及锁相设置，只断开 TEC。历史覆盖值按目录隔离，不修改采集快照或下一次实验；早期标量 R 不冒充矢量响应。
见[实验说明](../docs/experiments/z_field_bandwidth.md)和[默认参数](../params/experiments/z-field-bandwidth.yaml)。

## BB Pump 功率 Burst 扫描

`/experiments/bb-power-scan-burst` 提供运行／历史双 Tab、8 采集／3 独立分析节点和可编辑原图下载。
33 个可见参数在节点内编辑；顶部保存布局、派生预览、预检、完整运行与任务状态。暗弛豫窗口在激励与下一次分析设置间共享，草稿应用后显式重跑。
分步等待保持连接、输出与全局硬件锁；每帧恢复温控后保存。历史只覆盖所选目录的暗段拟合时长，不修改原始数据、采集快照或下一次实验。
按确认策略，正常结束、取消、异常及通信 EOF **均保留 Time_sequence 输出/Burst/SYNC 与最后写入的 Pump 电压**，恢复温控，只断开 TEC；结束会话不等于停止门控。
见[实验说明](../docs/experiments/bb_power_scan_burst.md)和[默认参数](../params/experiments/bb-power-scan-burst.yaml)。

## T1 标定

`/experiments/t1-calibration` 提供运行／历史双 Tab、8 采集／3 分析节点和可编辑流程图下载。
CH1 读取 PDB 差分，CH4 同步下降沿触发；不采集双 PD，不连接 GS200 或锁相。
22 个可见参数在节点内编辑，顶部保存布局、派生预览、预检、完整运行与任务状态；共享拟合界限同步，草稿应用后显式重跑。
每个尝试帧恢复温控后保存；X/Y/Z 在每轮采集后及结束时归零关闭，保留 Probe、Pump 载波和门控，只断开 TEC。
再次采集必须显式重跑 A04→A05→A06→A07；等待期间仍占全局硬件锁，取消与通信结束会安全收尾。
历史按实际快照填回，独立分析的拟合上下限按目录隔离；旧扫描缺波形时保留原 T1 序列和诊断，不虚构重拟合。
见[实验说明](../docs/experiments/T1_calibration.md)和[默认参数](../params/experiments/t1-calibration.yaml)。

## 前端结构

- `frontend/src/pages/`：路由页面，只编排页面级状态和 API 调用。
- `frontend/src/components/`：布局、表单、任务视图和仪器编辑器。
- `frontend/src/components/workflow/WorkflowPanel.tsx` 与 `workflow.css`：公共分步流程面板及原响应式样式；`NoiseSpectrumXY/` 原路径保留组件／类型导出和样式转发。面板继续复用已有参数表单控件，不在本次迁移重写控件。
- `frontend/src/pages/WorkflowExperimentPage.tsx`：运行／历史通用外壳，继续提供参数归一化、分析键映射和结果展示扩展；具体页面及既有接口不变。
- `frontend/src/types/api.ts`：与后端 Pydantic 模型对齐的 API 类型。
- `frontend/src/types/openapi.d.ts`：由 `npm run generate:api` 从后端 OpenAPI 自动生成，用于比对接口漂移，不直接手改。
- `frontend/src/App.tsx`：仅保留应用布局入口，不承载页面实现。

后端 schema 变更后，重新生成类型并核对：

```powershell
Set-Location GUI
..\agent_exp_env\Scripts\python.exe -m backend.openapi_dump > frontend\openapi.json
Set-Location GUI\frontend
npm run generate:api
```

前端测试使用 Vitest + Testing Library：

```powershell
Set-Location GUI\frontend
npm test
```

设备写入接口使用严格 Pydantic 请求模型，未知设置字段会返回验证错误；
设备回读使用按 `type` 区分的 GS200、DLC pro、信号发生器和示波器快照模型。
设备库的“扫描 USB / 网络 / 锁相放大器”包含 VISA 的 USB/网络资源、已有 VISA 地址、
`params/devices.yaml` 顶层 `network_resources` 中的指定网络目标，以及已配置 LabOne 数据服务器中的设备。
扫描只读取身份信息，显示序列号、连接地址、入库状态和失败原因。
同一设备的 USB 与网口连接按型号及序列号识别；加入新设备后需要保存设备库。
后端读取和保存 DG4000/DG900 时，会补全缺失的任意波与无限 Burst 能力字段，
保存后写入设备库；显式禁用的能力和自定义通道、点数限制保持原值，未知型号不自动补全。
在设备库的“网络扫描地址”按行编辑 VISA 资源并先保存，再主动扫描；清空列表会停止扫描
候选地址，已入库仪器仍参与扫描。`.31/.32/.33/.34` 已预置为 `192.168.124.*` 扫描候选，
确认型号与序列号后每台独立加入。旧扫描文件仅在新字段缺失时用于兼容迁移。
物理量映射的设备列表统一展示 USB/IP 仪器的型号、地址与序列号。更换设备会联动现有
同机组和共享通道组，保留通道分配；目标缺少所需通道时整组拒绝切换。两类组成员都填
物理量键，保存全部映射时由后端校验并原子写入，保存冲突保留当前草稿。
设备库中的信号源按映射用途及联动组显示卡片，Resource 下拉列出已入库的 USB/IP 实际仪器，显示型号、地址和可用序列号；选择会联动同机组和共享通道组并保留通道。未绑定的设备仍显示为设备库卡片。“设备连接地址”编辑选中仪器自身的配置，Resource 下拉则更换用途绑定，不覆盖原仪器地址。不同用途可选同一台信号源并跨实验共用通道；同一次实验独立使用的物理量占用同一通道时，预检拒绝启动。

主磁场 GS200 卡片也提供 Resource 下拉选择，只显示已入库的 GS200，不混入信号发生器或 Keithley 6221。网络设备 `9026D6597` 使用已验证可回读的命令端口 `TCPIP0::192.168.124.41::7655::SOCKET`，原 USB 设备仍保留为候选；点击“保存设备库”后才切换绑定，保留原映射的电流模式等字段。Socket 驱动设置 LF 收发终止符；连接或身份查询失败会释放会话。网页可访问只表示 HTTP 服务可用，仪器命令连接需另行验证。

加热信号（`Heat_Control`，CH1）与温度开关信号（`Temp_Switch`，CH2）属于 `temperature_control` 同机组，在设备库中合并为一张“加热信号 / 温度开关信号”卡片。两路共用一个 Resource 选择，点击“保存设备库”后一起切换实际仪器，保留 CH1/CH2 分配。

HF2 与 MFLI 合并为单张“锁相放大器”卡片，在卡片内选择全系统设备并显示其设备 ID、数据服务器地址和配置。信号源、主磁场 GS200 和锁相选择均先更新草稿，点击“保存设备库”后与设备配置一次提交，锁相保留解调器索引。页面提示未保存更改，保存冲突或失败保留草稿；硬件活动/排队期间可编辑草稿，保存按钮禁用。物理量映射页不再逐行选择锁相设备。详情见[仪器配置说明](../docs/instrument_mapping.md#gui-与-api)。

锁相扫描从数据服务器读取实际连接接口。当前 MFLI 的内置服务器报告 `PCIe`，它表示仪器内部连接，电脑仍通过网线访问 `192.168.124.109:8004`；独立 PC 上的服务器可报告 `1GbE` 或 `USB`。扫描只返回身份和接口信息，不自动改写已有设备配置。

“设备控制”提供两机共用的相位、解调频率、谐波、滤波与速率设置，以及 Signal / Aux 输出面板。解调 X/Y/R 和内置 Scope 采集复用全局队列、取消和波形预览，完整数据保存到 `data/LockinAcquisition/`。输出写入需要对应设备/通道的安全电压范围，未配置时仅允许回读和关闭主输出。双主电压回采实验选择 MFLI 时预检禁止启动，不自动改接线或回退设备。详见[锁相模块说明](../docs/instruments/lockin_amplifier.md)。

DG900 通道设置会等待仪器完成全部命令并检查 SCPI 错误队列后再回读；关闭调制时
只操作实际启用的类型，避免连续加热波形被不兼容的 PWM 状态命令干扰。
本轮新增计数器、截图、显式复位和 Pro 文件接口仅提供 Python 库入口，详见
[信号源接口与迁移说明](../docs/instruments/signal_generator.md#共同接口与旧-dg900-调用迁移)，未新增硬件功能面板。

“仪器控制”中的 GS200 模块只开放 `main_magnetic_field` 电流设定值（mA）和输出开关；
源模式、电流量程、限压和限流只读。电流范围从 `params/safety_limits.yaml` 读取，
当前为 `-10～10 mA`。输出从 OFF 切换到 ON 时，浏览器会再次显示目标电流并要求确认；
设备连接后的写入或回读发生异常时，后端按 `output_off_on_error` 尽力关闭输出。

TOPTICA DLC pro 模块开放激光电流、温度、PZT Scan Offset、扫描幅度和扫描启停，
扫描频率与 Laser Enabled 只读。Emission 使用独立高风险接口：远程 ON 默认由
`remote_emission_control_enabled=false` 禁止，开放后仍需现场安全勾选、输入控制器
编号和浏览器再次确认；OFF 无需确认但会强制回读。DLC pro 通信或回读异常时不自动
关光或回滚，界面会提示设备状态可能未知。完整说明见 `docs/instruments/toptica_dlc_pro.md`。

Keithley 6221 的 Hz 任意波 CSV/TXT 解析与标定换算逻辑位于
`lab_workflows/keithley_arb.py`（纯计算、无硬件），GUI 通过
`POST /api/tools/keithley-waveform-convert` 上传文件文本获得解析与换算结果；
标定实验 ID 通过 `CALIBRATION_EXPERIMENT_IDS` 别名集合兼容历史文件。

## 检查更新

导航“检查更新”页（`/api/updates/*`）集中检测软件环境并执行更新，分区块独立加载：

- **Git 仓库**：`git fetch` 后显示落后/领先提交数；仅在本地干净且可 fast-forward 时
  执行 `git pull --ff-only`，否则给出拒绝原因。
- **Python 依赖**：全 venv `pip list --outdated`，标注包来自哪个声明文件；
  勾选后 `pip install -U`，任务末尾自动运行 `pip check`。
- **前端依赖**：`GUI/frontend` 的 npm 过期包，勾选后安装 `@latest`。
- **全局 CLI 工具**：全局 npm 白名单包（codegraph 含其 MCP server、playwright cli、
  pi 本体、mineru）；更新 pi 后需重启 pi。
- **olcli 隔离环境**：`~/.local/olcli-ustc` 前缀内的 olcli-ustc、codex、pnpm，
  只用命令级 `npm_config_prefix`，不修改用户级 `.npmrc`。
- **AI Skills**：仅列出唯一入口 `.agents/skills` 的清单；退役备份不扫描，本地无版本源，仅展示。
- **工具链**：python / pip / node / npm / git 版本展示。

更新任务以后台 Job 运行且不占用硬件互斥队列，可与实验并行；更新请求只接受
最近一次检查结果中出现过的包名。更新 fastapi / uvicorn / pydantic 后需重启后端
（总览页“重启后端”），更新前端依赖后需重启 Vite，dist 部署需重新 `npm run build`。

## 任务进度

运行中的任务通过 `/api/jobs/{id}/events` SSE 增量推送进度；页面刷新恢复和任务结束后
只读取一次完整 Job 快照。`GET /api/jobs` 只返回不含事件与结果的摘要。任务对象可携带
`estimated_remaining_seconds`：typed workflow 子进程以 `__LAB_PROGRESS__ ` JSONL 行协议
上报结构化进度，采集实验按已完成点的累计平均耗时动态估算剩余时间；普通日志事件不覆盖
ETA，进入分析阶段或任务结束时显式清空。进度卡与全局任务横幅以 `MM:SS`（超过一小时为
`H:MM:SS`）显示“预计剩余”。Mx Y 最优控制 RF 频率响应专属页把任务状态保存在页面层：
切换“运行/历史”Tab 不丢失，重新进入页面时优先恢复本实验最新的活动任务，否则恢复最近
一次已开始的任务。后端仅保留
最近 100 个已完成、失败或取消的任务，运行中的任务不会被淘汰。实验原始数据和分析
结果保存在 `data/`，不受内存任务淘汰影响。

多个硬件任务不再冲突失败：后启动的任务进入排队状态，等前一个任务释放硬件后自动开始，
排队期间可取消。页面顶部会显示全局任务横幅；硬件占用期间，“功能模块”和实验页的
启动按钮、仪器控制页的读取按钮会自动禁用并提示。分析任务按 `(实验, 运行目录)` 去重，
同一目录不会并发分析。

### 本次运行目录与配置写入

强类型采集子进程通过同一 JSONL 协议的 `data.run_directory` 显式上报当前目录。父进程等待子进程退出和安全收尾后，检查目录位于本实验的数据目录下、配置文件存在且实验身份匹配，再自动分析。父子实验共用 `data_type` 时，按配置中的稳定实验 ID 筛选已上报目录；多个有效候选仍无法唯一确定时明确拒绝。未上报、目录无效或采集失败时明确报错，不扫描“最新目录”，也不回退分析历史运行。目录上报仅表示目录已初始化，不代表采集完成；分步会话的重跑仍可创建并上报新的独立目录。

运行配置及分析状态中的配置更新采用同目录唯一临时文件、关闭后原子替换；序列化、写入或替换失败保留原配置。该保证不等于跨进程读改写合并或断电事务，仍须遵守已有的硬件及同目录分析互斥。分析状态诊断写入失败记录日志，不覆盖原始分析异常。历史格式、采集参数、原始数据及手动选择历史目录分析的操作不变。

结构化错误通过 `X-Error-Code` 响应头区分（`revision_conflict`、`hardware_busy`、
`analysis_running`、`no_analyzer` 等），前端按错误码分支处理而不是匹配中文文案。

## 共享模块

实验脚本不依赖 GUI，可以直接复用平级目录中的工作流：

```python
from lab_workflows.clock_sync import synchronize_clocks
from lab_workflows.phase_calibration import calibrate_demod0_safely
from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.steps import calibrate_demod_phase, DirectAWStrategy
```

静磁场默认参数统一位于 `params/experiments/static-sensitivity.yaml`，正式实现为
`lab_workflows/experiment_modules/static_sensitivity/`；旧 `params/static_sensitivity.yaml` 仅留档。
色散采用 Z 直流逐点扫描、多次 X/Y/R 读数中位数和分组温控固定等待，不再使用 RAMP/SYNC。
温控测量时物理 OFF，恢复 5 V DC + ON；噪声保持 Z 输出关闭。
保留运行/历史两个主 Tab，新格式支持填回与重新分析，旧 RAMP 历史仅查看已有结果。
时钟目标来自 `params/devices.yaml`，`params/clock_sources.yaml` 仅为其他旧入口回退。
详细参数、原始数据及安全策略见[静磁场实验说明](../docs/experiments/static_mag_sens_v2.md)。

### 静磁场灵敏度局部优化（已归档）

`static-sensitivity-optimize-local` 只保留已归档卡片；旧书签显示归档提示，不再加载参数、运行、会话或历史页面，旧运行与历史 API 返回 410。不能仅修改目录归档标记恢复运行，必须先手动接回代码与注册接线。

专属实现、两份薄入口、正式回归、原图及 GUI 快照保存在[归档目录](../old/experiments/README.md)。稳定 ID、[默认参数](../params/experiments/static-sensitivity-optimize-local.yaml)及与主实验共用的 `data/Static_Magnetic_Field_Sensitivity/` 历史保持原位；静磁场主实验不受影响。归档前的 DC 候选测量、批次结论与恢复说明见[实验说明](../docs/experiments/static_sensitivity_optimize_local.md)。

### 参考时钟同步

当前 DG4000、DG900 和 HF2 / MFLI 统一接入同一外置 10 MHz 参考源，目标均为 `EXT`，
包含 Pump 调制信号源 `DG4E222800868`；其载波与门控共用设备参考时钟。
先完成外置源接线并开启参考信号，再在“功能模块 → 参考时钟同步”点击“开始同步”，
核对每台设备设置和回读均成功。旧 Python 实验运行前需先执行该准备步骤。
也可在仓库根目录执行同一共享流程：

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Set_All_Clocks_External.py
```

同步入口默认读取设备库，旧配置文件保留兼容用途；程序不连接或配置外置源。
保存设备库后，同步按钮按当前映射选择实际仪器，支持 USB/TCPIP 混用，并将同一
资源的多个通道合并同步一次。TCPIP 仪器按设备库 ID 读取其参考时钟目标。
信号源控制页的高阻负载显示为 `INF`，提交时同样使用 `INF`。仪器原始回读的
`9.9E+37` 是高阻标记，驱动也会将旧页面提交的数值标记转换为高阻关键字。

批量同步分为两阶段：先向全部设备写入目标时钟源并保持连接，再统一回读校验和释放连接，
逐台报告结果；同步不改动输出负载、幅度、偏置或输出开关。
负载使用独立的“功能模块 → 输出负载初始化”：点击“初始化负载”后，Pump 调制通道设为
50 Ω，其余设为高阻，包括 Probe AOM 载波、加热信号与温度开关信号。DG900 高阻写入
`INF` 并回读验证，负载不匹配则报告该设备失败。初始化不改动参考时钟；更改负载可能
改变幅度、偏置回读，操作后需核对。两项工具均使用全局硬件队列。
`EXT` 回读表示参考源选择生效，实际接线和锁定状态需在仪器上确认，
详见[参考时钟说明](../docs/instrument_mapping.md#统一外置参考时钟)。

## 实验中心

- `/experiments` 默认展示仍在维护且未归档的正式实验；“已归档”只显示静态卡片，不提供运行/历史入口。代码归档卡片由 `lab_workflows/experiments/archived.py` 提供，默认注册列表不加载归档实现。
- `*_plot.py` 挂在对应实验下作为离线分析器，不显示为独立实验。
- `.ipynb`、快速优化脚本和时钟同步工具不进入实验目录。
- 新实验先实现 `lab_workflows` 契约，再注册到 `lab_workflows/experiments/registry.py`。
- `typed_workflow` 的表单字段来自显式强类型参数模型，不扫描工作流中的全大写变量；
  `legacy_script` 继续使用兼容适配器。目录卡片不再显示新／旧模式徽标，执行模式不变。
- 完整清单与执行模式见[实验注册表](../lab_workflows/experiments/registry.py)；逐个维护旧脚本时应迁移成
  `lab_workflows/experiment_modules/` 中的模型、工作流、分析器和薄入口。
- 参数模型内部使用 `snake_case`，通过 `external_name` 保持 GUI/YAML 的历史大写键兼容。
- 公共流程历史列表跳过配置损坏、实验身份不符、格式不支持或读取期间已消失的目录，保留其它有效记录；直接打开无效目录仍返回对应错误，其它服务错误正常报告。
- T2 标定表单来自 `T2CalibrationParams` 的显式字段；固定接线通道、TTL 电平、
  AOM 固定载波和自动量程边界作为隐藏运行参数保存，不会出现在 GUI 表单中。
- “RF 频率响应（DirectAW）”的“任意波波形文件”参数会在每次打开配置页时
  自动扫描 `experiments/` 目录中的 `*.csv` 文件，并以下拉菜单供选择。
- “RF 频率响应（AM）”（`rf-sensitivity-aw-frequency`）已代码归档，仅保留目录卡片；运行、参数和历史 API 返回 410。DirectAW 实验及其共用分析入口不受此次归档影响，历史数据和参数原位保留。
- “温控开关 PID 周期测试”（`temperature-switch-pid-cycle`）同样已代码归档，采集／分析脚本移至 `old/experiments/experiments/`；GUI 仅留卡片，旧 API 返回 410，参数和历史数据原位保留。
- “任意波示波器检查”（`aw-waveform-scope-check`）已代码归档，不再迁移；原脚本位于 `old/experiments/experiments/AW_Control_Waveform_Scope_Check.py`。GUI 只保留归档卡片，旧运行、参数和历史 API 返回 410，不提供流程图或独立重新分析；共享波形 CSV、参数及历史数据不移动。
- `static-sensitivity-optimize`、`aw-const-omega-check`、`xy-dc-voltage-calibration`、`xy-aw-voltage-calibration` 同样只保留归档卡片；7 份专属脚本位于 `old/experiments/experiments/`，不进行模块化或流程图迁移。旧参数、运行和历史 API 返回 410，GUI 不提供重新分析或一键恢复；默认参数、历史结果及共享 CSV 保留。局部优化、静磁场主实验和 XY DirectAW DC 标定仍在维护，本次不操作用户服务。

实验中心标签可在页面右上角“管理分类与实验信息”中新增或重命名。进入编辑模式后，
每张实验卡片都可以修改显示名称、介绍和所属分类，或点击“归档／恢复”。
“全部”、普通分类和普通搜索只显示未归档实验；“已归档”入口提供独立的卡片查看与搜索。
归档保留原分类，不删除代码、原始数据或分析结果。已移出主目录的专属代码、正式测试和原图保存在
[`old/experiments/`](../old/experiments/README.md)，共用算法及默认参数、历史数据仍在原位。
这些实验没有配置、预检、运行、历史和重新分析入口，旧 API 请求返回 410；卡片保留名称、介绍和分类编辑。
已移走代码的卡片显示归档路径，不提供一键恢复；必须先手动接回实现、注册入口及必要的 GUI 接线并完成无硬件回归。
仅目录归档、代码未移走的其它实验仍可恢复到原分类；目录按钮不会自动搬运源文件，也不会取消现有任务。

标签、实验归属、显示名称、介绍、归档 ID 列表与参数布局保存在
`params/experiment_catalog.yaml`，只影响 GUI 组织方式，不改变实验稳定 ID、
运行目录或采集逻辑。归档状态在重启或换浏览器后保持，同一后端的其他浏览器刷新或重新进入后生效。
旧版本目录配置缺省不包含目录归档；已移走代码的 ID 始终视为归档，不能通过删改配置绕过。读取不会改写文件，下一次有变更的目录保存使用版本 3。
目录写入在同一后端进程内串行执行；原子替换失败时报错并保留旧文件，不以直接覆盖兜底。

目录接口在每次请求内复用一份设备映射，不跨请求缓存；实验中心以外的页面及图表按需加载，
因此这些页面首次打开可能短暂显示加载提示。目录加载失败可原地重试，导航和全局任务状态不受影响。
归档用于整理目录，不以减少卡片数代替性能验证。`GET /api/experiments` 仍返回全部实验，
响应中的 `archived` 表示归档状态，`archive_path` 非空表示实现已移出主目录；
`PUT /api/experiments/{experiment_id}/archive` 接受严格布尔值 `{"archived": true}` 或 `{"archived": false}`。
对代码已归档的实验请求恢复会返回 422；保留旧 Python 导入的按需兼容不代表开放 GUI 运行权限。

目录无硬件回归可在仓库根目录运行：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest GUI.backend.test_experiment_catalog
```

实验配置页可把参数在“基础参数”和“高级参数”之间移动，并调整组内顺序。
点击“存为默认参数”时，当前参数值、分类和顺序会一并保存到
`params/experiment_catalog.yaml`；后续刷新页面、重启服务或换浏览器都会读取同一布局。
GUI 后端以非热重载模式运行；更新后端代码后，优先在总览页点击“重启后端”。
重启只支持由 `GUI/start.ps1` 或 `python -m backend` 启动的正常运行后端；若新代码启动失败，
页面会在等待超时后提示检查后端窗口，并需人工修复后重新启动。

“Z 任意波实际电流闭环校正”（`/experiments/z-aw-closed-loop-waveform-correction`）复用 XY 双列分步界面：6 个采集、2 个分析节点，保留运行／历史 Tab、原指标、图和下载。
所有当前可见参数在节点内编辑，默认分类仍由模型和已保存布局共同决定；Scope 量程余量在 A04/A05 共享值与分类，初始化来源只在 A03 编辑。
保存参数与布局、预览、预检、完整运行、取消和日志位于流程顶部。应用草稿后显式重跑，每次 A05 新建目录；具体目录分析没有覆盖参数，保存默认值只保存下一次实验与统一分类。
等待下一步保持连接及硬件锁；A05、异常与取消均先停止 SDS、关闭 Z，再保存汇总，共同触发保持当前状态。A06 无前置结束并释放连接；刷新恢复存活会话，EOF 和后台退出自动收尾。
三种校正方法、独立验证与冻结格式均保留；未通过谐波重载验证不生成冻结波形。轮次汇总、冻结 NPZ/CSV 和恢复溯源是分析输入，重算不移动这些文件。
历史与摘要只读，旧重新分析和分步分析互斥；薄离线入口必须显式传入运行目录。详见 [闭环实验说明](../docs/experiments/z_aw_closed_loop_waveform_correction.md)。

## Mx XY 剩磁二维校准分步运行

`/experiments/mx-xy-residual-field-calibration` 提供运行/历史双 Tab、10 个采集与 3 个独立分析节点，20 个可见参数在节点内编辑。顶部保存参数和分类布局、预检、完整运行及任务状态；A07/A08 共享 SDS 记录时长，草稿应用后仍须显式重跑。A08 保留 X 外层、Y 同向网格、零 offset 与逐次量程门控，恢复温控后保存，失败抢救已读帧；重跑新建目录。

A09 恢复 GS200 原态、SDS DC 和温控，XYZ 归零关闭；A10 无前置结束并释放硬件锁。B01 检查原始资格，B02 复用原 Bloch/漂移和逐 X 色散算法，B03 只重绘/导出，不施加补偿输出。具体历史目录没有额外分析覆盖值，读取/填回不改原快照，保存默认值只针对下一次实验。原科学图、下载及完整运行入口保留，离线薄入口必须显式给目录。见[实验说明](../docs/experiments/mx_xy_residual_field_calibration.md)。

## Mx 主磁场示波器噪声谱分步运行

`/experiments/mx-main-field-scope-noise-spectrum` 提供运行/历史双 Tab 和 XY 风格双列流程图，10 个采集、2 个分析节点，32 个可见参数在节点内编辑。顶部保留参数与布局保存、派生预览、预检、完整运行和取消；下一次实验的 Welch 参数在 A07/B02 共享，具体历史目录使用独立分析覆盖值。
A08 每次 SDS 尝试均在配置后关温控、稳定、采集至内存、恢复后保存；恢复失败也抢救已读帧，重跑新建目录。A09 恢复 GS200、SDS DC 耦合和温控、关闭 Z/XY 并封存，A10 无前置结束并释放硬件锁；等待、取消、EOF 与关闭均复用串行会话安全协议。保留原局部诊断、二维全局算法、历史图与下载；薄离线入口必须显式提供目录。见[实验说明](../docs/experiments/mx_main_field_scope_noise_spectrum.md)。

## Mx Y RF Probe 失谐优化分步运行

`/experiments/mx-y-rf-probe-detuning-optimization` 复用 XY 双列流程和运行/历史 Tab，提供 9 个采集、2 个独立分析节点及可编辑原图下载。40 个可见参数在节点内编辑，保存、预检、完整运行及日志位于流程顶部；共享参数与分类同步，应用草稿后显式重跑。具体目录分析覆盖独立保存，不改下一次实验或历史采集快照。

保留固定 Pump、PZT 外层/Probe 内层蛇形扫描和 Probe 单点模式，不连接 TEC；每条 R 记录恢复温控后保存。结束恢复原 PZT/扫描与已执行的光功率基准，辅助场和 Y RF 归零关闭，保持主场与 Pump。重跑新建目录，已结束目录支持历史填回与双斜率重分析；命令行分析必须显式传入运行目录。详见[实验说明](../docs/experiments/mx_y_rf_probe_detuning_optimization.md)。

## Mx Y RF 光功率优化分步运行

`/experiments/mx-y-rf-power-optimization` 复用 XY 两列界面，提供 10 个采集、3 个分析节点及运行/历史 Tab。40 个可见参数在节点编辑，保存参数与布局、派生预览、预检、完整运行和任务日志在流程顶部；共享参数与分类同步，应用草稿后显式重跑。
A08 执行 Pump 外层、Probe 内层蛇形网格，每点复用原 R 幅度响应与零 RF 噪声。质量坏点仅在安全收尾成功后继续，设备错误立即停止；A09 恢复已执行参数中的光功率并封存，A10 无前置安全结束会话。等待期间保持硬件锁，取消、EOF 和后台退出均自动收尾。
分析保留双斜率矩阵、排名、最佳点和逐点诊断；具体目录仅覆盖支持的分析门槛与标定等参数，不改采集快照或下一次实验设置。历史读取只读，重新分析与分步分析互斥，薄离线入口必须显式传入目录。见[实验说明](../docs/experiments/mx_y_rf_power_optimization.md)。

## 原子自旋投影噪声分步运行

`/experiments/projection-noise` 提供运行／历史双 Tab、9 个采集／2 个分析节点，16 个可见参数在节点内编辑；顶部保留参数与布局保存、预检、完整运行和任务状态。A01–A06 完成主场标定预检、连接、时钟、光功率、TEC 和 SDS 准备；A07 保留整段可选 OFF 背景后接 ON 的顺序及跨阶段量程继承，每个候选帧恢复温控后才保存。
**Pump 载波／门控全程不修改**，不连接其专用信号源、不写其时钟或输出；Pump/Probe 光功率仍按界面设置。A08 恢复 GS200 完整原态、温控和 SDS STOP，XY/Z 归零关闭，成功后封存；再次采集须从 A04 重新准备。A09 无前置结束，取消／EOF／后台关闭自动收尾，恢复失败不报告安全完成。
下一次实验的 Welch 点数／拟合半宽在 A06 与 B02 共享；具体 SDS 历史目录使用独立覆盖值与草稿，修改后重跑 B01/B02。默认保存不写历史配置，历史支持参数填回、重新分析及原 PNG/PDF/NPZ/CSV 下载。旧 HF2 双文件仅走兼容 PSD，不套用 SDS RF 拟合；薄离线入口必须显式指定目录。详见[实验说明](../docs/experiments/Projection_noise.md)。

## Demod3 R 噪声谱分步运行

`/experiments/photon-shot-noise` 提供运行/历史双 Tab、8 个采集与 3 个独立分析节点，
21 个可见参数在节点内编辑；顶部保存布局、派生预览、预检、完整运行与任务状态。
采集时长和请求率在 A06/A07 共享，应用草稿后必须显式重跑；等待期间占用硬件锁。
每条重复恢复温控后保存；结束保留已执行主场、Probe、Pump OFF 和锁相设置，只断开 TEC。
分析保留原 Welch→ASD 中位数→均值平方加权拟合及四组 PNG/PDF，三个分析覆盖值按目录隔离，
浏览／分析不改历史采集配置；薄离线入口必须显式给目录。见[实验说明](../docs/experiments/Photon_shot_noise.md)。

`/experiments/noise-spectrum-xy-demod3-r` 提供运行/历史双 Tab、12 个采集与 3 个独立分析节点，60 个可见参数在节点内编辑。顶部保存参数与布局、派生预览、预检、完整运行和任务状态；校相/扫描共享稳定等待和波形参数，应用后须显式重跑。
A11 保留控制频率外层、Demod3 同向内层及温控批次，恢复后原子保存 R 均值矩阵，取消和恢复失败抢救已读值。A11 完成本轮安全收尾，再采须重新准备/校相；A12 无前置结束并释放硬件锁。X/Y、Z 和两路外触发归零关闭，温控恢复，Pump 与时序保持当前状态。
分析保留原脊线标定、逐列洛伦兹拟合和 R（V，非 PSD）科学图；具体目录没有分析覆盖值，历史读取/填回不改快照，重新分析与分步分析互斥。薄离线入口必须显式给目录。当前双主电压回环仅支持 HF2，选择 MFLI 预检拒绝，不自动改接线。见[实验说明](../docs/experiments/noise_spectrum_xy_demod3_r.md)。

## XY 正弦噪声谱分步运行

`/experiments/noise-spectrum-xy` 的运行 Tab 在两列流程节点中编辑参数；保存参数与布局、Welch 预览、预检、完整运行及采集任务状态统一放在实验流程图顶部，不再单独显示“默认参数与预览”和“控制与状态”卡片。
节点常显基础参数名称，展开编辑；高级参数独立折叠，可同时展开多个节点。共享过程参数同步值与分类并注明其他使用步骤，初始化条件仅在配置节点展示。
“恢复温控等待时间”在 A08/A10/A12 同步编辑；扫描范围在 A12 编辑，应用后的失效范围仍从 A11 起。
移动基础／高级分类后，“保存参数与布局”保存下一次实验的全部参数和统一分类偏好。
采集 A01～A13 顺序依赖，A14 随时结束空闲会话。分析默认编辑“下一次实验”的自动分析设置，频率格间距与 A11 共用；选择具体目录后使用独立分析覆盖值执行 B01～B05。
切换分析对象保留各自草稿，保存默认值不保存历史分析覆盖值；具体目录分析不改变下一次实验参数。
勾选模块及尚未完成的前置，再点击“运行所选 / 重跑”；空闲改参后先“应用参数到会话”。
编辑先显示“草稿未应用”及预计需重跑的已完成步骤，执行记录在应用前保持原状；还原值后预览消失，应用后以后端状态为准，再显式重跑。
具体目录分析草稿在运行所选分析时应用。执行期间沿用原编辑限制，独立历史分析仍可操作。
XY 校相未收敛禁止 DAQ/扫描，允许重试；A13 恢复后重扫必须重新 A11 准备。
每轮扫描创建新目录，旧 raw/config 保留；分阶段重算仅归档受影响产物。
质量未通过的节点展示已记录的诊断产物下载，后继仍受门槛阻断。

等待下一步时设备连接、输出状态和全局硬件锁保持，任务仍运行；检查后点击“结束会话”。
执行中使用任务“安全停止”。正常结束补做必要的解调器恢复；异常、取消、父进程通信断开自动收尾。
正常结束恢复最近成功校相的解调配置；只应用但尚未重跑的新采样率或时间常数不会写入仪器。该规则同样适用于 XY 已知噪声和 Probe AM 的共享结束路径。
刷新恢复仍存活的会话；后端重启不读取历史完成记录恢复硬件。原连续运行入口仍自动收尾并分析。
运行页可下载实验包内可编辑 Excalidraw 原图；历史结果保留，静态架构图已归档。

本实验接口前缀 `/api/experiments/noise-spectrum-xy`：

- `GET /workflow`、`GET /workflow/diagram`：共用模块清单及原图。`display_parameters` 描述节点编辑入口，独立于原 `parameters` 失效起点；`parameter_impacts.acquisition/analysis` 从后端共用失效规则生成重跑范围，前端直接用于草稿预览。
- `POST /sessions`、`GET /sessions/active`、`GET /sessions/{id}`：创建、刷新和查询会话。
- `PATCH /sessions/{id}/parameters`、`POST /sessions/{id}/steps`、`POST /sessions/{id}/finish`：携带修订号的串行命令；拒绝过期/重复/执行冲突。
- `GET /runs/{run_id}/analysis`、`POST /runs/{run_id}/analysis`：阶段记录、显式阶段及分析参数；与旧重新分析共享目录互斥。

更新流程接口后需重启后端；旧后端缺少角色、展示关系或影响范围时，按上文[分步流程契约与兼容](#分步流程契约与兼容)阻止新的分步执行，仍恢复存活任务，保留取消及安全结束通道。

通过现有 JSONL 和 SSE 推送会话/阶段快照；旧运行没有阶段记录时从 B01 开始。

## XY 已知可控噪声谱分步运行

`/experiments/noise-spectrum-xy-known-noise` 提供采集 A01～A15、分析 B01～B06 两列节点，保留运行/历史两个主 Tab。
节点内编辑共享参数，流程顶部保存参数与布局、预览、预检、完整运行、取消和日志。
分步等待保持连接、输出与硬件锁，刷新恢复活会话；A15 无前置结束，关闭 X/Y、Z 和共用触发并恢复温控。

分析阶段选择静磁场 v3 直流逐点历史曲线拟合增益，未选时完整运行完成 B01～B04 并显示待标定；
B05 只依赖 B01，B06 依赖 B04/B05。来源变化只重算 B05/B06，条件差异允许并保存在本轮产物中。
历史目录分别保留独立分析草稿，保存默认值仅保存下一次实验设置和统一布局。
会话、阶段分析和原图下载接口沿用上述协议，前缀为 `/api/experiments/noise-spectrum-xy-known-noise`。
具体步骤、原图和验证命令见[实验说明](../docs/experiments/noise_spectrum_xy_ctrl_known_noise.md)。Probe AM 保留原采集与页面行为。

## RF DirectAW 分步运行

所有正式实验的锁相初始化统一关闭 50 Ω 终端和 AC 耦合，含 Input2 回环；后续校相和采集保持关闭。
输入量程和差分选项仍按实验配置，旧耦合/阻抗参数键仅用于兼容，运行页不再显示开启选项。

`/experiments/rf-sensitivity-aw` 的运行页复用上述流程图交互，采集为 A01～A15，
A16 无前置、结束空闲会话；独立分析为 B01～B04。节点内编辑参数、共享值与分类同步，
草稿显示重跑预览，先应用到会话再显式勾选执行。共同触发只显示一组并同步 XY/Z 旧参数键。
流程顶部保留保存、采集预览、预检、完整运行及任务状态，历史 Tab 保留详情和多次对比。
A09 波形按「名称｜勾选框｜补偿倍率」逐行展示，本实验支持多选，各控制独立校相、采集、保存和分析，共用当前 RF 参数且不自动改频；补偿默认 1，只缩放 XY 物理包络，在 K/B 电压标定前应用，不改变主场和 Z RF。更改并应用后须显式重跑 A09 及后继。已知噪声实验使用相同的多选列表，补偿不作用于注入噪声；两个 X 响应实验的 B03 均支持可关闭 MAD 高值异常谱筛选和可配置评分频带；每控制独立评分，至少 5 条、有正基线且实际频带覆盖充分时才筛选。结果显示保留/剔除数量及未筛选原因，提供父子逐记录 CSV/JSON、PSD 与前后对照结果。旧单选历史填回为单项列表，历史缺策略保持全部平均；筛选覆盖只使 B03/B04 失效，不改变原始数据或下一次默认值。新多控制运行可与旧单控制运行跨次对比，按控制展开曲线。R 变体维持原参数与界面。

分步等待保持输出与硬件锁，质量未通过保存诊断并允许同连接重试。A15 恢复响应滤波、
关闭 XYZ、恢复温控并完成本轮，保持设备会话；再次采集须显式重跑 A09～A15，保存新目录。
A16、取消、EOF 或后端关闭完成安全收尾后才释放硬件锁，保持共同触发及其 SYNC、Pump 门控
和标准保留输出。结束会话保留已完成轮次的配置，详情见 [RF 实验说明](../docs/experiments/rf_field_measurement.md)。

分析对象默认为下一次实验的自动分析设置；具体已完成目录只支持 `NOISE_NPERSEG` 独立覆盖值，
执行分析时应用，草稿按目录保留，保存默认值不覆盖历史设置。四阶段分析校验输入与产物指纹，
重算归档受影响结果，与同目录重新分析和多次对比互斥。接口路径与上述 XY 会话一致，
前缀为 `/api/experiments/rf-sensitivity-aw`，可下载实验包内可编辑 Excalidraw 源图。
无硬件回归：`agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode lab_workflows.experiment_modules.noise_spectrum_xy.test_stages lab_workflows.experiment_modules.noise_spectrum_xy.test_analysis_stages GUI.backend.test_noise_spectrum_xy`。
实验步骤、安全策略和数据说明见 [实验文档](../docs/experiments/noise_spectrum_xy_ctrl.md)。

## 示波器、RF 频响与长飘分步运行

下列页面同样在运行 Tab 的依赖节点内编辑参数、保存布局、预检和运行；历史 Tab 保留各实验原有的结果与操作。应用草稿只更新失效状态，显式重跑后才执行；结束时使用已执行配置。具体目录的阶段分析与硬件会话分开，重新分析不覆盖采集参数。

`z-aw-current-waveform-scope-check` 同样复用 XY 两列分步界面：6 个采集、3 个分析节点，
理论/闭环双来源冻结，CH3 采样电阻电压换算为实际电流、CH4 下降沿对齐；具体目录没有分析覆盖值。
保存、预检、完整运行和任务状态位于流程顶部；应用参数后显式重跑，初始化参数仅在配置节点编辑。
每次重复采集新建目录；A06 无前置结束，正常、异常、取消及 EOF 均停止 SDS、关闭 Z 输出，保持共同触发当前状态。
历史 Tab 提供参数快照、填回、结果图和下载，读取不启动分析；重分析绑定该目录并与分步分析互斥。

| 稳定 ID | 采集 / 分析节点 | 结果与分析边界 |
| --- | --- | --- |
| `rf-sensitivity-aw-known-noise` | 17 / 4 | 固定同轮噪声 ON，A11 可跳过 RF 扫描并共用一个手动相位，不移动噪声；其余校相与标定逐版本执行，保留多版本图与历史对比 |
| `scope-capture` | 5 / 3 | 保留波形、PSD、双曲线和历史曲线操作；阶段 Welch 分析没有独立覆盖值 |
| `mx-y-rf-frequency-response` | 10 / 3 | 保留两张科学图与逐幅度布洛赫诊断，具体目录可独立覆盖拟合门槛 |
| `mx-y-optimal-control-rf-frequency-response` | 7 / 3 | 保留热图、相位中位数、可选常数对照和标定诊断；阶段分析没有独立覆盖值 |
| `mx-y-rf-sensitivity-drift` | 11 / 3 | 绝对时间调度，逐轮完成标准子运行并即时分析；父分析仅重建趋势、保留原两图与无效轮原因，无独立覆盖值 |
| `mx-z-optimal-control-rf-sensitivity` | 8 / 3 | 原成对 R/X/Y 校相、带符号 R 响应与可选 RF 噪声；零 RF 仅 Z 剩磁诊断。A07 安全封存，A12 无前置结束；历史分析无额外覆盖值 |
| `mx-z-optimal-control-xy-rf-sensitivity` | 7 / 3 | 理论/闭环双来源冻结、中心一次 RF 正交校相、蛇形 XY 灵敏度网格；A06 安全封存、A12 无前置结束。保留原热图、逐点诊断及历史下载，具体目录无额外覆盖值 |
| `mx-z-optimal-control-xy-noise-spectrum` | 7 / 3 | 双来源冻结、RF-off 蛇形 XY DC 噪声网格，A06 安全封存、A12 无前置结束；具体历史目录独立覆盖统计频段，保留 Welch、热图、谱线诊断与最佳点 |
| `mx-z-optimal-control-xy-leakage-response` | 7 / 3 | 理论来源冻结、同形带符号 XY 任意波蛇形网格及 R 质量重试，A05 施加实测最小点、A06 安全封存、A12 无前置结束；保持 Z/XY 与共同触发当前状态，历史分析无额外覆盖值，保留原热图和报告 |
| `mx-z-optimal-control-xy-rf-phase-response` | 6 / 3 | 理论/闭环来源冻结、蛇形 XY DC 与 180° 配对 RF 相位，R 质量重试和单轮异常重采；A05 安全封存、A12 无前置结束，保持 Z/共同触发且 X/Y 归零关闭；历史独立覆盖拟合最低 R² 与幅度显著性，保留非线性色散/旧正弦诊断和三组图，不选最佳 XY |
| `mx-z-optimal-control-dg4000-bias-xyz-balance` | 6 / 3 | GS200 物理断开且不连接，Z 任意波仅叠加 offset、X/Y DC 蛇形网格与同步 R/X/Y 复噪声重试；A05 恢复 Z/触发/XY 原态后封存、A12 无前置结束。来源与独立 Z 标定冻结，保留原复数 Bloch 拟合、静态 Z 零场及三组科学图；具体目录无额外分析覆盖值，R 最小点只作诊断 |

`mx-y-rf-frequency-response` 的旧报告非有限值经只读诊断接口返回 `null`，原 JSON 和图文件下载保持原字节；该页面区分已有科学产物与尚未开始的分步分析。长运行目录与参数表在窄屏换行，原图仍可下载。

长飘的 A09 包含标准子运行的采集及即时分析参数；它们属于下一次采集设置。具体父目录的 B01～B03 读取已完成子运行的结果，父分析参数为 `{}`，不重算子运行。全无效轮次仍可导出诊断，正在写入的父运行只允许读取已有摘要。
