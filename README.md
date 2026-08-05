# 实验自动化平台

面向 Bell-Bloom 磁力仪实验的自动化仓库，覆盖仪器控制、参数扫描、原始数据采集、离线分析与绘图。仓库同时保留交互式 Notebook 和可重复运行的 Python 脚本，适合实验调试、批量采集和后处理复现。

## 坐标系

| 轴 | 物理量 | 说明 |
|----|--------|------|
| **Z** | 主磁场 $B$ | 由 GS200 控制，对应 Larmor 频率主轴 |
| **X** | 光传播方向 | Pump / Probe 光沿 X 传播 |
| **Y** | RF / 横向控制方向 | 用于横向自旋激发、补偿场和调制控制 |

## 仓库结构

```text
src/
  gs200/                  # GS200 控制
  lockin_amplifier/       # HF2 控制与 DAQ 接口
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

examples/                 # 设备最小示例
docs/                     # 模块文档、实验文档、手册摘录
params/                   # YAML 配置（映射与安全限值）
  experiments/            # 正式实验的版本化默认参数
lab_workflows/            # GUI 与命令行共用的实验契约、参数模型和安全步骤
  experiment_modules/     # 新模式实验的模型、采集工作流与离线分析器
  plotting/               # 论文图（默认）与 A0 海报共享绘图规范
GUI/                      # 本地 Web 实验控制台
data/                     # 原始实验数据
results/                  # 结果图表
总结/                     # 阶段性总结
```

## 核心模块

| 模块 | 设备 / 能力 | 说明 |
|---|---|---|
| `sds_acquisition` | SDS 系列示波器 | 波形采集、保存、CLI 支持 |
| `signal_generator` | DG4000 / DG900 | 正弦、Burst、AM、任意波等控制 |
| `lockin_amplifier` | Zurich Instruments HF2 | Demod、DAQ、AuxOut 配置 |
| `gs200` | Yokogawa GS200 | 主磁场电流源控制 |
| `tec_controller` | TEC103 | 温控器串口控制 |
| `toptica_laser` | TOPTICA DLC pro | Probe 激光电流、温度、PZT 与扫描控制 |
| `sensitivity_analysis` | 分析工具 | 拟合、灵敏度计算、结果汇总 |

常规正式实验将 TEC103 视为可选控制设备：若 `COM3` 已被 TEC 桌面软件占用，
实验会警告并跳过设温与稳定等待，其他采集流程继续运行。此时请在外部软件中
确认温度；TEC/PID 专项实验仍要求独占串口。详见
[`docs/tec_controller.md`](docs/tec_controller.md)。

相关说明可见：

- `docs/sds_acquisition.md`
- `docs/signal_generator.md`
- `docs/lockin_amplifier.md`
- `docs/gs200.md`
- `docs/tec_controller.md`
- `docs/toptica_dlc_pro.md`

## GUI 基础使用

GUI 是运行在实验电脑本机的 Web 控制台，可用于读取和设置 GS200 主磁场电流源、
TOPTICA DLC pro Probe 激光、DG4000、DG900 Pro、SDS 示波器等仪器，并运行仓库中
已接入的实验流程。

### 首次安装

在仓库根目录 `D:\Code\exp_agent` 打开 PowerShell，安装后端和前端依赖：

```powershell
agent_exp_env\Scripts\pip.exe install -r GUI\requirements.txt
Set-Location GUI\frontend
npm install
npm run build
Set-Location ..\..
```

### 启动 GUI

在仓库根目录运行：

```powershell
.\GUI\start.ps1
```

启动成功后，浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。
服务只监听本机地址，启动 GUI 的 PowerShell 窗口需要保持打开。

如果 PowerShell 不允许运行脚本，可改用：

```powershell
Set-Location D:\Code\exp_agent\GUI
..\agent_exp_env\Scripts\python.exe -m backend
```

### 基本操作

1. 进入“仪器控制”页面，选择左侧设备；页面会自动回读设备当前参数。
2. 使用“读取设备参数”按钮可再次刷新仪器状态。
3. 信号发生器可在“基础波形”“调制”或“Burst”标签中修改参数；GS200 只开放主磁场电流设定值和输出开关；DLC pro 开放电流、温度、PZT、扫描幅度和扫描启停，扫描频率只读。
4. 点击“应用并回读”；写入前会检查 `params/safety_limits.yaml`，写入后以仪器实际回读值更新页面。GS200 从 OFF 切换到 ON 时会二次确认；DLC pro 的远程 Emission ON 默认禁止，安全流程见 `docs/toptica_dlc_pro.md`。
5. 实验结束后，在启动 GUI 的 PowerShell 窗口按 `Ctrl+C` 停止服务。

