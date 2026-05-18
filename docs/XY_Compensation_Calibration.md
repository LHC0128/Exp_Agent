---
title: X/Y 补偿磁场校准
type: experiment_type
description: 扫描 X/Y 补偿电压找到 R 信号最大处，消除横磁场，展宽线宽、提高灵敏度
keywords: [calibration, XY compensation, transverse field, Bell-Bloom, R signal, lock-in]
version: 1

scan_mode: point_by_point      # 二维逐点扫描

# ========== 默认参数 ==========
defaults:
  # ---- 扫描参数 ----
  X_SCAN_RANGE: 0.05           # ±X 扫描范围 (V) — 小范围
  Y_SCAN_RANGE: 0.2            # ±Y 扫描范围 (V) — 大范围
  XY_SCAN_POINTS: 21           # 每方向扫描点数
  XY_SETTLE_TIME: 0.05         # 每点等待稳定时间 (s)
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 90e3          # Pump 调制频率 (Hz)
  PUMP_MOD_AMPLITUDE: 0.18     # 100MHz 正弦波幅度 (V)
  PUMP_MOD_DUTY: 5             # 门控脉冲占空比 (%)
  RF_GATE_AMPLITUDE: 5.0       # CH2 门控脉冲幅度 (Vpp)
  RF_GATE_OFFSET: 2.5          # CH2 门控脉冲偏置 (V)
  RF_GATE_DELAY: 0.0           # 门控脉冲延迟 (s)
  # ---- HF2 解调参数 ----
  HF2_DEMOD_IDX: 0             # 解调器索引
  HF2_OSC_FREQ: 90e3           # 振荡器频率 (Hz)
  HF2_DEMOD_ORDER: 4           # 解调滤波器阶数
  HF2_SIGNAL_RANGE: 2.0        # 信号输入范围 (V)
  HF2_DEMOD_RATE: 1000         # 解调输出速率 (Sa/s)
  HF2_DEMOD_TC: 0.001          # 解调时间常数 (s)

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  X_magnetic_field:
    role: scan_x
    description: "DG4000 CH1 输出 X 补偿电压，二维扫描的 X 方向"
  Y_magnetic_field:
    role: scan_y
    description: "DG4000 CH2 输出 Y 补偿电压，二维扫描的 Y 方向"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流），校准过程中固定不变"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率"
  Probe_laser_power:
    role: fixed
    description: "Probe 光功率"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，每行 Y 扫描时关闭以消除温控磁场干扰"
  Pump_modulation:
    role: pump_mod
    description: "Pump 调制: CH1 100MHz 正弦 → RF 开关 IN, CH2 脉冲门控 → RF 开关 CTRL"
  lockin_r:
    role: detection
    description: "HF2 锁相，读 R 信号作为横磁场大小的判据"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - temperature
  - Temp_Switch

# ========== 修复经验 ==========
learned_notes:
  - X/Y 补偿线圈输出 ON 时即使设为 0V 也会耦合环境噪声，找到最优值后保持 ON
  - 每行 Y 扫描前关闭温度开关，扫描完成后立即恢复，避免温度漂移
  - 如果最优值落在扫描边界上，说明扫描范围不够大，需增大后重新扫描
  - HF2 相位校准需要在 Pump 光打开的条件下进行
  - 扫描代码须用 try/finally 包裹，确保异常时温度开关能恢复
  - 每行扫描前对 Vx/Vy 做 validate_safety_limit 检查
---

# X/Y 补偿磁场校准

## 原理

横磁场（X/Y 方向）会引入额外弛豫通道 → 展宽线宽 → 降低灵敏度。通过扫描补偿电压找到 R 最大点 → 横磁场被最小化。

总磁场为矢量叠加：

$$B_\text{total} = \sqrt{(B_z + \Delta B_z)^2 + (B_x + \Delta B_x)^2 + (B_y + \Delta B_y)^2}$$

X/Y 补偿电压共同决定横磁场大小，需要二维扫描找 R 信号的全局最大值。

## 数据采集方式

使用 DG4000 的 DC 输出控制 X/Y 补偿电压，逐点扫描二维网格，每点读取 HF2 锁相放大器的 R 值。

### 扫描策略

```
X 方向: ±X_SCAN_RANGE, XY_SCAN_POINTS 个均匀步长
Y 方向: ±Y_SCAN_RANGE, XY_SCAN_POINTS 个均匀步长
总计: XY_SCAN_POINTS × XY_SCAN_POINTS 个点
```

### 温度管理

每行 Y 扫描前关闭温度开关（消除温控磁场对锁相读数的干扰），扫描完成后立即恢复，并监测温度变化。

```
for each Vx:
    关闭温度开关
    for each Vy:
        设置 Vy
        等待 XY_SETTLE_TIME
        读取 R
    恢复温度开关
    监测温度
```

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| Pump 调制 | DG4000 (DG4E222800868) | CH1 | 100MHz 正弦波 → RF 开关 IN |
| RF 开关门控 | DG4000 (DG4E222800868) | CH2 | 脉冲方波 → RF 开关 CTRL |
| RF 开关输出 | RF 开关 | OUT | → AOM（串 0.1μF 隔直电容） |
| X 补偿 | DG4000 (DG4E234902522) | CH1 | X 方向补偿电压输出 |
| Y 补偿 | DG4000 (DG4E234902522) | CH2 | Y 方向补偿电压输出 |

