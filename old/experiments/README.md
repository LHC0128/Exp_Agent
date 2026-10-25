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
| `aw-const-omega-awscale-check` | `experiments/AW_ConstOmega_Calibration_Check_AWScale*.py` |
| `dg-am-comp-transfer-check` | `experiments/DG_AM_to_DG_COMP_Transfer_Check*.py` |
| `rf-sensitivity-aw-frequency` | `experiments/RF_Field_Sensitivity_AW_FreqSweep.py`，以及共用 plot 入口的原始副本 |
| `temperature-switch-pid-cycle` | `experiments/Temperature_Switch_PID_Cycle_Test.py`、`experiments/Temperature_Switch_PID_Cycle_Test_plot.py` |
| `aw-waveform-scope-check` | `experiments/AW_Control_Waveform_Scope_Check.py`（原脚本包含诊断绘图，无独立分析入口） |
| `static-sensitivity-optimize` | `experiments/Static_Magnetic_Field_Sensitivity_Optimize.py`（原脚本包含分析和诊断图，无独立分析入口） |
| `static-sensitivity-optimize-local` | `lab_workflows/experiment_modules/static_sensitivity_optimize_local/`、同名采集／分析薄入口及专属 GUI 快照 |
| `aw-const-omega-check` | `experiments/AW_ConstOmega_Calibration_Check.py`、`experiments/AW_ConstOmega_Calibration_Check_plot.py` |
| `xy-dc-voltage-calibration` | `experiments/XY_DC_Voltage_Calibration.py`、`experiments/XY_DC_Voltage_Calibration_plot.py` |
| `xy-aw-voltage-calibration` | `experiments/XY_AW_Voltage_Calibration.py`、`experiments/XY_AW_Voltage_Calibration_plot.py` |

采集和分析入口统一在本目录的 `experiments/` 下。RF R 的后端服务、专属接口快照、API 测试，以及前端测试、fixture 和原共用页面快照分别在 `GUI/backend/`、`GUI/frontend/src/pages/`。接口及页面快照仅供恢复参考，不会被当前 GUI 注册或构建；其相对导入需在接回原目录后核对。

## 当前边界

- GUI 的“已归档”只显示静态卡片，保留名称、介绍及分类编辑，不提供运行、参数、历史或重新分析页面；旧 API 请求返回 410。不能只点“恢复”就重新启用代码。
- 静态卡片元数据在 [`lab_workflows/experiments/archived.py`](../../lab_workflows/experiments/archived.py)。默认实验注册列表不加载这些实现；完整目录可用 `list_experiments(include_archived=True)` 读取卡片。
- [`params/experiments/`](../../params/experiments/)、[`data/`](../../data/) 和 [`results/`](../../results/) 保持原位置，稳定 ID、参数键、数据格式及历史运行均未改写。Notebook 不在本次迁移范围内。
- DG AM 传递检查与任意波示波器检查均已归档；后者仍按原逻辑读取前者的历史传递曲线，数据不移动、算法不修改。`AW_Control_Waveform_Scope_Check.py` 按原字节保留，不进行模块化或流程图迁移，也不新增离线分析器。其共享输入 `experiments/control_waveformAW.csv` 保持原位；原 `WAIT_BEFORE_EXIT` / `KEEP_OUTPUTS_AFTER_EXIT` 及输出策略不变，归档脚本仍会操作真实仪器，不能作为离线测试或安全收尾工具运行。
- 外部 AM 频响的采集脚本已移入此处；`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py` 及其 DirectAW 分析模块仍供在维护的 `rf-sensitivity-direct-aw-frequency` 使用，不随归档移走。此处另保留共用 plot 入口的原始副本，供旧 Python 兼容调用与恢复参考；未实现模块化迁移或更改原扫描、保存及科学算法。
- 温控开关 PID 周期测试的采集和离线分析脚本均按原字节归档；专用 TEC 启停、初始判稳、PID 设置、周期时序和收尾规则原样保留，普通采集时序例外也不删除。它不再提供 GUI 运行或历史入口，不能把归档脚本当作无硬件测试。
- 全局静磁场优化、恒定 Omega 检查、XY DC 与 XY AW 电压标定的 7 份脚本按原字节归档，不进行模块化或流程图迁移。三对采集／分析脚本保留原科学算法，全局优化仍没有独立分析入口。全局优化实际写入的 `data/Static_Magnetic_Field_Sensitivity/` 与主实验、局部优化共享历史，整个目录保持原位；卡片继续保留原注册 `data_type`，不借归档改写历史身份。局部优化 `static-sensitivity-optimize-local` 也已归档：22 份专属实现、采集／分析薄入口、正式回归、原图及 GUI 快照按归档前字节保留，当前后端不再加载其会话、格式处理或专属路由。主实验 `static-sensitivity` 与 `xy-direct-aw-dc-calibration` 继续维护；共用驱动、标准 DC 工作流、分析和波形 CSV 不移动。
- 在用 XY 相位响应实验仍复用的 `mx_keithley_6221_optimal_control_rf_sensitivity/phase.py` 原位保留，归档中也保留其原始副本。共享驱动和算法不随专属实验移走。
- 为兼容已有 Python 导入，`lab_workflows.experiment_modules` 追加此处的包搜索路径；`get_experiment(id)` 仅在显式请求归档 ID 时按需加载旧定义。这不是 GUI 运行入口，导入/schema 检查不连接仪器。

## 以后恢复

1. 先核对原路径与当前修改，不覆盖仍在使用的共享代码；按本目录结构把需要恢复的实现接回主目录。
2. 从静态归档卡片移出对应 ID，把定义接回正式注册列表；有专属 GUI 时再恢复页面、服务、接口及生命周期收尾接线。RF R 的共用页面快照不是当前页面的替代品，须逐项合并。
3. 确认正式回归仍完整，重新生成 API 类型并完成无硬件 Python、API、前端测试和生产构建后，再解除目录归档标记。不要通过改目录配置绕过代码恢复。
4. 对真实硬件的运行仍需另行授权；不要把归档脚本当作无硬件检查执行。
