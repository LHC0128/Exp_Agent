# GUI 与共享工作流开发说明

## 目录职责

- `gui/backend/`：FastAPI 路由、SSE、任务状态与本机静态文件服务。
- `gui/frontend/`：React 页面、导航与用户交互。
- `lab_workflows/`：实验脚本和 GUI 共用的硬件/实验流程。
- `src/`：底层仪器驱动，不放具体实验步骤。

## 强制约定

- 不得在 FastAPI 路由或 React 页面中复制实验流程、SCPI 命令或安全恢复逻辑。
- 共享实验逻辑必须放入与 `gui/` 平级的 `lab_workflows/`，并保持可由普通 Python 脚本直接导入。
- 新增输出设置前，先在 `params/safety_limits.yaml` 中确认物理量限值；调用设备 setter 前执行 `validate_safety_limit()`。
- 同一物理 resource 的多通道必须复用连接语义，硬件任务必须遵守全局互斥。
- 扫描、等待和重复采集循环必须提供协作式取消检查点，并在 `finally` 中恢复本流程改变的危险状态。
- GUI 页面使用中文；实验绘图的标题、坐标轴、图例和注释继续使用英文。
- 自动化测试使用假仪器或依赖注入，不得默认连接真实硬件。

## 后续修改入口

- 修改静磁场默认参数：`params/static_sensitivity.yaml`。
- 修改实验流程：先定位 `lab_workflows/experiments/registry.py` 中的稳定实验 ID，再修改对应共享工作流；保留 `experiments/` 脚本为薄入口。
- 修改静磁场流程：`lab_workflows/static_sensitivity/`；静磁场也必须通过统一实验注册表调用。
- 修改设备时钟目标：`params/clock_sources.yaml`。
- 修改时钟算法：`lab_workflows/clock_sync.py`。
- 新增实验：使用仓库 `expcodegen` Skill，增加共享参数模型、工作流和分析器，再注册统一实验定义；不新增专用 API 或专用导航。
- 新增普通实验参数：为 dataclass 字段补充 UI metadata，前端动态表单会自动呈现。
- 修改实验中心标签：通过 `params/experiment_catalog.yaml` 或标签管理 API；不得通过重命名标签改变实验稳定 ID、`data_type` 或历史目录。

## 验证

- Python：运行 `agent_exp_env\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`。
- Skill：运行 `agent_exp_env\Scripts\python.exe .agents\skills\expcodegen\scripts\validate_experiment.py --root .`。
- 语法：对修改的实验入口运行 `python -m py_compile`。
- 前端：在 `gui/frontend/` 运行 `npm run build` 与 `npm test`（Vitest + Testing Library）。
- API 类型：后端 schema 变更后运行 `python -m backend.openapi_dump > frontend/openapi.json`，再在 `gui/frontend/` 运行 `npm run generate:api`，并检查 `src/types/openapi.d.ts` 变更。
- 未连接硬件时，至少完成所有无硬件测试和生产构建。