## 实验流程

1. **连接所有设备**：GS200（主磁场）、DG4000×3（Pump 调制、X/Y 补偿、光功率）、TEC103（温度）、HF2（锁相检测）
2. **设置初始条件**：
   - Pump/Probe 光功率（DC 电平）
   - 主磁场（GS200 恒流）
   - 目标温度（TEC103，等待稳定）
   - 温度开关 ON
   - X/Y 补偿输出 ON（初始 0V）
3. **配置 RF 开关方案**：CH1 100MHz 正弦 + CH2 脉冲门控
4. **HF2 相位校准**：关闭温度开关 → 配置解调器 → 自动相位校准 → 恢复温控
5. **二维扫描（数据采集）**：逐行扫描 X/Y 补偿电压，记录 R 值 → 保存原始数据到 `raw/scan_data.npz`
6. **数据分析 & 绘图**（可离线执行）：从文件或内存加载数据 → 找全局最优 → 边界检测 → 绘图 → 保存 `optimal_xy.yaml`
7. **（可选）精细扫描**：在最优值周围以更小范围重扫，提高精度
8. **断开设备**：保持输出状态不变

## 输出数据

### 运行目录结构

```
data/XY_Compensation_Calibration/
  MMDD_HHMM_xycalib/
    experiment_config.yaml    # 实验完整配置（可复现用）
    raw/
      scan_data.npz           # 粗扫原始数据（R_map, X/Y_values 等）
      refine_data.npz         # 精细扫描原始数据（仅运行精扫后有）
    results/
      xy_calibration_2d.png   # 粗扫 2D 校准图
      xy_calibration_refine.png # 精细扫描 2D 校准图（仅运行精扫后有）
      optimal_xy.yaml         # 最优补偿值
```

### `optimal_xy.yaml` 字段

| 字段 | 单位 | 说明 |
|------|------|------|
| `main_magnetic_field_mA` | mA | 当前主磁场（校准时的固定值） |
| `X_optimal_V` | V | X 方向最优补偿电压 |
| `Y_optimal_V` | V | Y 方向最优补偿电压 |
| `R_max_V` | V | 最优点的 R 信号值 |
| `X_scan_range_V` | V | X 方向扫描范围（±） |
| `Y_scan_range_V` | V | Y 方向扫描范围（±） |
| `scan_points` | - | 每方向扫描点数 |
| `initial_temperature_C` | °C | 扫描开始时的温度 |
| `final_temperature_C` | °C | 扫描结束时的温度 |

## 采集与分析分离

本实验遵循**数据采集与数据分析分离**的设计模式：

| 步骤 | Cell | 功能 | 需设备连接？ |
|------|------|------|:---:|
| 数据采集 | Cell 8 | 执行二维扫描，保存 `raw/scan_data.npz` | ✅ |
| 数据分析 & 绘图 | Cell 9 | 从文件加载数据，绘图，保存结果 | ❌ |
| 精细扫描（可选） | Cell 10 | 在最优值周围重扫提高精度 | ✅ |

**好处**：
- 采集完成后可关设备、重启 kernel，仍可运行分析
- 数据分析 Cell 自包含，优先加载 `.npz` 文件，内存变量为后备
- 便于重复调整绘图样式而不必重新采集

## 精细扫描 (Refine)

粗扫完成后，可以选择在最优值周围做一次精细扫描提高精度：

- 扫描范围 = 粗扫范围 × `REFINE_FACTOR`（默认 30%）
- 扫描点数 = `REFINE_POINTS`（默认 15）
- 结果自动覆盖粗扫的最优值，参与后续的断开操作

> **注意**：如果粗扫的最优值在边界上，精细扫描的结果可能不可靠，应先增大范围重新粗扫。

## 原始数据

每次扫描的原始数据保存在 `raw/` 目录下：
- `scan_data.npz`：包含 `R_map`, `X_values`, `Y_values`, `ij_max`, `X_opt`, `Y_opt`, `R_max` 等
- `refine_data.npz`：精细扫描的数据（仅运行精扫后有）

可用以下代码加载复查：

```python
data = np.load("raw/scan_data.npz")
R_map = data["R_map"]
X_values = data["X_values"]
Y_values = data["Y_values"]
```

## 结果使用

将校准得到的最优值填入灵敏度测量实验的配置中：

```python
FIXED_PARAMS = {
    ...
    "X_magnetic_field": X_optimal_V,   # ← 用校准结果替换
    "Y_magnetic_field": Y_optimal_V,   # ← 用校准结果替换
    ...
}
```

> **注意**：校准结果是针对当前主磁场大小的横磁场补偿最优值。如果改变主磁场大小，需要重新校准 X/Y 补偿。
