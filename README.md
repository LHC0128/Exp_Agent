# 实验自动化平台

实验设计、数据采集、数据分析的自动化平台。支持多种实验室仪器的 Python 控制。

## 项目结构

```
src/
  sds_acquisition/       # SDS 系列示波器控制
  signal_generator/      # DG4000 系列信号发生器控制
  lockin_amplifier/      # HF2 锁相放大器控制
  gs200/                 # Yokogawa GS200 直流电压/电流源
  tec_controller/        # 光测未来 TEC103 温控器
experiments/             # 实验 Jupyter Notebook
  Static_Magnetic_Field_Sensitivity.ipynb   # 静磁场灵敏度测量
  XY_Compensation_Calibration.ipynb        # X/Y 补偿磁场校准
  Z_Field_Calibration.ipynb                # Z 磁场频率标定
examples/                # 设备使用示例 Notebook
data/                    # 原始数据（按实验类型分目录）
params/                  # 实验参数、YAML 配置文件
  mapping.yaml           # 物理量↔仪器通道映射
  safety_limits.yaml     # 各物理量安全限值
results/                 # 实验分析结果（图片、图表等）
manuals/                 # 设备编程手册、技术文档
docs/                    # 模块文档 + 实验类型文档
```

## 模块文档

| 模块 | 仪器 | 协议 | 文档 |
|---|---|---|---|
| `sds_acquisition` | SDS 系列示波器 | PyVISA + SCPI | [📄 文档](docs/sds_acquisition.md) · [📓 Notebook](examples/data_acquisition_demo.ipynb) |
| `signal_generator` | DG4000 系列信号发生器 | PyVISA + SCPI | [📄 文档](docs/signal_generator.md) · [📓 Notebook](examples/signal_generator_demo.ipynb) |
| `lockin_amplifier` | Zurich Instruments HF2 锁相放大器 | LabOne API (zhinst) | [📄 文档](docs/lockin_amplifier.md) · [📓 Notebook](examples/lockin_amplifier_demo.ipynb) |
| `gs200` | Yokogawa GS200 直流电压/电流源 | PyVISA + SCPI | [📄 文档](docs/gs200.md) · [📓 Notebook](examples/gs200_demo.ipynb) |
| `tec_controller` | 光测未来 TEC103 温控器 | 串口 (ASCII) | [📄 文档](docs/tec_controller.md) · [📓 Notebook](examples/tec_controller_demo.ipynb) |

## 实验 Notebook

| 实验 | 文档 | 说明 |
|------|------|------|
| 静磁场灵敏度测量 | [📄 文档](docs/static_mag_sens.md) | 连续扫场测色散线形，计算磁场灵敏度 |
| X/Y 补偿磁场校准 | [📄 文档](docs/XY_Compensation_Calibration.md) | 二维扫描找 R 最大点，消除横磁场 |
| Z 磁场频率标定 | [📄 文档](docs/z_field_calibration.md) | 多点 V→B 线性回归标定 |

### 采集与分析分离

所有实验 Notebook 采用**数据采集与数据分析分离**架构：
- **数据采集 Cell**：仅执行扫描 + 保存原始 `.npz` 文件
- **数据分析 & 绘图 Cell**：自包含，优先从 `.npz` 文件加载数据
- 重启 kernel 后仍可复现分析结果，数据与代码解耦

## 配置管理

两个全局 YAML 配置文件，所有实验共享：

| 文件 | 用途 | 示例 |
|------|------|------|
| `params/mapping.yaml` | 物理量→仪器通道映射 | `main_magnetic_field` → GS200 @ USB0::... |
| `params/safety_limits.yaml` | 各物理量安全范围 | `main_magnetic_field`: min=0, max=10 mA |

修改配置文件后，所有实验 Notebook 自动适配，无需逐一修改代码。

## 环境

- Python 虚拟环境：`agent_exp_env\`
- Python 可执行文件：`agent_exp_env\Scripts\python.exe`
- 安装包：`agent_exp_env\Scripts\pip install <package>`
- 导出依赖：`agent_exp_env\Scripts\pip freeze > requirements.txt`
- 安装本地包：`agent_exp_env\Scripts\pip install -e .`

所有仪器控制包已通过 `pip install -e .` 安装到虚拟环境，可直接 `import` 使用。

### 第三方依赖

```bash
# 仪器通信
agent_exp_env\Scripts\pip install pyvisa pyvisa-py pyserial

# HF2 锁相放大器（LabOne API）
agent_exp_env\Scripts\pip install zhinst

# 数据处理与可视化
agent_exp_env\Scripts\pip install numpy scipy matplotlib pyyaml jupyter
```
