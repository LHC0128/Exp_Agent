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

experiments/
  *.ipynb                 # 交互式实验 Notebook
  *.py                    # 采集脚本
  *_plot.py               # 分析与绘图脚本
  control_waveform*.csv   # 任意波 / 控制包络

examples/                 # 设备最小示例
docs/                     # 模块文档、实验文档、手册摘录
params/                   # YAML 配置（映射与安全限值）
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
| `sensitivity_analysis` | 分析工具 | 拟合、灵敏度计算、结果汇总 |

相关说明可见：

- `docs/sds_acquisition.md`
- `docs/signal_generator.md`
- `docs/lockin_amplifier.md`
- `docs/gs200.md`
- `docs/tec_controller.md`

## 实验组织方式

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

## 当前实验清单

| 方向 | 主要入口 | 相关文档 |
|---|---|---|
| 光散粒噪声 | `experiments/Photon_shot_noise.py` | `docs/Photon_shot_noise.md` |
| 热态投影噪声 | `experiments/Projection_noise.py` | `docs/Projection_noise.md` |
| 静磁场灵敏度 | `experiments/Static_Magnetic_Field_Sensitivity.py`、`experiments/Static_Magnetic_Field_Sensitivity_Optimize.py` | `docs/static_mag_sens_v2.md` |
| T1 / T2 标定 | `experiments/T1_Calibration.py`、`experiments/T2_Calibration.py` | `docs/T1_calibration.md`、`docs/T2_relaxation.md` |
| X/Y 补偿与通道验证 | `experiments/XY_Compensation_Calibration.ipynb`、`experiments/XY_Channel_Calibration.ipynb`、`experiments/XY_AM_Transfer.ipynb`、`experiments/XY_MOD_ZeroOffset.ipynb`、`experiments/XY_Output_Verification.ipynb` | `docs/XY_Compensation_Calibration.md`、`docs/z_field_calibration.md` |
| 噪声谱测量 | `experiments/Noise_Spectrum_XY_Ctrl.ipynb`、`experiments/Noise_Spectrum_XY_Ctrl_v2.ipynb` | `docs/noise_spectrum_xy_ctrl.md` |
| MORS 相关 | `experiments/MORS_feasibility_test.ipynb`、`experiments/MORS_feasibility_test_v2.ipynb`、`experiments/MORS_polarization_pulsed.ipynb` | `docs/MORS_polarization.md` |
| RF 场灵敏度 Notebook | `experiments/RF_Field_Sensitivity.ipynb`、`experiments/RF_Field_Sensitivity_AW.ipynb` | `docs/rf_field_measurement.md`、`docs/rf_field_measurement_2.md` |
| RF 场频率响应（AW 包络） | `experiments/RF_Field_Sensitivity_AW_FreqSweep.py`、`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py` | `docs/rf_field_measurement_2.md` |
| RF 场频率响应（XY 恒定场） | `experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py`、`experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py` | 暂无独立文档，可参考 `docs/rf_field_measurement_2.md` |
| XY DC 电压标定 | `experiments/XY_DC_Voltage_Calibration.py`、`experiments/XY_DC_Voltage_Calibration_plot.py` | `docs/xy_dc_voltage_calibration.md` |

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

## 配置管理

两个全局 YAML 配置文件由所有实验共享：

| 文件 | 用途 |
|---|---|
| `params/mapping.yaml` | 物理量到仪器通道的映射 |
| `params/safety_limits.yaml` | 输出量安全上下限 |

所有实验在设置输出量前都应调用 `validate_safety_limit()`。

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
- 实验参数集中放在脚本或 Cell 顶部，使用全大写命名
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
