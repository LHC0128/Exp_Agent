# Bell-Bloom 实验控制台

本地 Web GUI，用于读取和设置实验仪器、同步参考时钟、校准 Demod0，
以及从统一“实验中心”配置、预检、运行和重新分析正式 Python 实验。

## 安装

```powershell
agent_exp_env\Scripts\pip.exe install -r GUI\requirements.txt
Set-Location GUI\frontend
npm install
npm run build
```

## 启动

```powershell
Set-Location GUI
..\agent_exp_env\Scripts\python.exe -m backend
```

浏览器访问 `http://127.0.0.1:8000`。服务只监听本机地址。

开发前端时，可分别运行后端和：

```powershell
Set-Location GUI\frontend
npm run dev
```

Vite 会把 `/api` 转发到本机 FastAPI。

## 共享模块

实验脚本不依赖 GUI，可以直接复用平级目录中的工作流：

```python
from lab_workflows.clock_sync import synchronize_clocks
from lab_workflows.phase_calibration import calibrate_demod0_safely
from lab_workflows.experiments import get_experiment, list_experiments
from lab_workflows.steps import calibrate_demod_phase, DirectAWStrategy
```

静磁场默认参数位于 `params/static_sensitivity.yaml`，时钟目标位于
`params/clock_sources.yaml`。脚本与网页均读取同一配置。

## 实验中心

- `/experiments` 展示注册表中的 24 个正式 `.py` 采集入口。
- `*_plot.py` 挂在对应实验下作为离线分析器，不显示为独立实验。
- `.ipynb`、快速优化脚本和时钟同步工具不进入实验目录。
- 新实验先实现 `lab_workflows` 契约，再注册到 `lab_workflows/experiments/registry.py`。
- 旧脚本当前可经兼容适配器执行；逐个维护时应继续迁移成共享工作流和薄入口。
- “RF 频率响应（DirectAW）”的“任意波波形文件”参数会在每次打开配置页时
  自动扫描 `experiments/` 目录中的 `*.csv` 文件，并以下拉菜单供选择。

实验中心标签可在页面右上角“管理分类与实验信息”中新增或重命名。进入编辑模式后，
每张实验卡片都可以修改该实验自己的显示名称和简要介绍。卡片底部的“标签”选择框用于调整归类。标签、实验归属、显示名称和介绍保存在
`params/experiment_catalog.yaml`，只影响 GUI 组织方式，不改变实验稳定 ID、
运行目录或采集逻辑。
