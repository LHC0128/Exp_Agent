# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## 注意事项

不要猜测我的意图，有任何不明确的地方必须向我提问。

## 语言要求

与本仓库相关的一切对话和沟通，请使用**简体中文**。

## 项目概述

这是一个实验项目，涵盖实验设计、数据记录、数据分析等多个环节。项目由用户主导，AI 辅助推进。

## 实验代码生成 Skill

本仓库包含一个自定义 AI Skill（`.agents/skills/expcodegen/SKILL.md`），用于根据自然语言描述自动生成实验 Notebook 代码。

调用方式：在与 AI 对话中描述实验方案，Skill 会自动读取 `params/mapping.yaml`、`params/safety_limits.yaml` 以及 `docs/*.md` 实验类型文档，生成完整的 Jupyter Notebook。

## 代码规范

- Python 绘图的所有标注（坐标轴标签、图例、标题、注释等）使用**英文**，便于图表在论文/报告中的复用。
- 其他代码注释、文档字符串、提交信息等使用**中文**（遵循语言要求）。
- 实验参数在 Cell 顶部集中定义，使用全大写命名（如 `SCAN_RANGE`、`FIXED_PARAMS`）。
- 安全限值检查：所有输出量设置前调用 `validate_safety_limit()`。
- 扫描循环用 `try/finally` 包裹，确保异常时设备能恢复安全状态。
- 幅度扫描中使用 `set_amplitude()` 而非 `setup_sine()`，避免重绘波形导致 Burst 模式退出和相位跳变。

## 实验类型文档规范

`docs/*.md` 是实验类型的结构化描述，包含 YAML frontmatter：

```yaml
---
title: 实验名称
type: experiment_type
scan_mode: point_by_point | continuous_ramp | nested_scan
defaults:              # Notebook 默认参数
  PARAM_NAME: value
mapping_keys:          # 涉及物理量及其角色
  key_name:
    role: scan | fixed | detection
required_devices:      # 所需设备清单
learned_notes:         # 修复经验（自动注入代码注释）
---
```

## 目录结构

```
src/
  sds_acquisition/       # 示波器控制 (SDS 系列)
  signal_generator/      # 信号发生器 (DG4000 系列)
  lockin_amplifier/      # 锁相放大器 (Zurich HF2，含 DAQ 模块)
  gs200/                 # 直流电压/电流源 (Yokogawa GS200)
  tec_controller/        # 温控器 (光测未来 TEC103)
experiments/             # 实验 Jupyter Notebook
  Static_Magnetic_Field_Sensitivity.ipynb
  XY_Compensation_Calibration.ipynb
  Z_Field_Calibration.ipynb
  Noise_Spectrum_XY_Ctrl.ipynb           # ★ X/Y 控制噪声谱测量
examples/                # 设备使用示例 Notebook
  data_acquisition_demo.ipynb
  gs200_demo.ipynb
  lockin_amplifier_demo.ipynb
  signal_generator_demo.ipynb
  tec_controller_demo.ipynb
data/                    # 原始数据（按实验类型分目录）
  Static_Magnetic_Field_Sensitivity/
  XY_Compensation_Calibration/
  Z_Field_Calibration/
  Noise_Spectrum_XY_Ctrl/
params/                  # 实验参数、YAML 配置文件
  mapping.yaml           # 物理量↔仪器通道映射
  safety_limits.yaml     # 各物理量安全限值
  experiment_types/      # 实验类型模板（预留）
results/                 # 实验分析结果（图片、图表等）
manuals/                 # 设备编程手册、技术文档
docs/                    # 文档
  experiment_template.md      # 实验方案填写模板
  XY_Compensation_Calibration.md  # X/Y 补偿校准实验文档
  z_field_calibration.md      # Z 磁场标定实验文档
  static_mag_sens_v2.md       # 静磁场灵敏度实验文档
  noise_spectrum_xy_ctrl.md   # X/Y 控制噪声谱测量文档 ★
  gs200.md                    # GS200 模块文档
  signal_generator.md         # DG4000 模块文档
  lockin_amplifier.md         # HF2 模块文档
  sds_acquisition.md          # SDS 示波器模块文档
  tec_controller.md           # TEC103 模块文档
.claude/
  skills/expcodegen/SKILL.md  # 实验代码生成 Skill（Claude Code 用）
.agents/
  skills/expcodegen/SKILL.md  # 实验代码生成 Skill（通用 AI Agent 用）
```

## 单次实验运行目录结构

```
data/<实验类型>/MMDD_HHMM_tag/
  experiment_config.yaml    # 运行时完整配置（可复现用）
  raw/                      # 原始数据
    waveform_C000.npy       # 逐点波形数据（噪声谱实验）
    scan_data.npz           # 粗扫数据（其他实验）
    refine_data.npz         # 精细扫描数据（可选）
  results/                  # 分析结果
    psd_matrix.npz          # PSD 矩阵 + 频率轴（噪声谱实验）
    calibration.npz         # 幅频标定结果
    popt_fit.npz            # 洛伦兹拟合参数
    noise_spectra.npz       # 提取的噪声谱（S_beta, N_S1）
    *.png                   # 图表
    optimal_xy.yaml         # 校准结果（XY 补偿）
    analysis.yaml           # 分析结果（灵敏度等）
```

## 环境

- Python 虚拟环境：`agent_exp_env\`
- Python 可执行文件：`agent_exp_env\Scripts\python.exe`
- 激活虚拟环境：`agent_exp_env\Scripts\Activate.ps1`（PowerShell）或 `agent_exp_env\Scripts\activate.bat`（CMD）
- 安装包：`agent_exp_env\Scripts\pip install <package>`
- 安装本地包（可编辑模式）：`agent_exp_env\Scripts\pip install -e .`
- 导出依赖：`agent_exp_env\Scripts\pip freeze > requirements.txt`

**注意**：`sds_acquisition` 等仪器控制包已通过 `pip install -e .` 安装到虚拟环境中，
可直接 `import`，无需设置 `PYTHONPATH`。

### 第三方依赖

- HF2 锁相放大器：`agent_exp_env\Scripts\pip install zhinst`
- TEC103 温控器：`agent_exp_env\Scripts\pip install pyserial`
- 仪器通信：`agent_exp_env\Scripts\pip install pyvisa pyvisa-py`
- 数据处理：`agent_exp_env\Scripts\pip install numpy scipy matplotlib pyyaml`
