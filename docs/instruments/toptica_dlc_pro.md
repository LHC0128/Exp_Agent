# TOPTICA DLC pro Probe 激光

## 设备与连接

| 项目 | 配置 |
|---|---|
| 控制器 | `DLC PRO_53043` |
| 地址 | `192.168.124.68` |
| SDK 端口 | Command `1998` / Monitoring `1999` |
| 激光通道 | Laser 1 |
| 激光头 | DLpro S/N `24105` |
| Python SDK | `toptica-lasersdk==3.3.0` |

连接配置位于 `params/mapping.yaml` 的 `probe_laser`。该映射不会替换
`Probe_laser_power`；后者继续由 DG900 CH2 控制外部 Probe 光功率。

驱动位于 `src/toptica_laser/`，使用官方 SDK 的低层
`Client` 和 `NetworkConnection`。驱动不提供通用 raw setter，只提供
`get_parameter()` 只读诊断接口。

## 控制量与参数路径

| 控制量 | DLC pro 参数 | 安全范围 |
|---|---|---|
| 激光电流设定值 | `laser1:dl:cc:current-set` | 200～390 mA，且不超过实时 `current-clip` |
| 激光温度设定值 | `laser1:dl:tc:temp-set` | 19～21 °C |
| PZT 电压 | `laser1:scan:offset` | 0～140 V |
| 扫描幅度 | `laser1:scan:amplitude` | 0～50 Vpp |
| 扫描启停 | `laser1:scan:enabled` | 布尔值 |
| 扫描频率 | `laser1:scan:frequency` | 只读，保持设备当前值 |

这里的“PZT 电压”明确指 Scan Offset。驱动另行读取
`laser1:dl:pc:voltage-act` 作为 PZT 实际电压，但不暴露
`laser1:dl:pc:voltage-set` 写入。

每次写入前同时校验扫描包络：

```text
0 V <= PZT voltage - scan amplitude / 2
PZT voltage + scan amplitude / 2 <= 140 V
```

全部目标值会在第一条设备写命令之前完成校验。联合修改 PZT 与扫描幅度时，
共享控制服务会选择中间状态仍满足包络限制的写入顺序。设备写入或回读异常时，
系统不会自动关闭 Emission，也不会自动回滚，而是报告“设备状态可能未知”，
要求操作员检查 DLC pro 面板和 TOPAS。

## Python 使用

```python
from toptica_laser import DLCProInstrument

with DLCProInstrument("192.168.124.68") as laser:
    print(laser.get_controller_serial())
    print(laser.get_laser_current_actual_ma())
    print(laser.get_laser_temperature_actual_c())
    print(laser.get_pzt_voltage_v())
    print(laser.get_scan_frequency_hz())

    # 写入前会执行驱动层安全校验并强制回读。
    laser.set_laser_current_ma(202.0)
    laser.set_laser_temperature_c(20.3)
    laser.set_pzt_voltage_v(80.0)
    laser.set_scan_amplitude_vpp(20.0)
    laser.set_scan_enabled(False)
```

正式实验和 GUI 应优先通过 `lab_workflows.instrument_control` 使用共享安全限值，
而不是直接创建驱动实例。

## GUI 与 API

设备类型为 `DLC_PRO`。仪器控制页显示控制器、激光头、固件、健康、联锁、
Emission、Laser Enabled、电流、温度、PZT 和扫描状态。

- `PUT /api/devices/{id}/laser`：接收 `current_set_ma`、
  `temperature_set_c`、`pzt_voltage_v`、`scan_amplitude_vpp`、
  `scan_enabled`。
- `PUT /api/devices/{id}/emission`：独立的 Emission 高风险接口。

`remote_emission_control_enabled` 默认为 `false`。在确认 SI 模块、激光等级、
联锁和警示回路之前不得修改。Laser Enabled 在 GUI 中始终只读。

远程 ON 同时要求：

1. 配置门禁已明确开放。
2. 系统和激光头健康码为 0，联锁闭合，Laser Enabled 为 ON。
3. 全部关键参数回读均处于安全范围。
4. 操作员勾选现场安全确认。
5. 输入 `DLC PRO_53043`，并通过浏览器的再次确认。

远程 OFF 不要求确认，但必须回读验证。

## 硬件验收

2026-07-27 已完成第一阶段只读核对，未发送任何设备写命令。一次只读快照为：

- 控制器 `DLC PRO_53043`，系统 `DLCpro`，固件 `3.5.1.3a02efe74`。
- Laser 1 为 `DLpro (S/N 24105)`，系统和激光头健康状态均为 `OK`，
  联锁闭合，Laser Enabled 为 ON。
- 电流设定/实际约为 `201.081/201.325 mA`，`current-clip` 为 `392 mA`。
- 温度设定/实际约为 `20.3/20.300 °C`。
- PZT Scan Offset 约 `80.296 V`，实际电压约 `79.950 V`。
- 扫描为 OFF，幅度 `34 Vpp`，频率约 `11 Hz`。
- 当时 Emission 为 ON；因此本次实现和验证没有执行同值写入或任何远程关光操作。

后续现场验收必须在操作员监护下按顺序进行：

1. Emission OFF 时执行同值写入并回读。
2. 小范围分别调整电流、温度和 PZT。
3. 验证扫描包络及扫描启停。
4. 确认 SI 模块和联锁后，先验证远程 OFF。
5. 最后才可将 `remote_emission_control_enabled` 改为 `true`，验证受保护的 ON。
