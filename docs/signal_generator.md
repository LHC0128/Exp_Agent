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

## GUI Z 任意波控制

“功能模块”页面提供独立的 **Z 任意波控制**，仪器控制中的 Z 面板提供快捷入口。
来源为 `data/Z_AW_Closed_Loop_Waveform_Correction/<运行名>/results/corrected_control_waveform.npz`；
不自动选择最新文件，不加载理论版本或 CSV。选择结果后即可离线预览一个周期的
`time_s`–`voltage_v` 曲线，支持悬停读数、缩放和恢复全图，并显示实际电压范围。
这张图是文件电压预览，不是示波器实测；不按 Burst 相位移动原始时间轴。

文件中的 `voltage_v` 是闭环建议的实际输出电压。例如 `obbv5/6/7` 的文件幅度均为
6 Vpp。GUI 默认使用文件 Vpp，但允许修改“输出幅度 (Vpp)”：应用时保持 `voltage_v`
不变，重新计算 `normalized=(voltage_v-offset)/(Vpp/2)` 后上传；如果新 Vpp 太小导致
归一化值超过 [-1,1]，应用会被拒绝并提示所需的最小 Vpp。界面还允许调整 Z 的 Burst
起始相位，默认 0°；Z 固定使用外部下降沿、无限 Burst。
仪器 Z 面板读到任意波时只展示参数并允许关闭 Z；完整配置在功能模块中完成。

“联动时序信号2”默认关闭。启用后可编辑触发方波频率、Vpp、偏置和占空比，默认
100 Hz、5 Vpp、2.5 V、50%，负载为 High-Z。占空比采用信号发生器共同支持的
20%–80% 范围。触发方波频率独立于冻结波形的重复频率。
时序信号2必须与 Z 位于同机不同通道，并接入 Z 信号发生器 Ext Trig；若分配给
其他设备，应使用同一下降沿。软件无法确认实体接线及实际触发是否发生。

- **仅加载，保持关闭**：先关闭涉及的输出，再配置触发、上传任意波及设置 Burst，完成后保持关闭。
- **加载并启动**：配置完成后先开启 Z，再开启选定的共同触发；未联动时等待已有外部触发。
- **停止**：按最近已应用的联动状态关闭 Z 及本次联动的时序信号2，不依赖当前预览文件或未提交表单。
- 配置失败或执行取消时关闭本次涉及的输出；预检失败不写硬件。成功操作只释放连接，离开页面或停止 GUI 服务不会自动关闭输出。

操作复用任务管理器的硬件互斥、进度和取消。后端在执行前重新校验文件 SHA-256、
映射修订、点数能力和完整输出包络；非 Vpp 通道应先在仪器控制中切换单位并回读。
最近应用记录与预览相互独立，展示来源、联动设置及操作时的两路回读结果；后续
仪器操作可能改变输出。应用记录只保存在当前后端会话，重启或映射变化后来源未知，
重新加载可建立记录；来源未知时仍能关闭当前映射的 Z 输出。

共享实现位于 `lab_workflows/z_arbitrary_control.py`。Mx Z 原有触发和任意波配置函数
通过兼容入口复用此实现，保持实验原有准备顺序和结束策略。GUI 不设置 GS200、
激光、温控、HF2，也不执行灵敏度采集。

接口位于 `/api/tools/z-arbitrary-control`：`GET /sources`、
`GET /preview/{run_name}`、`GET /state` 和 `POST /actions`。
动作包括 `configure`、`configure_and_start`、`stop`；应用携带来源及预览 SHA-256，
所有动作携带设备库与映射修订号。停止忽略未提交的波形和触发表单参数。

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

## DG4162 DC 电平设置注意事项

DG4000 编程手册将 `VOLTage:LEVel:IMMediate:OFFSet` 定义为偏置电压的
专用设置命令。2026-08-17 在 DG4162（固件 00.01.14）CH1 上以 SDS1204X HD
CH3 外部测量 `0/1/2 V` 扫描时，`VOLT:OFFSet` 与旧代码使用的
`APPLy:USER` 都产生线性响应，拟合 `R²` 分别为 `0.99837` 和 `0.99901`；
HIGH/LOW 写法的 `R²` 仅为 `0.02014`，没有形成有效 DC 扫描。

因此 `DG4000Instrument.set_dc_voltage(v)` 使用 `VOLTage:OFFSet`，
`setup_dc()` 按「切换 DC 波形、设置 OFFSET、打开输出」执行。示波器测得的
电压可以与命令值存在比例差异，这由后级链路决定；只要随命令值线性变化即可用于
标定。DG4162 的 `VOLT:OFFSet?`、`APPLy?` 或 HIGH/LOW 查询都不能视为独立的
物理输出测量，驱动返回的查询值仅表示仪器报告值。

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
