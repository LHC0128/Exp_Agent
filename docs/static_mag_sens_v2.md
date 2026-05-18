---
title: 静磁场灵敏度测量
type: experiment_type
description: 测量静磁场的色散线形，根据色散斜率和功率谱密度计算磁场灵敏度
keywords: [dispersion, sensitivity, PSD, RAMP sweep, lock-in]
version: 1

# ========== 扫描方式 ==========
scan_mode: continuous_ramp       # point_by_point | continuous_ramp
trigger_source: DG4000_SYNC      # SYNC 信号触发 HF2 DAQ

# ========== 默认参数 (skill 生成代码时填入) ==========
defaults:
  RAMP_LOW: -0.5
  RAMP_HIGH: 0.5
  RAMP_FREQ: 1.0
  RAMP_SYMMETRY: 20
  DAQ_DURATION: 1.0
  HF2_DEMOD_RATE: 1000
  HF2_DEMOD_TC: 0.001
  HF2_NOISE_RATE: 50000
  HF2_NOISE_TC: 1e-6
  NOISE_N_AVG: 10
  NOISE_DURATION: 1.0
  fall_crop: 0.1                # 裁剪转折区比例 (首尾各 crop)

# ========== 所需设备 ==========
required_devices:
  - instrument: signal_generator    # Z 磁场扫场
    role: sweep
    channels: [1, 2]
  - instrument: signal_generator    # Pump/Probe 光功率
    role: laser
    channels: [1, 2]
  - instrument: signal_generator    # X/Y 补偿磁场
    role: compensation
    channels: [1, 2]
  - instrument: signal_generator    # Pump 调制
    role: modulation
    channels: [1, 2]
  - instrument: signal_generator    # 温度开关
    role: temp_switch
    channels: [2]
  - instrument: gs200               # 主磁场
    role: main_field
  - instrument: tec_controller      # 温度控制
    role: temperature
  - instrument: lockin_amplifier    # 锁相放大器
    role: detection
    demod_channels: 1
    has_daq: true

# ========== mapping.yaml 中的 key 与角色对应 ==========
# skill 据此从 params/mapping.yaml 中查找各物理量的配置
mapping_keys:
  Z_magnetic_field:               # 扫场变量
    role: sweep
    description: "Z 方向磁场，RAMP 扫场"
  Pump_laser_power:               # 固定参数
    role: laser
    description: "Pump 光功率 (DC 电平)"
  Probe_laser_power:              # 固定参数
    role: laser
    description: "Probe 光功率 (DC 电平)"
  X_magnetic_field:               # 固定参数，输出关闭
    role: compensation
    description: "X 方向补偿磁场"
  Y_magnetic_field:               # 固定参数，输出关闭
    role: compensation
    description: "Y 方向补偿磁场"
  Pump_modulation:                # 固定参数
    role: modulation
    description: "Pump 调制任意波 + 时序方波"
  Temp_Switch:                    # 固定参数（实验过程中会通断）
    role: temp_switch
    description: "温度开关继电器 (5V=ON, 0V=OFF)"
  main_magnetic_field:            # 固定参数
    role: main_field
    description: "主磁场 (GS200 恒流)"
  temperature:                    # 固定参数
    role: temperature
    description: "气室温度 (TEC103)"
  lockin_r:                       # 锁相检测
    role: detection
    description: "HF2 锁相放大器，读取解调信号"

# ========== 固定参数（哪些 mapping_key 是固定值） ==========
# 这些物理量在实验中设一次初值后不再改变
fixed_params:
  - Pump_laser_power
  - Probe_laser_power
  - X_magnetic_field
  - Y_magnetic_field
  - Pump_modulation
  - Temp_Switch
  - main_magnetic_field
  - temperature
    has_daq: true
---

# 静磁场灵敏度测量

## 实验流程

### Phase 1: 准备
1. 连接所有设备，设置固定参数
2. 生成 Pump 调制任意波（100MHz/60MHz 交替，AOM 控制）

### Phase 2: 相位校准
1. **关闭 Z 磁场输出** (DC 0V + 输出 OFF)
2. **关闭温度开关**，消除温控磁场干扰
3. 配置 HF2 信号输入、振荡器、解调器 (rate=1000 Sa/s, TC=1ms)
4. 自动相位校准 → 得到 `calibrated_phase`
5. 恢复温度开关
6. 等待系统稳定 (1s)

### Phase 3: 色散采集 (连续扫场 + 触发同步)
1. 启动 DG4000 RAMP 扫场
2. 开启 SYNC 输出
3. 关闭温度开关
4. HF2 DAQ 触发采集（等待 SYNC 上升沿，采集 1 个周期）
5. 停止扫场，恢复温度开关

### Phase 4: 数据分析 (纯离线)
1. 计算 t_norm、上升/下降段掩码、有效数据窗口
2. 取下降段，绘制色散曲线 (X, Y, R vs B)
3. 对 Y 做色散拟合，得 dY/dB

### Phase 5: 噪声采集 (多次平均)
1. 找零交叉点 V0（上升段 Y 过零点）
2. 将 HF2 改为高速率 (50000 Sa/s, 10μs TC)
3. 关闭 Z 磁场、关闭温度开关
4. 采集 NOISE_N_AVG 次 Y 噪声，逐次保存到硬盘
5. 每次采集完立即恢复温度开关，等系统稳定
6. 读出各次数据，Welch 法求 PSD 后等权平均
7. **恢复解调器至采集配置** (rate=1000, TC=1ms)

### Phase 6: 灵敏度计算
1. δB(f) = sqrt(PSD_avg) / |dY/dB|
2. 取色散线宽内低频段中位数作为灵敏度

## 数据解析规则 (重要)

### RAMP 波形与掩码

```
对称性 S = RAMP_SYMMETRY / 100
  0 ~ S 周期:    上升段 (单调递增，V=RAMP_LOW → RAMP_HIGH)
  S ~ 1 周期:    下降段 (单调递减，V=RAMP_HIGH → RAMP_LOW)
```

**关键规则**：绘图和拟合**必须只取一个单调段**（下降段），否则数据折叠。

```python
t_cycle = t_norm % 1.0
# 裁剪转折区 (首尾各 crop):
fall_start = s + (1.0 - s) * crop     # 默认 crop=0.1
fall_end   = s + (1.0 - s) * (1 - crop)
fall_mask  = (t_cycle >= fall_start) & (t_cycle < fall_end)
```

### 注意事项 (由修复经验总结)
- 噪声测量后**必须恢复**解调器配置，否则第二次运行异常
- `grid_cols` 必须使用 `actual_rate` 而非硬编码常量 `HF2_DEMOD_RATE`
- Cleanup 时使用 `all_off()` 关双通道，显式关 SYNC
- 温度开关在 cleanup 中保持开启
- 每次噪声采集完立即恢复温度开关，防止温度漂移
