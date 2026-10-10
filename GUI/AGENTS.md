# GUI 与共享工作流开发说明

## 目录职责

- `GUI/backend/`：FastAPI 路由、SSE、任务状态与本机静态文件服务。
- `GUI/frontend/`：React 页面、导航与用户交互。
- `lab_workflows/`：实验脚本和 GUI 共用的硬件/实验流程。
- `src/`：底层仪器驱动，不放具体实验步骤。

## 强制约定

- 不得在 FastAPI 路由或 React 页面中复制实验流程、SCPI 命令或安全恢复逻辑。
- 共享实验逻辑必须放入与 `GUI/` 平级的 `lab_workflows/`，并保持可由普通 Python 脚本直接导入。
- 新增输出设置前，先在 `params/safety_limits.yaml` 中确认物理量限值；调用设备 setter 前执行 `validate_safety_limit()`。
- 同一物理 resource 的多通道必须复用连接语义，硬件任务必须遵守全局互斥。
- 扫描、等待和重复采集循环必须提供协作式取消检查点，并在 `finally` 中恢复本流程改变的危险状态。
- GUI 页面使用中文；实验绘图的标题、坐标轴、图例和注释继续使用英文。
- 自动化测试使用假仪器或依赖注入，不得默认连接真实硬件。

## 后续修改入口

- 修改正式实验默认参数：先核对注册定义的 `defaults_saver`；当前强类型实验保存到 `params/experiments/<实验ID>.yaml`，静磁场为 `params/experiments/static-sensitivity.yaml`。文档只链接来源，不复制默认值。
- 修改实验流程：先定位 `lab_workflows/experiments/registry.py` 中的稳定实验 ID，再修改对应共享工作流；保留 `experiments/` 脚本为薄入口。
- 修改静磁场流程：`lab_workflows/experiment_modules/static_sensitivity/`；静磁场也必须通过统一实验注册表调用。
- 修改设备时钟目标：新模式使用 `params/devices.yaml`；`params/clock_sources.yaml` 仅用于旧入口兼容。
- 修改时钟算法：`lab_workflows/clock_sync.py`。
- 独立参考时钟同步先全部写入再统一回读，不改输出负载；“输出负载初始化”单独调用 `initialize_output_loads()`，两项工具都复用全局硬件队列。
- 修改仪器驱动先看 `src/signal_generator/README.md` 或 `src/lockin_amplifier/README.md`；设备切换复用 `components/instruments/mappingDevices.ts` 的同机与共享通道联动，不在页面复制规则。
- 新增实验：使用仓库 `expcodegen` Skill，增加共享参数模型、工作流和分析器，再注册统一实验定义；默认在实验中心路由下提供运行/历史双 Tab。优先复用通用 API，历史摘要确有需要时补齐最小接口，不另建专用导航。
- 新增普通实验参数：为 dataclass 字段补充 UI metadata，前端动态表单会自动呈现。
- 修改实验中心标签：通过 `params/experiment_catalog.yaml` 或标签管理 API；不得通过重命名标签改变实验稳定 ID、`data_type` 或历史目录。

## 验证

- Python：运行受影响的现有无硬件测试。当前可用示例：`agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy.test_sine_mode`。
- 当前检出没有根目录 `tests/`，该目录也被 `.gitignore` 忽略；有本地测试时先核对文件和使用的测试工具，不要直接运行依赖该目录的发现命令。
- Skill：基础检查运行 `agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root .`；新增实验还必须按 Skill 的 `references/delivery-validation.md` 执行 `--delivery` 文档、路由和历史接口验证。
- 语法：对修改的实验入口运行 `python -m py_compile`。
- 前端：在 `GUI/frontend/` 运行 `node node_modules/typescript/bin/tsc --noEmit`、`npm run build` 与 `npm test`（Vitest + Testing Library）。
- API 类型：后端 schema 变更后在 `GUI/` 运行 `..\agent_exp_env\Scripts\python.exe -m backend.openapi_dump > frontend/openapi.json`，再在 `GUI/frontend/` 运行 `npm run generate:api`，并检查 `GUI/frontend/src/types/openapi.d.ts` 变更。
- 未连接硬件时，至少完成所有无硬件测试和生产构建。
- 仅修改文档时核对路径、入口、启动命令与根目录协作说明，不需要执行仪器采集或新增测试脚本。

## 本地服务

- 从仓库根目录运行 `GUI/start.ps1`；前端为 `http://127.0.0.1:5173`，后端为 `http://127.0.0.1:8000`。
- Vite 将 `/api` 请求代理到后端；开发模式直接读取源码，不要求事先构建 `dist/`。
- 后端在独立窗口运行；当前窗口 Ctrl+C 只结束 Vite，后端需要单独停止。
- 后端 `reload=False`，修改 Python 代码后重启；更新 API schema 后同步前端生成类型。
