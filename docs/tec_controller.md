# TEC103 温控器控制模块

## 概述

`tec_controller` 是光测未来 TEC103 温度控制器的 Python 控制模块，通过串口（RS485/TTL）使用 ASCII 指令与设备通信。

## 依赖

- `pyserial`：串口通信

安装：`pip install pyserial`

## 快速开始

```python
from tec_controller import TECInstrument

# 连接温控器
tec = TECInstrument(port="COM3")
tec.connect()

# 设置目标温度 25°C
tec.set_target_temperature(25.0)

# 查询当前温度
temp = tec.get_temperature()
print(f"{temp:.3f} °C")

# 使能输出
tec.set_enable(True)

# 关闭输出
tec.set_enable(False)

# 断开连接
tec.disconnect()
```

也支持上下文管理器用法：

```python
with TECInstrument("COM3") as tec:
    tec.set_target_temperature(30.0)
    print(tec.get_temperature())
```

## API 参考

### 连接管理

| 方法 | 说明 |
|------|------|
| `connect()` | 打开串口连接 |
| `disconnect()` | 关闭串口连接 |
| `connected` (属性) | 返回是否已连接 |

### 目标温度

| 方法 | 说明 |
|------|------|
| `set_target_temperature(temp, channel=1)` | 设置目标温度 (°C) |
| `get_target_temperature(channel=1)` | 查询目标温度 (°C) |

温度内部缩放系数为 100000，例如 `2500000` 代表 25.00000 °C。

### 实际温度

| 方法 | 说明 |
|------|------|
| `get_temperature(channel=1)` | 查询当前实际温度 (°C) |
| `get_resistance(channel=1)` | 查询传感器电阻值 (kΩ) |

未接传感器时返回 `NaN`。

### 输出控制

| 方法 | 说明 |
|------|------|
| `set_enable(enable, channel=1)` | 设置输出使能 (True/False) |
| `get_enable(channel=1)` | 查询输出使能状态 |
| `set_output_mode(mode, channel=1)` | 设置输出模式 (0~3) |
| `set_max_duty(percent, channel=1)` | 设置最大输出占空比 (0~90%) |
| `set_slope(slope, channel=1)` | 设置温度变化斜率 (°C/s) |

### PID 参数

| 方法 | 说明 |
|------|------|
| `set_pid(kp, ki, kd, channel=1)` | 设置 PID 参数 |
| `set_kp(value, channel=1)` | 设置比例系数 |
| `set_ki(value, channel=1)` | 设置积分系数 |
| `set_kd(value, channel=1)` | 设置微分系数 |
| `get_kp(channel=1)` | 查询比例系数 |
| `get_ki(channel=1)` | 查询积分系数 |
| `get_kd(channel=1)` | 查询微分系数 |

### 批量查询

| 方法 | 说明 |
|------|------|
| `query_key_data(mode=1)` | 一次性查询关键数据 (原始字符串) |
| `get_all_temperatures()` | 解析返回各通道温度及温控器自身温度 |

### 设备信息

| 方法 | 说明 |
|------|------|
| `get_model()` | 查询温控器型号代码 |
| `get_firmware_version()` | 固件版本号 (如 "1.0.0") |
| `get_error_code()` | 查询错误代码 |

## 串口参数

| 参数 | 值 |
|------|-----|
| 波特率 | 38400 (默认) |
| 数据位 | 8 |
| 停止位 | 1 |
| 校验位 | 无 |

## 命令格式

所有 ASCII 指令以 `@` 结尾，响应以 `@\r\n` 结尾：

- 查询：`TC1:TG=?@` → `OKTC1:TG=2500000@\r\n`
- 设置：`TC1:TG=2500000@` → `OKTC1:TG=2500000@\r\n`
- 通道一前缀 `TC1`，通道二前缀 `TC2`
