# 静磁场灵敏度测量

> 调用 `/expcodegen` 即可自动生成实验 Notebook。

---

## 1. 实验信息

- **实验类型**：`Static Magnetic Field Sensitivity`
- **实验目的**：`测量静磁场的色散线形，根据色散斜率和功率谱密度计算灵敏度`

---

## 2. 设备接线

### 时钟同步

| 设备 | 操作 |
|------|------|
| Z 场 DG4000 (`DG4E242401288`) | 外部时钟 |
| 补偿场 DG4000 (`DG4E234902522`) | 外部时钟 |
| HF2 锁相放大器 (`dev18246`) | `set_extclk(True)` |

### 触发与信号

| 源设备 | 源端口 | 目标设备 | 目标端口 | 说明 |
|--------|--------|----------|----------|------|
| Z 场 DG4000 (CH1) | [SYNC] 输出 | HF2 | 辅助输入 1 (auxins/0) | 每 ramp 周期起始的 TTL 脉冲，用于 DAQ 触发同步 |

---

## 3. 涉及物理量

> 物理量名称需与 `params/mapping.yaml` 中的 key **完全一致**。

### 扫描变量

| 物理量 (mapping key) | 起始值 | 终止值 | 点数 | 单位 | 说明 |
|----------------------|--------|--------|------|------|------|
| `Z_magnetic_field` | -1 | 1 | 无穷 | V | DG4000 CH1 设为 RAMP 模式连续扫场，点数由 DAQ 采样率决定 |

### 固定参数

| 物理量 (mapping key) | 设定值 | 单位 | 说明 |
|----------------------|--------|------|------|
| `Pump_laser_power` | 1.0 | V | DG4000 DC 电平控制 Pump 光功率 |
| `Probe_laser_power` | 1.0 | V | DG4000 DC 电平控制 Probe 光功率 |
| `temperature` | 100 | °C | TEC103 控制 Cell 温度 |
| `Temp_Switch` | 5 | V | **开启**: setup_dc(5V)；**关闭**: set_output(False)（禁用输出而非输出 0V） |
| `Pump_modulation` | 180 | mV | 90kHz 的任意波，详见第 5 节 |
| `Time_sequence` | 10 | V | 时序信号 10Hz 方波 |
| `main_magnetic_field` | 9.29 | mA | GS200 主磁场，保持不变 |

### 补偿/调节磁场（DC 0V 配置后关闭输出）

| 物理量 (mapping key) | 设定值 | 单位 | 说明 |
|----------------------|--------|------|------|
| `X_magnetic_field` | 0 | V | X 方向补偿，setup_dc(0V) 后 set_output(False) |
| `Y_magnetic_field` | 0 | V | Y 方向补偿，setup_dc(0V) 后 set_output(False) |
| `Time_sequence_2` | 0 | V | 预留时序，setup_dc(0V) 后 set_output(False) |

---

## 4. 初始化步骤

1. 连接各个设备
2. 将 DG4E242401288、DG4E234902522、HF2 三台设备时钟源改为外部
3. 设置各个实验参数，X/Y/预留时序配置为 DC 0V 后关闭输出
4. `Pump_modulation` 设置任意波（见第 5 节），绘制波形图供确认
5. 进行解调器 0 的相位校准（校准前关闭 Temp_Switch，校准后恢复）

---

## 5. Pump 调制任意波

- **重复频率**：90 kHz
- **波形结构**：每个周期内 100MHz 正弦波（相对幅值 1，占 5%）与 60MHz 正弦波（相对幅值 0.5，占 95%）切换，时间占比 1:19
- **点数**：15000 点（DG4000 支持的范围内）
- **触发模式**：设置完波形后，改为外部信号源无限触发模式，由外部时序驱动
- **物理意义**：100MHz 使 AOM 衍射角将 Pump 光耦合进原子气室（Pump ON），60MHz 使 Pump 光偏离（Pump OFF）

---

## 6. 数据采集

### 扫场方式

- Z 场 DG4000 CH1 设为 RAMP 模式，从 -1V 到 +1V 连续扫场
- RAMP 参数：
  - 幅度：2 Vpp（对应 -1V ~ +1V）
  - 对称性：0~100%，100% 为锯齿波（全程线性上升 + 快降），50% 为三角波
  - 频率：0.05 Hz 或根据需要调整（采集时长 = 1/频率）
- HF2 DAQ 在触发模式下同步采集一个完整的 ramp 周期

### 每次采集流程

1. **关闭 Temp_Switch**：`set_output(False, CH2)`，避免温控磁场干扰锁相读数
2. **启动 Z 场 RAMP**：`setup_ramp()` — 注意：APPLy 命令会重置该通道 SYNC 为 OFF
3. **开启 SYNC 输出**：必须在 `setup_ramp` 之后调用 `set_sync_state(True)`，否则 SYNC 会被 APPLy 关闭
4. **HF2 DAQ 触发采集**：等待辅助输入 1 上 SYNC 上升沿 → 触发 → 采集一个周期
5. **停止扫场**：关闭 CH1 输出和 SYNC
6. **恢复 Temp_Switch**：`set_output(True, CH2)`，等待温度稳定后再进行下一次采集

### 订阅信号

- 解调器 0：`sample.r`, `sample.x`, `sample.y`
- 辅助输入 1：`sample.AuxIn0`（记录 SYNC 信号，用于后续验证时序）  
  → 非解调器信号需通过 `subscribe_raw()` / `extra_paths` 订阅完整节点路径

### 数据保存

```
data/Static_Magnetic_Field_Sensitivity/<timestamp>_<purpose>/
  raw/scan_data.npz     # X, Y, R, Z_voltage, AuxIn0
  raw/scan_log.csv      # 同上，CSV 格式
  results/dispersion_curve.png  # X/Y 分量+R 色散曲线
```

### 数据处理

- Z 场电压从 DAQ 时间轴按 ramp 对称性映射：
  - 对称性 100%（锯齿波）：V(t) = -1 + 2t/T
  - 对称性 50%（三角波）：先升后降各占 T/2
  - 对称性 0%（反锯齿）：V(t) = 1 - 2t/T
- SYNC 信号（AuxIn0）叠加在 R 图上用双 y 轴显示，用于验证采集时序

---

## 7. 注意事项

- APPLy 命令（`setup_ramp` / `setup_dc` / `setup_sine` 等）会**重置**该通道的 SYNC 状态为 OFF，
  需在 APPLy 完成后重新调用 `set_sync_state(True)`
- 数据采集前关闭 Temp_Switch 用 `set_output(False)` 而非 `setup_dc(0V)`，
  前者禁用输出（高阻），后者仍输出 0V 可能存在残留干扰
- 锁相放大器相位校准在 Pump 光打开、Z 场为 0V DC、Temp_Switch 关闭的静态磁场条件下进行
- X/Y 补偿磁场和预留时序初始化时配置 DC 0V 后关闭输出，需要时再 `set_output(True)`
- 5 台 DG4000 中仅 Z 场和补偿场的需要外部时钟，其余保持内部时钟
