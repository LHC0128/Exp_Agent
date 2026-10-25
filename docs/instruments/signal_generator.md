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
| 脉冲参数 | `:FUNCtion:PULSe:*` | 两款提供脉宽、占空比；基础延迟和边沿接口仅 DG4000 |
| 调制 | DG4000 `:MOD:*`；DG900 `:AM/:FM/:PM/:FSKey/:PWM` | AM / FM / PM / FSK / PWM，内/外调制源可选 |
| 扫描 | DG4000 `:SWEep` | DG900 驱动本轮未扩展扫频接口 |
| 脉冲串 | `:BURSt` + 触发命令 | 触发/门控模式、周期数、相位、周期和延迟 |
| 谐波输出 | DG4000 `:HARMonic` | DG900 驱动本轮未扩展谐波配置接口 |
| 参考时钟 | `:ROSCillator:SOURce` | 内部 10 MHz / 外部同步 |
| 输出控制 | `:OUTPut` | 开/关、负载阻抗、同步信号；输出极性接口仅 DG4000 |
| 状态管理 | DG4000 `*SAV` / `*RCL` | 保存/恢复仪器状态 |
| 计数器 | `:COUNter:STATe` / `:COUNter:MEASure?` | 两款均返回原始测量字符串 |
| 截图 | `:HCOPy:SDUMp:DATA?` | DG4000 仅 BMP；DG912 Pro 支持 BMP/PNG |
| 任意波文件 | DG912 Pro `:MMEMory:TRACe:ARB:DATA:DAC` | 内置存储、覆盖保护、准确上传点数记录 |

## 正式实验负载策略

通过 `lab_workflows.devices.create_signal_generator()` 连接的正式实验和仪器控制页，
会在连接后按本次物理量及其同机/共享通道组设置两路信号发生器的输出负载：
`Pump_modulation` 所在通道为 `50 Ω`，其余通道为 `High-Z`，包括 `Probe_AOM_Carrier`。
信号源端点可供不同实验复用；Z/时序与 AOM 载波绑定同一台设备时，按当前用途选择负载。
展开配置中的 `mapping_key` 保留用途；复用设备时使用物理量键或展开配置字典，单独传 resource
的旧调用继续采用整体资源策略。实验工作流不应再在波形配置
过程中分散修改负载；若需改变标定条件，应在映射或专用校准说明中明确记录。

参考时钟批量同步工具沿用工厂默认负载策略，连接时先设置两路负载，再设置参考时钟源。
加热信号和温度开关信号均设为高阻；更改负载后应核对幅度和偏置回读。
DG900 Pro 的高阻设置统一发送 `:OUTPut<n>:LOAD INF`，随后通过
`:OUTPut<n>:LOAD?` 回读验证：高阻返回约 `9.9E+37`，`1` 和 `50` 分别表示
`1 Ω` 和 `50 Ω`，不能作为高阻。数值负载也须与目标一致；回读不一致立即报错，
连接阶段验证失败时释放通信会话。`get_output_load()` 继续返回仪器原始字符串。
两款驱动均将高阻数值回读（`>= 9e37`，通常为 `9.9E+37`）识别为高阻，
再次设置时使用各自的高阻关键字；不能把该查询标记作为普通阻抗数值写入。
GUI 负载框将该标记显示并提交为 `INF`，因此读取高阻后直接点击“应用并回读”
也会正确设置高阻。普通 `1 Ω`、`50 Ω` 数值负载保持原语义；非正数或无效数值在写入前拒绝。

工厂支持 `create_signal_generator("Z_magnetic_field")`，按当前物理量绑定选择型号、
地址和通道；仍支持展开后的配置字典以及原来的 resource 字符串。返回未连接实例，
由调用方按原流程连接。连接失败直接报错，不自动换设备。