“实验中心”统一展示 37 个正式 Python 采集入口，并提供动态参数、默认值保存、
无副作用预检、运行日志、安全停止和离线重新分析。每张实验卡片同时显示对应的
采集程序和独立分析程序；没有独立分析脚本时会明确标注。`*.ipynb` 不进入实验中心，
`*_plot.py` 只作为对应实验的分析器。卡片还会明确显示“新模式”或“旧模式”；完整
迁移状态见 `docs/experiment_migration_status.md`。

同一时间只能有一个进程占用 8000 端口。如果提示端口已被占用，应先停止旧的 GUI
进程，再重新运行启动命令。更详细的开发说明见 `GUI/README.md`。

## 实验组织方式

GUI 与命令行薄入口共同调用 `lab_workflows/`：

```text
GUI ───────────┐
               ├─→ 实验注册表 → 共享实验步骤 → src 仪器驱动
experiments ───┘
```

以后修改实验步骤，应修改共享工作流；新模式的 `experiments/*.py` 只保留命令行调用。
新增或修改实验时使用仓库级 `expcodegen` Skill，并运行其中的验证器。

正式实验有两种明确执行模式：

- `typed_workflow`：参数字段由 `ExperimentParams` 子类显式声明，模型内部使用
  `snake_case`；GUI/YAML 通过 `external_name` 继续使用稳定的大写键或
  `FIXED_PARAMS.*` 旧键。新增实验和迁移实验都采用此模式。
- `legacy_script`：过渡期兼容模式，仍从旧脚本常量生成参数并通过隔离适配器执行。
  它不会被当作新实验模板。

当前共有 19 个新模式实验和 18 个旧模式实验。GUI 参数表单只展示新模式模型显式
声明的字段，不会因为工作流中新增一个全大写运行时常量而意外增加表单项目。

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
- `RF_Field_Sensitivity_AW_FreqSweep.py` / `RF_Field_Sensitivity_AW_FreqSweep_plot.py`
- `RF_Field_Sensitivity_ConstXY_FreqSweep.py` / `RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py`
- `XY_DC_Voltage_Calibration.py` / `XY_DC_Voltage_Calibration_plot.py`
- `XY_DirectAW_DC_Calibration.py` / `XY_DirectAW_DC_Calibration_plot.py`
- `Noise_Spectrum_XY_Demod3_R.py` / `Noise_Spectrum_XY_Demod3_R_plot.py`
- `Mx_Y_RF_Sensitivity.py` / `Mx_Y_RF_Sensitivity_plot.py`
- `Mx_Z_Optimal_Control_RF_Sensitivity.py` / `Mx_Z_Optimal_Control_RF_Sensitivity_plot.py`
- `Mx_Z_Optimal_Control_XY_Leakage_Response.py` / `Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py`
- `Mx_Z_Optimal_Control_XYZ_Balance.py` / `Mx_Z_Optimal_Control_XYZ_Balance_plot.py`
- `Mx_Y_RF_Power_Optimization.py` / `Mx_Y_RF_Power_Optimization_plot.py`
- `Mx_Y_RF_Probe_Detuning_Optimization.py` / `Mx_Y_RF_Probe_Detuning_Optimization_plot.py`
- `Mx_Main_Field_Calibration.py` / `Mx_Main_Field_Calibration_plot.py`
- `Mx_Main_Field_Noise_Spectrum.py` / `Mx_Main_Field_Noise_Spectrum_plot.py`
- `Mx_Main_Field_Scope_Noise_Spectrum.py` / `Mx_Main_Field_Scope_Noise_Spectrum_plot.py`
- `Mx_Z_Field_Calibration.py` / `Mx_Z_Field_Calibration_plot.py`
- `Mx_Z_Noise_Spectrum.py` / `Mx_Z_Noise_Spectrum_plot.py`
- `Mx_XY_Residual_Field_Calibration.py` / `Mx_XY_Residual_Field_Calibration_plot.py`

## 当前实验清单

