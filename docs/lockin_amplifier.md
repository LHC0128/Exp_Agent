# HF2 锁相放大器控制模块 (`lockin_amplifier`)

基于 LabOne API (`zhinst.core`) 控制 Zurich Instruments HF2 锁相放大器。

## 主要功能

| 功能 | LabOne 节点 | 说明 |
|---|---|---|
| 连接管理 | ziDAQServer | 连接 LabOne 数据服务器 |
| 参考频率 | /OSCS/N/FREQ | 设置解调参考频率 |
| 数据速率 | /DEMODS/N/RATE | 解调输出数据速率 (Sa/s) |
| 带宽控制 | /DEMODS/N/TIMECONSTANT + ORDER | 低通滤波器时间常数 + 阶数 |
| 输入量程 | /SIGINS/N/RANGE | +/- V |
| 耦合方式 | /SIGINS/N/AC | AC/DC 耦合 |
| 相位校准 | /DEMODS/N/PHASESHIFT | 手动或自动校准使 Y ≈ 0 |
| DAQ 采集 | Data Acquisition Module | 连续/触发模式，可设采样率/时长/点数 |
| 辅助输出 | /AUXOUTS/N | 将解调信号路由到物理接口 |

## 硬件模块

HF2 锁相放大器包含多个独立硬件模块：

| 模块 | 数量 | 对应节点 | 配置类 |
|---|---|---|---|
| 信号输入 | 2 | sigins/0~1 | `SignalInputConfig` |
| 振荡器 | 2 | oscs/0~1 | `OscillatorConfig` |
| 解调器 | 6 | demods/0~5 | `DemodulatorConfig` |
| 信号输出 | 2 | sigouts/0~1 | `SignalOutputConfig` |
| 辅助输出 | — | auxouts/0~1 | `AuxOutConfig` |

## 安装

```bash
pip install zhinst
```

`zhinst` 包提供 LabOne API 的 Python 绑定，通过 socket 与 LabOne 数据服务器通信。

## Python API 使用

```python
from lockin_amplifier import (
    HF2Instrument,
    SignalInputConfig, OscillatorConfig,
    DemodulatorConfig, SignalOutputConfig,
    demod, daq, auxout,
)

# 连接设备
instr = HF2Instrument("127.0.0.1", 8005, 1, "dev18246", interface="PCIe")
instr.connect()

# 1. 信号输入配置
demod.configure_signal_input(instr, SignalInputConfig(input_index=0, range=1.0))

# 2. 振荡器配置
demod.configure_oscillator(instr, OscillatorConfig(osc_index=0, frequency=100e3))

# 3. 解调器配置
cfg = DemodulatorConfig(demod_index=0, enable=True, rate=10e3,
                         input_channel=0, osc_select=0,
                         harmonic=1, time_constant=0.001, order=4)
actual_rate = demod.configure_demodulator(instr, cfg)

# 4. 信号输出配置
demod.configure_signal_output(instr, SignalOutputConfig(output_index=0, range=1.0))

# 5. 相位校准
demod.auto_calibrate_phase(instr, demod_idx=0)

# 6. DAQ 采集
results = daq.acquire_data(instr, DAQConfig(duration=0.1),
                            demod_idx=0, actual_rate=actual_rate)

# 7. 辅助输出
auxout.configure_aux_output(instr, AuxOutConfig(output_select=2))

# 断开
instr.disconnect()
```

## 单个参数设置

解调器各参数可独立调整，无需重新配置全部：

```python
demod.set_demod_phase(instr, demod_idx=0, phase_deg=45.0)
demod.set_reference_frequency(instr, osc_idx=0, freq=200e3)
demod.set_demod_time_constant(instr, demod_idx=0, tc=0.01)
demod.set_demod_rate(instr, demod_idx=0, rate=1e3)
demod.set_input_range(instr, 0.5, input_idx=0)
demod.set_output_enable(instr, output_idx=0, enable=True)
```

## 配置示例

```yaml
signal_input:
  input_index: 0
  range: 1.0
  ac_coupling: true
  impedance: 50

oscillator:
  osc_index: 0
  frequency: 100000.0
  source: manual

demodulator:
  demod_index: 0
  enable: true
  rate: 10000.0
  input_channel: 0
  osc_select: 0
  harmonic: 1
  time_constant: 0.001
  order: 4

signal_output:
  output_index: 0
  range: 1.0
  offset: 0.0
  enable: true

daq:
  duration: 0.1
  grid_cols: 1000
  signal_paths: [sample.r, sample.x, sample.y, sample.theta]
```

## 注意事项

- HF2 仅支持 **API Level 1**，数据服务器端口为 **8005**
- LabOne 数据服务器 (ziServer.exe) 需先在 PC 上运行
- 无硬件时 `zhinst` 包仍可导入，但连接会失败
- 部分功能可以在 LabOne UI 中操作并生成 API 日志，辅助编程
- 解调器 `rate` 仅支持特定离散值，设置后会从设备读回实际采样率

## 模块结构

```
src/lockin_amplifier/
  __init__.py        # 包导出
  instrument.py      # HF2Instrument 连接封装
  config.py          # 配置数据类 (各模块 Config + DAQResult)
  demod.py           # 信号输入/振荡器/解调器/信号输出 配置
  daq.py             # DAQ 模块数据采集
  auxout.py          # 辅助输出配置
```

详细说明见 [examples/lockin_amplifier_demo.ipynb](../examples/lockin_amplifier_demo.ipynb)。
