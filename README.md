# 实验自动化平台

实验设计、数据采集、数据分析的自动化平台。支持多种实验室仪器的 Python 控制，基于 Bell-Bloom 磁力仪构型进行自旋压缩与量子噪声标定实验。

## 坐标系

| 轴 | 物理量 | 说明 |
|----|--------|------|
| **Z** | 主磁场 $B$ | GS200 电流源控制，$\Omega_L/2\pi\approx 90$ kHz |
| **X** | 光传播方向 | Pump / Probe 光沿 X 传播 |
| **Y** | RF 线圈 | 激发横向自旋，用于 MORS 谱 / T₂ FID 测量 |

## 项目结构

```
src/
  sds_acquisition/       # SDS 系列示波器控制
  signal_generator/      # DG4000 / DG900 系列信号发生器控制
  lockin_amplifier/      # HF2 锁相放大器控制（含 DAQ 模块）
  gs200/                 # Yokogawa GS200 直流电压/电流源
  tec_controller/        # 光测未来 TEC103 温控器
experiments/             # 实验 Jupyter Notebook
examples/                # 设备使用示例 Notebook
data/                    # 原始数据（按实验类型分目录）
params/                  # 实验参数、YAML 配置文件
  mapping.yaml           # 物理量 ↔ 仪器通道映射
  safety_limits.yaml     # 各物理量安全限值
results/                 # 实验分析结果（图片、图表等）
manuals/                 # 设备编程手册、技术文档
docs/                    # 模块文档 + 实验类型文档
```

## 模块文档

| 模块 | 仪器 | 协议 | 文档 |
|---|---|---|---|
| `sds_acquisition` | SDS 系列示波器 | PyVISA + SCPI | [📄 文档](docs/sds_acquisition.md) · [📓 Notebook](examples/data_acquisition_demo.ipynb) |
| `signal_generator` | DG4000 / DG912 Pro 信号发生器 | PyVISA + SCPI | [📄 文档](docs/signal_generator.md) · [📓 Notebook](examples/signal_generator_demo.ipynb) |
| `lockin_amplifier` | Zurich Instruments HF2 锁相放大器 | LabOne API (zhinst) | [📄 文档](docs/lockin_amplifier.md) · [📓 Notebook](examples/lockin_amplifier_demo.ipynb) |
| `gs200` | Yokogawa GS200 直流电压/电流源 | PyVISA + SCPI | [📄 文档](docs/gs200.md) · [📓 Notebook](examples/gs200_demo.ipynb) |
| `tec_controller` | 光测未来 TEC103 温控器 | 串口 (ASCII) | [📄 文档](docs/tec_controller.md) · [📓 Notebook](examples/tec_controller_demo.ipynb) |

## 实验 Notebook

### 噪声与耦合标定

| 实验 | Notebook | 文档 | 说明 |
|------|----------|------|------|
| 光散粒噪声标定 | `Photon_shot_noise.ipynb` | [📄](docs/Photon_shot_noise.md) | 扫描 Probe 功率，线性拟合提取 $\alpha$, $\beta$ |
| 热态投影噪声标定 | `Projection_noise.ipynb` | [📄](docs/Projection_noise.md) | 两阶段 PSD 法，$\tilde{\kappa}^2$ + PNL + 洛伦兹 T₂ 交叉验证 |
| X/Y 控制噪声谱测量 | `Noise_Spectrum_XY_Ctrl.ipynb` | [📄](docs/noise_spectrum_xy_ctrl.md) | 扫描 XY 幅度测量 PSD，提取可控/不可控噪声谱 ★ |

### 磁场标定

| 实验 | Notebook | 文档 | 说明 |
|------|----------|------|------|
| 静磁场灵敏度测量 | `Static_Magnetic_Field_Sensitivity.ipynb` | [📄](docs/static_mag_sens_v2.md) | 连续扫场测色散线形，计算磁场灵敏度 |
| X/Y 补偿磁场校准 | `XY_Compensation_Calibration.ipynb` | [📄](docs/XY_Compensation_Calibration.md) | 二维扫描找 R 最大点，消除横磁场 |
| Z 磁场频率标定 | `Z_Field_Calibration.ipynb` | [📄](docs/z_field_calibration.md) | 多点 V→B 线性回归标定 |
| X/Y 通道校准 | `XY_Channel_Calibration.ipynb` | — | X/Y 方向磁场通道输出校准 |
| X/Y AM 传输 | `XY_AM_Transfer.ipynb` | — | AM 调制传输特性验证 |
| X/Y MOD 零偏 | `XY_MOD_ZeroOffset.ipynb` | — | 调制零偏标定 |
| X/Y 输出验证 | `XY_Output_Verification.ipynb` | — | X/Y 输出精度验证 |

### 自旋参数标定

| 实验 | Notebook | 文档 | 说明 |
|------|----------|------|------|
| 自旋极化度标定 (MORS) | — | [📄](docs/MORS_polarization.md) | MORS 谱多峰拟合，得 $\rho_{m,m} \to P$ |
| MORS 可行性验证 | `MORS_feasibility_test_v2.ipynb` | — | Bell-Bloom 调制扫频，外推多峰可分辨 B 场 |
| 纵向弛豫 T₁ 标定 | — | [📄](docs/T1_calibration.md) | Faraday 旋光 $\theta_F$ 指数衰减 → T₁ |
| 横向弛豫 T₂ 标定 | — | [📄](docs/T2_relaxation.md) | FID 时域法 + MORS 频域法交叉验证 |

### 射频场

| 实验 | Notebook | 文档 | 说明 |
|------|----------|------|------|
| RF 场灵敏度 | `RF_Field_Sensitivity.ipynb` | [📄](docs/rf_field_measurement.md) | RF 场灵敏度标定 |
| RF 场灵敏度 (AW) | `RF_Field_Sensitivity_AW.ipynb` | [📄](docs/rf_field_measurement_2.md) | 任意波调制 RF 场灵敏度 |

### 采集与分析分离

所有实验 Notebook 采用**数据采集与数据分析分离**架构：
- **数据采集 Cell**：仅执行扫描 + 保存原始 `.npz` 文件
- **数据分析 & 绘图 Cell**：自包含，优先从 `.npz` 文件加载数据
- 重启 kernel 后仍可复现分析结果，数据与代码解耦

## 配置管理

两个全局 YAML 配置文件，所有实验共享：

| 文件 | 用途 | 示例 |
|------|------|------|
| `params/mapping.yaml` | 物理量 → 仪器通道映射 | `main_magnetic_field` → GS200, `rf_coil` → DG4000 CH2 (Y方向) |
| `params/safety_limits.yaml` | 各物理量安全范围 | `main_magnetic_field`: 0–10 mA, `rf_coil`: 0–2 V |

修改配置文件后，所有实验 Notebook 自动适配，无需逐一修改代码。

## 代码规范

- 绘图标注（坐标轴、图例、标题等）使用**英文**，便于论文复用
- 代码注释、文档字符串、提交信息使用**中文**
- 实验参数在 Cell 顶部集中定义，使用 `ALL_CAPS` 命名
- 所有输出量设置前调用 `validate_safety_limit()`
- 扫描循环用 `try/finally` 包裹，确保异常时设备恢复安全状态
- 幅度扫描使用 `set_amplitude()` 而非 `setup_sine()`，避免 Burst 退出和相位跳变

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