| 方向 | 主要入口 | 相关文档 |
|---|---|---|
| 光散粒噪声 | `experiments/Photon_shot_noise.py` | `docs/Photon_shot_noise.md` |
| 原子自旋投影噪声（SDS） | `experiments/Projection_noise.py`、`experiments/Projection_noise_plot.py` | `docs/Projection_noise.md` |
| 静磁场灵敏度 | `experiments/Static_Magnetic_Field_Sensitivity.py`、`experiments/Static_Magnetic_Field_Sensitivity_Optimize.py` | `docs/static_mag_sens_v2.md` |
| Mx Y 向 RF 场灵敏度 | `experiments/Mx_Y_RF_Sensitivity.py`、`experiments/Mx_Y_RF_Sensitivity_plot.py` | `docs/mx_y_rf_sensitivity.md` |
| Mx Z 最优控制 RF 灵敏度（X DC + Y RF 偏置平衡剩磁场；Y RF 非零时使用 Demod0 X/Y 成对正交校相） | `experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py`、`experiments/Mx_Z_Optimal_Control_RF_Sensitivity_plot.py` | `docs/mx_z_optimal_control_rf_sensitivity.md` |
| Mx Z 最优控制 XY 泄露响应（二维扫描同形 X/Y 触发任意波并报告实测最小 R 网格点） | `experiments/Mx_Z_Optimal_Control_XY_Leakage_Response.py`、`experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py` | `docs/mx_z_optimal_control_xy_leakage_response.md` |
| Mx Z 最优控制 XYZ 平衡场（X/Y DG4000 DC + Z GS200 三维扫描，以实测 Demod0 R 最小点为结果） | `experiments/Mx_Z_Optimal_Control_XYZ_Balance.py`、`experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py` | `docs/mx_z_optimal_control_xyz_balance.md` |
| Mx Y RF 光功率灵敏度优化（含逐点诊断图、tqdm ETA、拟合导数与零点实测斜率双排名） | `experiments/Mx_Y_RF_Power_Optimization.py`、`experiments/Mx_Y_RF_Power_Optimization_plot.py` | `docs/mx_y_rf_power_optimization.md` |
| Mx Y RF Probe 光功率与 PZT 失谐灵敏度优化（支持固定 Probe 单点；不连接 TEC，保留温控开关门控） | `experiments/Mx_Y_RF_Probe_Detuning_Optimization.py`、`experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py` | `docs/mx_y_rf_probe_detuning_optimization.md` |
| Mx 主磁场频率标定 | `experiments/Mx_Main_Field_Calibration.py`、`experiments/Mx_Main_Field_Calibration_plot.py` | `docs/mx_main_field_calibration.md` |
| Mx 主磁场控制噪声谱 | `experiments/Mx_Main_Field_Noise_Spectrum.py`、`experiments/Mx_Main_Field_Noise_Spectrum_plot.py` | `docs/mx_main_field_noise_spectrum.md` |
| Mx 主磁场示波器噪声谱（固定 X/Y DC 补偿，可选 AC/DC 耦合） | `experiments/Mx_Main_Field_Scope_Noise_Spectrum.py`、`experiments/Mx_Main_Field_Scope_Noise_Spectrum_plot.py` | `docs/mx_main_field_scope_noise_spectrum.md` |
| Mx 高主场 Z 磁场频率标定 | `experiments/Mx_Z_Field_Calibration.py`、`experiments/Mx_Z_Field_Calibration_plot.py` | `docs/mx_z_field_calibration.md` |
| Mx Z 直流控制噪声谱 | `experiments/Mx_Z_Noise_Spectrum.py`、`experiments/Mx_Z_Noise_Spectrum_plot.py` | `docs/mx_z_noise_spectrum.md` |
| Mx XY 剩磁二维校准（SDS） | `experiments/Mx_XY_Residual_Field_Calibration.py`、`experiments/Mx_XY_Residual_Field_Calibration_plot.py` | `docs/mx_xy_residual_field_calibration.md` |
| T1 / T2 标定 | `experiments/T1_Calibration.py`、`experiments/T2_Calibration.py` | `docs/T1_calibration.md`、`docs/T2_relaxation.md` |
| X/Y 补偿与通道验证 | `experiments/XY_Compensation_Calibration.ipynb`、`experiments/XY_Channel_Calibration.ipynb`、`experiments/XY_AM_Transfer.ipynb`、`experiments/XY_MOD_ZeroOffset.ipynb`、`experiments/XY_Output_Verification.ipynb` | `docs/XY_Compensation_Calibration.md`、`docs/z_field_calibration.md` |
| XY 控制噪声谱测量 | `experiments/Noise_Spectrum_XY_Ctrl.py`、`experiments/Noise_Spectrum_XY_Ctrl_plot.py` | `docs/noise_spectrum_xy_ctrl.md` |
| Demod3 R 噪声谱测量 | `experiments/Noise_Spectrum_XY_Demod3_R.py`、`experiments/Noise_Spectrum_XY_Demod3_R_plot.py` | `docs/noise_spectrum_xy_demod3_r.md` |
| MORS 相关 | `experiments/MORS_feasibility_test.ipynb`、`experiments/MORS_feasibility_test_v2.ipynb`、`experiments/MORS_polarization_pulsed.ipynb` | `docs/MORS_polarization.md` |
| RF 场灵敏度 Notebook | `experiments/RF_Field_Sensitivity.ipynb`、`experiments/RF_Field_Sensitivity_AW.ipynb` | `docs/rf_field_measurement.md`、`docs/rf_field_measurement_2.md` |
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
制作 A0 海报面板时显式选择 `a0_poster`。尺寸、配色、导出和科研表达约定见
`docs/plotting_style.md`。