GUI「仪器控制 → 设备库」的信号源用途卡片提供 Resource 下拉，选择设备库中的实际仪器，
同机/共享通道组联动并保留通道；点击“保存设备库”才与锁相选择、设备配置一起生效。
同一次新模式实验独立使用的物理量占用同一信号源通道时预检拒绝启动，具体规则见
[设备库与映射说明](../instrument_mapping.md)。

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
# DG4000
dg.set_mod_am_internal_freq(100)
dg.set_mod_source("INTernal")     # 或 "EXTernal"
dg.set_mod_state(True)
```

DG900 Pro 的调制类型没有通用的 `MOD:TYPE/STATE` 命令，而是分别使用
`:AM:STATe`、`:FM:STATe` 等命令。驱动提供统一命名的参数方法，并通过
`set_mod_type_state()` 启停指定类型：

```python
dg = DG900Instrument("USB0::...", channel=1)
dg.set_mod_type("AM")
dg.set_mod_source("EXTernal")
dg.set_mod_am_depth(80)
dg.set_mod_internal_frequency("AM", 100)
dg.set_mod_state(True)
```

`set_mod_source(source, channel=None)` 使用本实例最近选择的调制类型；
`get_mod_source(channel=None)` 查询当前启用的类型，全部关闭时使用实例选择记录，
首次未选择时默认 AM。DG912 Pro 发送 `:SOURce<n>:AM/FM/...:SOURce`，
DG4000 发送自身的 `:SOURce<n>:MOD:<type>:SOURce`。

## 共同接口与旧 DG900 调用迁移

DG4000 的既有方法名称、签名和内容保持原样。DG900 的共同参数向 DG4000 对齐，
不再接受旧 `apply_wave()` 的频率、幅度等位置参数：

```python
# 旧：dg.apply_wave("SINusoid", 1000, 1.0, 0.0, 90.0, 2)
dg.apply_wave("SINusoid", channel=2, freq=1000, amp=1.0, offset=0.0, phase=90.0)
# 旧：dg.set_mod_source("FM", "EXTernal", 2)
dg.set_mod_type("FM", channel=2)
dg.set_mod_source("EXTernal", channel=2)
# 旧：dg.get_mod_source("FM", 2)
source = dg.get_mod_source(channel=2)
```

其他关键字迁移如下；位置参数顺序不变：

| 方法 | 旧 DG900 参数 | 统一参数 |
|---|---|---|
| `set_frequency` | `frequency` | `freq` |
| `set_phase_adjust`、`set_burst_phase` | `phase` | `angle` |
| `set_pulse_width` | `seconds` | `width` |
| `set_burst_ncycles` | `cycles` | `n` |
| `set_mod_fm_deviation`、`set_mod_pm_deviation` | `value` | `deviation` |
| `set_mod_fsk_frequency` | `value` | `freq` |
| `set_mod_fsk_rate` | `value` | `rate` |
| `set_mod_fsk_polarity` | `value` | `polarity` |
| `set_mod_pwm_deviation_dcycle` | `value` | `percent` |

`set_ref_clock_source(source)` 现在要求显式提供来源。`get_output_load()` 在两款上均
返回未展开的负载字符串。`apply_wave()` 本身不发送输出开关命令；`setup_*()`
便捷方法保持打开输出的行为。Pro 的 `CUSTom/USER` 波形名映射到 `ARBitrary`。
噪声 APPLy 使用 Pro 必需的频率占位符；`delay` 不对应 Pro 脉冲相位，因此明确拒绝。
共同脉冲 APPLy 不接收 `phase`，需单独调用 `set_phase_adjust()`。
本轮未扩展 DG900 的扫频、谐波、输出反相和基础脉冲边沿接口；共享配置请求这些
未实现功能时会报错，默认的普通波形和 AM/FM/PM 配置使用已有 Pro 接口。

## 计数器、截图和显式恢复

两款新增相同签名，均无需 GUI 面板：

| 接口 | 返回值与行为 |
|---|---|
| `set_counter(enable=True)` | `None`，控制频率计开关 |
| `get_counter_measure()` | 原始 `str`，不拆分五项测量值 |
| `get_screenshot(format="BMP", save_path=None, timeout_ms=15000)` | 完整图片 `bytes`；保存后仍返回字节 |
| `recover_connection(drain_reads=3)` | 身份 `str`，清理残留通信，不发送 `*RST` |
| `recover_connection_with_reset(wait_seconds=8.0)` | 身份 `str`，显式发送 `*RST` |
| `reset_stuck_resource(resource_string, wait_seconds=8.0)` | 类方法，无需成功首次连接，复位后返回身份并关闭临时资源 |

DG4000 只支持 BMP，请求 PNG 会报错，且不发送 Pro 的格式命令。Pro 使用
`:HCOPy:SDUMp:DATA:FORMat` 设置 BMP/PNG。读取确定长度二进制块或 Pro 图片流时，
检查完整图片长度；截断数据报错，不补零、不降级格式。截图结束或失败都会恢复
VISA 的超时和读终止符。普通连接和恢复不会自动复位；只有显式调用带复位的接口
才恢复仪器默认设置，调用方需要重新配置波形、输出与时钟。

## DG912 Pro 内置任意波文件

本节仅适用于 `DG900Instrument`。DG4000 本轮不提供文件接口，其手册没有本次
要求的内置任意波文件传输与存储对应能力。协议只声明两款共同具备的能力。

```python
points = dg.upload_arb_wave("INT:\\control.arb", normalized, advanced=True)
dg.config_arb_wave(1, "INT:\\control.arb", freq=50, vpp=1.0, points=points)
files = dg.list_files("INT:\\")
# 覆盖时必须显式授权：dg.upload_arb_wave(..., overwrite=True)
```

`upload_arb_wave(file_path, data, advanced=False, *, overwrite=False)` 返回上传点数。
普通模式必须恰好 16,384 点；高级模式允许 32～16,777,216 点（不自动启用 32 Mpts
选件）。输入必须为一维有限实数并位于 `[-1,1]`，不裁剪、重采样或降频。
文件名必须以 `.arb` 结尾，省略存储器前缀时使用 `INT:\\`；路径统一后检查同名文件，
默认拒绝覆盖。每段 ASCII 数据不超过 20,000 字节，单段用 `END`，多段用
`HEADer/CONTinue/END`；每段等待 `*OPC?` 并检查错误队列。仅全部成功后记录点数，
传输失败使相关记录失效并恢复 VISA 超时和终止符；已写入的部分文件可能仍留在仪器。

`get_arb_wave_points(file_path)` 只返回当前实例成功上传的准确记录。新实例、外部
上传或未知文件会报错；不会用文件字节大小推算点数。通过外部工具替换已记录文件时，
调用方应使用新实例或为配置显式提供实际点数。

`config_arb_wave(channel, file_path, freq=None, sample_rate=None, vpp=1.0,
offset=0.0, phase=0.0, *, points=None)` 不改变输出开关。`freq` 与 `sample_rate`
必须且只能提供一个，点数优先取显式 `points`，否则取本实例成功上传记录。
16,384 点且提供 `freq` 时使用普通模式；其他情况使用高级模式，高级模式按
`points × freq` 换算采样率，范围为 `1e-6～312.5e6 Sa/s`，超限报错，不自动降频。
支持 `.arb/.csv`；`.txt` 需要额外分隔符和数据类型，本接口明确拒绝。
已有文件的显式 `points` 必须由调用方确保准确。

`list_files(path="INT:\\")` 返回字典列表，每项包含 `name`、`attribute`、`size_bytes`，
保留文件夹和未知文件属性；空目录返回空列表。`delete_file(file_path)` 删除文件，
等待操作完成并使本实例对应点数记录失效。`set_mod_am_dssc(state, channel=None)`
通过 Pro 的 `:SOURce<n>:AM:DSSC` 控制载波抑制，不改变调制开关。

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

2026-09-08 在 Z 通道 DG4162（DG4E242401288，固件 00.01.14）直接测量 CH1 时，
GUI 的 DC/OFFSET 应用出现设定 +0.2 V、实际输出 -2.5 V 的异常。
改为先选择 DC，再下发 `APPLy:USER 0,0,0.3,0`，用户实测为 +0.3 V；
从残留 5 Vpp 的状态重复此流程也得到 +0.3 V，无需提前清零幅度。
这说明此前 OFFSET 扫描记录不足以保证 GUI 完整应用流程正确。

当前 `DG4000Instrument.set_dc_voltage(v)` 在已选择 DC 的前提下使用
`:SOURce<n>:APPLy:USER 0,0,<v>,0`；`setup_dc()` 按「选择 DC、设置电平、
打开输出」执行。DG4000 中文编程手册的 `APPLy:USER` 条目明确说明：当前任意波为
DC 时，仅提取偏移参数，丢弃频率、幅度和相位。GUI 在单位、负载、Mod/Burst
设置完成后再选择 DC、设置电平，最后按请求设置输出开关；选择 DC 会自动关闭
调制、扫频和脉冲串。DG900 继续使用自身的 `APPLy:DC` 路径。

本次实测 +0.3 V 时，`VOLT:OFFSet?` 和 `APPLy?` 仍报告偏移 0 V，HIGH/LOW
也未反映实际 DC 电平。本次仅修复应用路径，回读行为保持不变：GUI 的偏置输入框
仍可能被回读值覆盖为 0 V，不能据此认定实际输出为 0 V；驱动没有用缓存设定值
替代仪器回读。后续再次应用前应重新填写目标 DC 电压。

## GUI 行为

仪器控制页以多列网格展示全部物理量，打开时不连接硬件；可逐项读取，也可顺序读取
全部目标。每个信号源面板只读取对应 mapping key 的通道。首行按三栏显示：DC 为“波形 / 偏置 / V（固定）”，交流为“波形 / 幅度 / 单位”；交流的频率、偏置和相位位于下一行。波形细节、负载、调制和 Burst 默认折叠，写入成功后用设备实际回读值替换表单状态。

正弦、方波和斜波支持 Vpp / Vrms / dBm，脉冲与任意波暂只开放 Vpp，不估算脉冲边沿或任意波 RMS。dBm 仅在明确的数值负载下开放，高阻（含 `9.9E+37` 回读标记）和未知负载下禁用。选择单位只修改表单草稿，幅度输入值按所选单位解释；点击“应用并回读”才写入。偏置始终以 V 表示，DC 不使用残留的交流幅度。

后端将正弦、方波、斜波幅度分别按 `Vpp = 2√2 × Vrms`、`2 × Vrms`、`2√3 × Vrms` 换算；dBm 先按 `Vrms = √(R × 10^(dBm/10) / 1000)` 换算，其中 R 为数值负载。校验偏置与正负峰值后，统一在 Vpp 下写入幅度，再恢复所选显示单位；新旧控制接口复用同一实现。

- 启用 Mod 时自动关闭 Burst；启用 Burst 时自动关闭 Mod。
- 基础输出电压写入前继续使用 `params/safety_limits.yaml` 校验。
- `rf_coil` 作为 `Y_magnetic_field` 的历史别名不单独显示，手动控制统一使用 Y 场面板。
- 已知单位的 Vrms/dBm 状态可继续编辑并应用；未知单位仅允许关闭输出或恢复 Vpp 后重新读取。只关闭输出时不重写波形、幅度或负载。
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
setup.apply_all({inst.resource_string: inst for inst in [sg1, sg2]})
```

## 模块结构

```
src/signal_generator/
  __init__.py        # 包导出
  dg4000.py          # DG4000 PyVISA 封装 + SCPI 命令
  dg900.py           # DG900 Pro 官方 SCPI 封装
  types.py           # 两系列公共参数类型
  protocol.py        # 两系列公共协议与能力描述
  _visa.py           # 新增公共截图与显式通信恢复工具
  config.py          # 配置与多仪器管理 (SignalGeneratorConfig, MultiGeneratorSetup)
  instrument.py      # 旧 DG4000 导入路径的兼容入口
  test_interfaces.py # 假仪器接口、截图、文件与恢复回归
  README.md          # 文件职责、阅读顺序、导入与验证入口
```

源码导航见 [src/signal_generator/README.md](../../src/signal_generator/README.md)。
两款驱动是平级的独立实现，共同参数类型来自 `types.py`；旧的
`from signal_generator.instrument import DG4000Instrument` 继续有效。

详细说明见 [examples/signal_generator_demo.ipynb](../../examples/signal_generator_demo.ipynb)。
