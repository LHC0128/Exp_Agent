# DG4000 / DG900 Pro 信号发生器控制模块 (`signal_generator`)

控制 DG4000 与 DG900 Pro 系列函数/任意波形发生器，基于 PyVISA + SCPI 协议。
两系列的远程命令层级不同，分别由 `DG4000Instrument` 和
`DG900Instrument` 实现，不应跨系列复用 SCPI 命令。

## 主要功能

| 功能 | SCPI 命令 | 说明 |
|---|---|---|
| 通道绑定 | — | 每个实例绑定固定通道，无需重复传参 |
| 波形输出 | `:APPLy:<shape>` | SINusoid / SQUare / RAMP / PULSe / NOISe / DC |
| 自定义波形 | DG4000 `:TRACe:DATA:DATA`；DG900 `:TRACe:DATA:DAC16` | 任意周期函数，归一化 [-1, +1] |
| 频率/周期 | `:FREQuency` / `:PERiod` | 1 µHz ~ 160 MHz (依波形类型) |
| 幅度/偏置 | `:VOLTage:AMPLitude/OFFSet` | Vpp / VRMS / DBM 单位可选 |
| 相位控制 | DG4000 `:PHASe:ADJust`；DG900 `:PHASe` / `:PHASe:SYNChronize` | 相位设置、回读和同相位操作 |
| 方波占空比 | `:FUNCtion:SQUare:DCYCle` | 20% ~ 80% |
| 斜波对称度 | `:FUNCtion:RAMP:SYMMetry` | 0% ~ 100% |
| 脉冲参数 | `:PULSe:*` | 脉宽、占空比、延迟、边沿时间 |
| 调制 | DG4000 `:MOD:*`；DG900 `:AM/:FM/:PM/:FSKey/:PWM` | AM / FM / PM / FSK / PWM，内/外调制源可选 |
| 扫描 | `:SWEep` | 线性/对数扫描，起止频率可设 |
| 脉冲串 | `:BURSt` + 触发命令 | 触发/门控模式、周期数、相位、周期和延迟 |
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

DG900 Pro 将归一化值转换为有符号 16 位码值，通过原生
`:SOURce<n>:TRACe:DATA:DAC16 CODE,<flag>,...` 分块下载。点数必须为 32 至
16 Mpts，每个 ASCII 数据块不超过 20 kB；下载完成后驱动执行 `*OPC?` 并检查完整
错误队列。`setup_arbitrary()` 失败时会恢复调用前的输出开关状态。

Direct-AW 的无限 Burst 在 DG900 Pro 上不是独立模式。统一接口
`set_burst_mode("INFinity")` 会写入 `BURSt:MODE TRIGgered` 和
`BURSt:NCYCles INFinity`，外部触发后持续输出；工作流不得再覆盖固定循环数。

## 调制示例

```python
dg.set_mod_type("AM")
dg.set_mod_am_depth(80)
dg.set_mod_am_internal_freq(100)
dg.set_mod_source("INTernal")     # 或 "EXTernal"
dg.set_mod_state(True)
```

DG900 Pro 的调制类型没有通用的 `MOD:TYPE/STATE` 命令，而是分别使用
`:AM:STATe`、`:FM:STATe` 等命令。驱动提供统一命名的参数方法，并通过
`set_mod_type_state()` 启停指定类型：

```python
dg = DG900Instrument("USB0::...", channel=1)
dg.set_mod_source("AM", "EXTernal")
dg.set_mod_am_depth(80)
dg.set_mod_type_state("AM", True)
```

## 参数回读

- DG900 Pro 使用 `:SOURce<n>:APPLy?` 一次读取波形、频率、幅度、偏置和相位，
  `get_wave_parameters()` 会解析引号、科学计数法和 `DEF` 占位符。
- DG4000 使用现有的独立查询命令读取基础波形参数。
- 两系列均提供 Mod 和 Burst setter 对应的 getter，并将 `TRIG`、`EXT`、`POS`
  等仪器缩写展开为稳定名称。
- DG900 Pro 的 Burst 延迟、触发源和触发边沿使用独立的 `:TRIGger<n>:*`
  子系统；DG4000 继续使用 `:SOURce<n>:BURSt:TRIGger:*`。
- DG900 Pro 不提供基础脉冲波形延迟命令，因此仅回读脉宽和起始相位；
  `burst.delay` 仍由 `:TRIGger<n>:DELay?` 正常回读。
- DG900 Pro 使用 `:OUTPut<n>:SYNC` 控制同步输出，并以
  `:SOURce<n>:PHASe:SYNChronize` 执行同相位操作；这两个方法与 DG4000 采用相同
  Python 接口名 `set_sync_state()` 和 `phase_init()`。
- 两系列 GUI 都可读写幅度单位和输出负载；Pulse Delay 仅在 DG4000 上显示。

## GUI 行为

仪器控制页以多列网格展示全部物理量，打开时不连接硬件；可逐项读取，也可顺序读取
全部目标。每个信号源面板只读取对应 mapping key 的通道。常用波形字段直接显示，波形细节、单位、负载、调制和
Burst 默认折叠，写入成功后用设备实际回读值替换表单状态。

- 启用 Mod 时自动关闭 Burst；启用 Burst 时自动关闭 Mod。
- 基础输出电压写入前继续使用 `params/safety_limits.yaml` 校验。
- `rf_coil` 作为 `Y_magnetic_field` 的历史别名不单独显示，手动控制统一使用 Y 场面板。
- 非 Vpp 状态仅允许回读和关闭输出；其他写入前必须切换为 Vpp 并重新读取。
- 非核心查询失败时，其余字段仍返回，并在 `readback_errors` 中记录失败字段。
- 所有映射的信号源通道均显示实际回读值；`Heat_Control` 允许编辑，
  但写入仍受 `params/safety_limits.yaml` 的保守限值保护。

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
  instrument.py      # DG4000 PyVISA 封装 + SCPI 命令
  dg900.py           # DG900 Pro 官方 SCPI 封装
  protocol.py        # 两系列公共协议与能力描述
  config.py          # 配置与多仪器管理 (SignalGeneratorConfig, MultiGeneratorSetup)
```

详细说明见 [examples/signal_generator_demo.ipynb](../examples/signal_generator_demo.ipynb)。
