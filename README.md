# 实验自动化平台

实验设计、数据采集、数据分析的自动化平台。支持多种实验室仪器的 Python 控制。

## 项目结构

```
src/
  sds_acquisition/       # SDS 系列示波器控制
  signal_generator/      # DG4000 系列信号发生器控制
  lockin_amplifier/      # HF2 锁相放大器控制
  experiments/           # 实验脚本（预留）
examples/                # Jupyter notebook 示例
data/                    # 原始数据
params/                  # 实验参数、YAML 配置文件
results/                 # 实验结果（图片、图表等）
manuals/                 # 设备编程手册、技术文档
docs/                    # 模块文档
```

## 模块文档

| 模块 | 仪器 | 协议 | 文档 |
|---|---|---|---|
| `sds_acquisition` | SDS 系列示波器 | PyVISA + SCPI | [📄 文档](docs/sds_acquisition.md) · [📓 Notebook](examples/data_acquisition_demo.ipynb) |
| `signal_generator` | DG4000 系列信号发生器 | PyVISA + SCPI | [📄 文档](docs/signal_generator.md) · [📓 Notebook](examples/signal_generator_demo.ipynb) |
| `lockin_amplifier` | Zurich Instruments HF2 锁相放大器 | LabOne API (zhinst) | [📄 文档](docs/lockin_amplifier.md) · [📓 Notebook](examples/lockin_amplifier_demo.ipynb) |
| `gs200` | Yokogawa GS200 直流电压/电流源 | PyVISA + SCPI | [📄 文档](docs/gs200.md) · [📓 Notebook](examples/gs200_demo.ipynb) |
| `tec_controller` | 光测未来 TEC103 温控器 | 串口 (ASCII) | [📄 文档](docs/tec_controller.md) · [📓 Notebook](examples/tec_controller_demo.ipynb) |

## 环境

- Python 虚拟环境：`agent_exp_env\`
- Python 可执行文件：`agent_exp_env\Scripts\python.exe`
- 安装包：`agent_exp_env\Scripts\pip install <package>`
- 导出依赖：`agent_exp_env\Scripts\pip freeze > requirements.txt`
- 安装本地包：`agent_exp_env\Scripts\pip install -e .`

所有仪器控制包已通过 `pip install -e .` 安装到虚拟环境，可直接 `import` 使用。
