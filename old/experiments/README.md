# 已归档实验代码

这里只归档、不删除。按原仓库相对路径保存专属代码、正式测试和原图；`manifest.json` 记录移动前源文件的 SHA256，可用 `GUI/backend/test_archived_experiments.py` 核对完整性。未提交的修改也按移动当时的内容保留。

## 归档清单

| 稳定 ID | 实现目录（本目录下） |
| --- | --- |
| `mx-keithley-6221-main-field-calibration` | `lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/` |
| `mx-keithley-6221-optimal-control-rf-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/` |
| `mx-keithley-6221-optimal-control-xyz-balance` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/` |
| `mx-keithley-6221-optimal-control-balanced-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/` |
| `mx-main-field-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_noise_spectrum/` |
| `mx-z-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_noise_spectrum/` |
| `noise-spectrum-xy-v2` | `experiments/Noise_Spectrum_XY_Ctrl_v2*.py` |
| `rf-sensitivity-aw-r` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/` |

采集和分析薄入口统一在本目录的 `experiments/` 下。RF R 的后端服务、专属接口快照、API 测试，以及前端测试、fixture 和原共用页面快照分别在 `GUI/backend/`、`GUI/frontend/src/pages/`。接口及页面快照仅供恢复参考，不会被当前 GUI 注册或构建；其相对导入需在接回原目录后核对。

## 当前边界

- GUI 的“已归档”只显示静态卡片，保留名称、介绍及分类编辑，不提供运行、参数、历史或重新分析页面；旧 API 请求返回 410。不能只点“恢复”就重新启用代码。
- 静态卡片元数据在 [`lab_workflows/experiments/archived.py`](../../lab_workflows/experiments/archived.py)。默认实验注册列表不加载这些实现；完整目录可用 `list_experiments(include_archived=True)` 读取卡片。
- [`params/experiments/`](../../params/experiments/)、[`data/`](../../data/) 和 [`results/`](../../results/) 保持原位置，稳定 ID、参数键、数据格式及历史运行均未改写。Notebook 不在本次迁移范围内。
- 在用 XY 相位响应实验仍复用的 `mx_keithley_6221_optimal_control_rf_sensitivity/phase.py` 原位保留，归档中也保留其原始副本。共享驱动和算法不随专属实验移走。
- 为兼容已有 Python 导入，`lab_workflows.experiment_modules` 追加此处的包搜索路径；`get_experiment(id)` 仅在显式请求归档 ID 时按需加载旧定义。这不是 GUI 运行入口，导入/schema 检查不连接仪器。

## 以后恢复

1. 先核对原路径与当前修改，不覆盖仍在使用的共享代码；按本目录结构把需要恢复的实现接回主目录。
2. 从静态归档卡片移出对应 ID，把定义接回正式注册列表；有专属 GUI 时再恢复页面、服务、接口及生命周期收尾接线。RF R 的共用页面快照不是当前页面的替代品，须逐项合并。
3. 确认正式回归仍完整，重新生成 API 类型并完成无硬件 Python、API、前端测试和生产构建后，再解除目录归档标记。不要通过改目录配置绕过代码恢复。
4. 对真实硬件的运行仍需另行授权；不要把归档脚本当作无硬件检查执行。
