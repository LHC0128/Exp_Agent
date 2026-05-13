# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言要求

与本仓库相关的一切对话和沟通，请使用**简体中文**。

## 项目概述

这是一个实验项目，涵盖实验设计、数据记录、数据分析等多个环节。项目由我和用户协作推进，共同维护。

## 代码规范

- Python 绘图的所有标注（坐标轴标签、图例、标题、注释等）使用**英文**，便于图表在论文/报告中的复用。
- 其他代码注释、文档字符串、提交信息等使用**中文**（遵循语言要求）。

## 目录结构

```
src/
  sds_acquisition/       # 示波器控制（已实现）
  signal_generator/      # 信号发生器（预留）
  lockin_amplifier/      # 锁相放大器（预留）
  experiments/           # 实验脚本（预留）
examples/                # Jupyter notebook 示例
data/                    # 原始数据
params/                  # 实验参数、配置
results/                 # 实验结果（图片、图表等）
manuals/                 # 设备编程手册、技术文档
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
