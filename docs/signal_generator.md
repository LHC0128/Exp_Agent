# DG4000 信号发生器控制模块 (`signal_generator`)

控制 DG4000 系列函数/任意波形发生器，基于 PyVISA + SCPI 协议。

## 主要功能

| 功能 | SCPI 命令 | 说明 |
|---|---|---|
| 通道绑定 | — | 每个实例绑定固定通道，无需重复传参 |
| 波形输出 | `:APPLy:<shape>` | SINusoid / SQUare / RAMP / PULSe / NOISe / DC |
| 自定义波形 | `:TRACe:DATA:DATA` | 任意周期函数，归一化 [-1, +1] |
| 频率/周期 | `:FREQuency` / `:PERiod` | 1 µHz ~ 160 MHz (依波形类型) |
| 幅度/偏置 | `:VOLTage:AMPLitude/OFFSet` | Vpp / VRMS / DBM 单位可选 |
| 相位控制 | `:PHASe:ADJust` | 0° ~ 360°, 分辨率 0.01° |
| 方波占空比 | `:FUNCtion:SQUare:DCYCle` | 20% ~ 80% |
| 斜波对称度 | `:FUNCtion:RAMP:SYMMetry` | 0% ~ 100% |
| 脉冲参数 | `:PULSe:*` | 脉宽、占空比、延迟、边沿时间 |
| 调制 | `:MOD` | AM / FM / PM / FSK / PWM，内/外调制源可选 |
| 扫描 | `:SWEep` | 线性/对数扫描，起止频率可设 |
| 脉冲串 | `:BURSt` | 触发/门控模式，可设周期数 |
| 谐波输出 | `:HARMonic` | 偶/奇/全谐波，阶次和幅度可设 |
| 参考时钟 | `:ROSCillator:SOURce` | 内部 10 MHz / 外部同步 |
| 输出控制 | `:OUTPut` | 开/关、负载阻抗、极性、同步信号 |
| 状态管理 | `*SAV` / `*RCL` | 保存/恢复仪器状态 |

## 通道绑定模式

每个实例可绑定到固定通道，后续调用无需传 `channel=`：

```python
from signal_generator import DG4000Instrument

ch1 = DG4000Instrument("USB0::...", channel=1)
ch2 = DG4000Instrument("USB0::...", channel=2)

with ch1, ch2:
    ch1.setup_sine(1000, 2.0)    # 自动用 CH1
    ch2.setup_square(100, 3.0)   # 自动用 CH2
```

## 便捷方法

```python
dg.setup_sine(1000, 2.0)          # 正弦波: 1 kHz, 2 Vpp
dg.setup_square(100, 3.0, dcycle=30, phase=45)  # 方波: 占空比 30%, 相位 45°
dg.setup_ramp(10, 4.0, symmetry=30)
dg.setup_pulse(1000, 3.3, width=10e-6)
dg.setup_dc(1.5)                  # DC 输出
dg.setup_noise(1.0, offset=0.0)
```

## 自定义任意波

```python
import numpy as np

t = np.linspace(0, 2*np.pi, 1000, endpoint=False)
y = 0.7 * np.sin(t) + 0.3 * np.sin(3*t)

dg = DG4000Instrument("USB0::...", channel=1)
dg.setup_arbitrary(y, freq=50, amplitude=5.0)
```

## 调制示例

```python
dg.set_mod_type("AM")
dg.set_mod_am_depth(80)
dg.set_mod_am_internal_freq(100)
dg.set_mod_source("INTernal")     # 或 "EXTernal"
dg.set_mod_state(True)
```

## 多仪器配置管理

```python
from signal_generator import SignalGeneratorConfig, MultiGeneratorSetup

# 单台配置
cfg = SignalGeneratorConfig(resource="USB0::...", shape="SINusoid",
                            frequency=1000, amplitude=5.0)
cfg.to_yaml("params/sg1.yaml")

# 多台配置 (YAML 内用 resource 区分)
setup = MultiGeneratorSetup.from_yaml("params/default_sg_config.yaml")
setup.apply_all({inst.resource: inst for inst in [sg1, sg2]})
```

## 模块结构

```
src/signal_generator/
  __init__.py        # 包导出
  instrument.py      # PyVISA 封装 + SCPI 命令 (110+ 方法)
  config.py          # 配置与多仪器管理 (SignalGeneratorConfig, MultiGeneratorSetup)
```

详细说明见 [examples/signal_generator_demo.ipynb](../examples/signal_generator_demo.ipynb)。