## 配置管理

两个全局 YAML 配置文件由所有实验共享：

| 文件 | 用途 |
|---|---|
| `params/mapping.yaml` | 物理量到设备型号、资源地址和通道的唯一映射 |
| `params/safety_limits.yaml` | 输出量安全上下限 |
| `params/clock_sources.yaml` | DG4000、DG900 与 HF2 的参考时钟目标 |
| `params/experiments/<experiment-id>.yaml` | 正式实验的版本化默认参数，使用稳定外部键 |

信号发生器条目必须同时配置 `model`、`resource` 和 `channel`：

```yaml
Pump_laser_power:
  instrument: signal_generator
  model: DG900
  resource: USB0::0x1AB1::0x0646::DG9Q280100002::INSTR
  channel: 1
```

正式 Python 实验统一调用 `lab_workflows.devices.create_signal_generator()`，
由工厂读取 `model` 并选择 DG4000 或 DG900 驱动。更换信号发生器时，只需在
`params/mapping.yaml` 更新对应条目的型号、资源地址和通道；实验脚本不再硬编码驱动类。
同一物理资源的多个通道必须配置相同型号，否则设备发现和连接阶段会直接报错。

新模式硬件工作流在设备连接完成后统一调用
`lab_workflows.steps.synchronize_connected_clocks()`，按 `params/clock_sources.yaml`
设置已连接的 DG4000、DG900 和 HF2，并通过回读严格验证；任何设备不一致都会在实验正式输出配置前终止实验。

所有实验在设置输出量前都应调用 `validate_safety_limit()`。
正式硬件工作流统一通过 `lab_workflows/steps/safety_shutdown.py` 声明安全收尾策略：
需要关闭的 DG 通道会依次关闭 Burst/同步/调制、归零为 0 V DC 并关闭输出，随后恢复
温度开关为 5 V ON 并只断开 TEC 通信。默认只保留主磁场、Pump/Probe 光功率、Pump
调制输出和全部 HF2 设置；温控硬件保持运行，其他可控输出全部关闭。实验特定例外必须显式声明；
T2 额外保持 RF 门控 CH2 Output ON，并只关闭该通道的 Burst。

## 环境准备

- Python 要求：`>=3.10`
- 本地包名：`exp-agent`
- 虚拟环境：`agent_exp_env\`

常用命令：

```powershell
agent_exp_env\Scripts\Activate.ps1
agent_exp_env\Scripts\pip install -e .
agent_exp_env\Scripts\pip install pyvisa pyvisa-py pyserial zhinst numpy scipy matplotlib pyyaml jupyter
```

如果只想同步当前环境，也可以使用：

```powershell
agent_exp_env\Scripts\pip freeze > requirements.txt
```

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
- `.agents/skills/expcodegen/SKILL.md`
- `.claude/skills/expcodegen/SKILL.md`

这些说明会要求 Agent 在生成或修改实验代码时读取：

- `params/mapping.yaml`
- `params/safety_limits.yaml`
- `docs/*.md`

从而尽量保证实验脚本、文档和安全约束保持一致。
