# Bell-Bloom 实验控制台

本地 Web GUI，用于读取和设置实验仪器（包括 GS200 主磁场电流源和 TOPTICA DLC pro Probe 激光）、同步参考时钟、校准 Demod0，
以及从统一“实验中心”配置、预检、运行和重新分析正式 Python 实验。

## 安装

先按[仓库环境说明](../README.md#环境准备)安装本地包、仪器与分析依赖。
前端需要 Node.js / npm，当前 Vite 要求 Node.js `^20.19.0 || >=22.12.0`，
依赖版本由 `frontend/package-lock.json` 锁定。

```powershell
.\agent_exp_env\Scripts\python.exe -m pip install -r GUI\requirements.txt
Set-Location GUI\frontend
npm ci
```

`npm run build` 不再是必备步骤；`GUI\start.ps1` 会以 Vite dev 模式直接 serve 源码并提供 HMR。
只有在准备发布或不便启动 Vite dev（例如纯命令行环境）时才需要 `npm run build`。

## 启动

完成安装后，在文件资源管理器中双击仓库根目录的 **`start_gui.bat`**。
它会启动前后端，并在 Vite 就绪后自动打开默认浏览器，无需手动输入地址。
入口使用自身目录定位仓库，支持从其他工作目录运行；PowerShell 执行策略仅对该进程生效，
不修改系统设置，不自动安装依赖。

也可从仓库根目录用命令行启动；默认不自动打开浏览器，附加 `-OpenBrowser` 可开启：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\start.ps1
# 自动打开浏览器
powershell -NoProfile -ExecutionPolicy Bypass -File GUI\start.ps1 -OpenBrowser
```

启动器会同时拉起两个进程：

- **FastAPI 后端** `http://127.0.0.1:8000`，提供 `/api/`；存在 `frontend/dist/` 时也提供构建后的页面
- **Vite dev server** `http://127.0.0.1:5173`，serve 前端源码并把 `/api` 转发到 FastAPI

页面地址为 `http://127.0.0.1:5173`。修改前端源码后，Vite 会自动 HMR 推送变更到浏览器，
不需要再 build。启动器在独立窗口运行后端，当前窗口按 Ctrl+C 只停止 Vite；
后端需要在自己的窗口停止或关闭。关闭浏览器不会停止服务。后端没有启用热重载，
修改 Python 代码后需要重启。停止服务前先在 GUI 正常停止实验或功能模块，
服务退出不等于仪器安全收尾。

启动器检查虚拟环境 Python、`node_modules`、`npm.cmd` 和 8000 / 5173 端口。
缺失依赖或端口占用时直接报错，不会自动安装、结束已有进程或改用其他端口。
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

## 前端结构

- `frontend/src/pages/`：路由页面，只编排页面级状态和 API 调用。
- `frontend/src/components/`：布局、表单、任务视图和仪器编辑器。
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
DG900 通道设置会等待仪器完成全部命令并检查 SCPI 错误队列后再回读；关闭调制时
只操作实际启用的类型，避免连续加热波形被不兼容的 PWM 状态命令干扰。

“仪器控制”中的 GS200 模块只开放 `main_magnetic_field` 电流设定值（mA）和输出开关；
源模式、电流量程、限压和限流只读。电流范围从 `params/safety_limits.yaml` 读取，
当前为 `-10～10 mA`。输出从 OFF 切换到 ON 时，浏览器会再次显示目标电流并要求确认；
设备连接后的写入或回读发生异常时，后端按 `output_off_on_error` 尽力关闭输出。

TOPTICA DLC pro 模块开放激光电流、温度、PZT Scan Offset、扫描幅度和扫描启停，
扫描频率与 Laser Enabled 只读。Emission 使用独立高风险接口：远程 ON 默认由
`remote_emission_control_enabled=false` 禁止，开放后仍需现场安全勾选、输入控制器
编号和浏览器再次确认；OFF 无需确认但会强制回读。DLC pro 通信或回读异常时不自动
关光或回滚，界面会提示设备状态可能未知。完整说明见 `docs/toptica_dlc_pro.md`。

Keithley 6221 的 Hz 任意波 CSV/TXT 解析与标定换算逻辑位于
`lab_workflows/keithley_arb.py`（纯计算、无硬件），GUI 通过
`POST /api/tools/keithley-waveform-convert` 上传文件文本获得解析与换算结果；
标定实验 ID 通过 `CALIBRATION_EXPERIMENT_IDS` 别名集合兼容历史文件。

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
运行/历史/架构 Tab 保留，新格式支持填回与重新分析，旧 RAMP 历史仅查看已有结果。
时钟目标来自 `params/devices.yaml`，`params/clock_sources.yaml` 仅为其他旧入口回退。
详细参数、原始数据及安全策略见[静磁场实验说明](../docs/static_mag_sens_v2.md)。

## 实验中心

- `/experiments` 默认展示 `lab_workflows/experiments/registry.py` 中未归档的正式实验；“已归档”入口查看其余实验，注册表本身不变。
- `*_plot.py` 挂在对应实验下作为离线分析器，不显示为独立实验。
- `.ipynb`、快速优化脚本和时钟同步工具不进入实验目录。
- 新实验先实现 `lab_workflows` 契约，再注册到 `lab_workflows/experiments/registry.py`。
- 卡片中的“新模式”对应 `typed_workflow`：表单字段来自显式强类型参数模型，
  不扫描工作流中的全大写变量；“旧模式”对应 `legacy_script` 兼容适配器。
- 完整清单与执行模式见[实验迁移状态](../docs/experiment_migration_status.md)；逐个维护旧脚本时应迁移成
  `lab_workflows/experiment_modules/` 中的模型、工作流、分析器和薄入口。
- 参数模型内部使用 `snake_case`，通过 `external_name` 保持 GUI/YAML 的历史大写键兼容。
- T2 标定表单来自 `T2CalibrationParams` 的显式字段；固定接线通道、TTL 电平、
  AOM 固定载波和自动量程边界作为隐藏运行参数保存，不会出现在 GUI 表单中。
- “RF 频率响应（DirectAW）”的“任意波波形文件”参数会在每次打开配置页时
  自动扫描 `experiments/` 目录中的 `*.csv` 文件，并以下拉菜单供选择。

实验中心标签可在页面右上角“管理分类与实验信息”中新增或重命名。进入编辑模式后，
每张实验卡片都可以修改显示名称、介绍和所属分类，或点击“归档／恢复”。
“全部”、普通分类和普通搜索只显示未归档实验；“已归档”入口提供独立的查看、搜索和恢复。
归档保留原分类，恢复后回到原分类和“全部”，不删除脚本、原始数据或分析结果。
归档实验的配置链接、预检、运行、历史和重新分析仍可使用，也不会取消正在运行的任务。

标签、实验归属、显示名称、介绍、归档 ID 列表与参数布局保存在
`params/experiment_catalog.yaml`，只影响 GUI 组织方式，不改变实验稳定 ID、
运行目录或采集逻辑。归档状态在重启或换浏览器后保持，同一后端的其他浏览器刷新或重新进入后生效。
旧版本目录配置缺省为无归档，读取不会改写文件，下一次有变更的目录保存使用版本 3。
目录写入在同一后端进程内串行执行；原子替换失败时报错并保留旧文件，不以直接覆盖兜底。

目录接口在每次请求内复用一份设备映射，不跨请求缓存；实验中心以外的页面及图表按需加载，
因此这些页面首次打开可能短暂显示加载提示。目录加载失败可原地重试，导航和全局任务状态不受影响。
归档用于整理目录，不以减少卡片数代替性能验证。`GET /api/experiments` 仍返回全部实验，
响应中的 `archived` 表示归档状态；`PUT /api/experiments/{experiment_id}/archive` 接受严格布尔值
`{"archived": true}` 或 `{"archived": false}`，不改变实验运行权限。

目录无硬件回归可在仓库根目录运行：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest GUI.backend.test_experiment_catalog
```

实验配置页可把参数在“基础参数”和“高级参数”之间移动，并调整组内顺序。
点击“存为默认参数”时，当前参数值、分类和顺序会一并保存到
`params/experiment_catalog.yaml`；后续刷新页面、重启服务或换浏览器都会读取同一布局。
GUI 后端以非热重载模式运行；更新后端代码后需要先停止旧进程并重新执行
`GUI/start.ps1`，否则前端会提示后端尚未确认参数分类保存。

“Z 任意波实际电流闭环校正”使用专用运行页（`/experiments/z-aw-closed-loop-waveform-correction`），
参数分组行为与上面一致，但入口不同：

- 默认分组由 `ZAWClosedLoopWaveformCorrectionParams` 的 `group` 与
  `params/experiment_catalog.yaml` 中的 `z-aw-closed-loop-waveform-correction` 共同决定，
  当前为 10 个基础参数、10 个高级参数；`CONTROL_SCALE`、`TRIGGER_FREQUENCY_HZ`、
  `TRIGGER_AMPLITUDE_VPP`、`SCOPE_CYCLES`、`SCOPE_HEADROOM_FACTOR` 默认在高级参数。
- 每个参数右上角的“移至高级 / 移至基础”只修改 `parameter_layout`，参数键和参数值都不变；
  运行期间按钮禁用，避免界面状态与正在执行的快照冲突。
- 移动后页面提示“布局有未保存修改”，点击“保存参数与布局”才把完整的参数值与分类写入磁盘；
  保存成功后前端回读 schema 校验磁盘布局与页面一致，不一致会提示刷新页面。
