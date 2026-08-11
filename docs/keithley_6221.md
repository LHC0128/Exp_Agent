# Keithley 6221 精密电流源

## 概述

`keithley_6221` 是 Keithley 6221 精密电流源的独立 PyVISA 驱动。它与
Yokogawa GS200 使用不同的设备类型和驱动，不共享型号定义。首版支持直流输出、
内部正弦/方波/Ramp、易失性任意波、时间或周期数时长以及波形启动和中止。

实现依据是 Tektronix 发布的
[Model 6220/6221 Reference Manual, 622x-901-01 Rev. C](https://download.tek.com/manual/622x-901-01%20(C%20-%20Oct%202008)(Ref).pdf)。
仓库不保存该 PDF。

## 设备库配置

设备接入电脑并确认 VISA Resource 后，可在 GUI 的“设备库”中通过 VISA 扫描加入，
或使用如下设备定义：

```yaml
devices:
  keithley_6221_example:
    instrument: keithley_6221
    model: "6221"
    label: Keithley 6221
    resource: USB0::0x05E6::0x6221::SERIAL::INSTR
    reference_clock: null
    connection: {}
    capabilities: {}
```

物理用途确定后，还必须单独增加 mapping 和对应的安全上下限，才能出现在“设备控制”
页面。当前仓库没有预置 6221 设备实例、物理量映射或安全限值。

## Python API

驱动的电流单位为 A，Compliance 单位为 V，频率单位为 Hz，时间单位为 s；GUI 和
电流源控制 API 的电流单位为 mA。

```python
from keithley_6221 import Keithley6221Instrument

with Keithley6221Instrument(
    "USB0::0x05E6::0x6221::SERIAL::INSTR"
) as source:
    print(source.idn())
    source.set_compliance(10.0)
    source.set_autorange(True)
    source.set_current(1e-3)
    source.set_output(True)
    source.set_output(False)
    source.set_current(0.0)
```

直流方法包括：

- `set_current()` / `get_current()`
- `set_current_range()` / `get_current_range()`
- `set_autorange()` / `get_autorange()`
- `set_compliance()` / `get_compliance()`
- `set_analog_filter()` / `get_analog_filter()`
- `set_output_response()` / `get_output_response()`
- `set_output()` / `get_output()`

波形方法包括：

- `set_waveform_function()`、`set_waveform_frequency()`、
  `set_waveform_amplitude()`、`set_waveform_offset()`
- `set_waveform_duty_cycle()`、`set_waveform_ranging()`、
  `set_waveform_duration()`
- `upload_arbitrary()`、`get_arbitrary_point_count()`
- `arm_waveform()`、`start_waveform()`、`abort_waveform()`

连接与诊断方法包括 `idn()`、`reset()`、`clear_status()`、
`wait_for_operation_complete()`、`get_error()` 和 `raise_for_errors()`。

## 设备边界

| 参数 | 驱动边界 |
|---|---:|
| 直流电流 | -105 mA 到 +105 mA |
| Compliance | 0.1 V 到 105 V |
| 波形峰值幅度 | 2 pA 到 105 mA |
| 波形频率 | 1 mHz 到 100 kHz |
| 方波占空比 | 0% 到 100% |
| 时间时长 | 100 ns 到 999999.999 s，或无限 |
| 周期数 | 0.001 到 99999999900，或无限 |
| 任意波点数 | 2 到 65535 |

设备边界不能代替物理 mapping 的安全限值。控制服务会先验证完整电流包络：内置波形
使用 `offset - amplitude` 到 `offset + amplitude`；任意波使用
`offset + amplitude * min(points)` 到 `offset + amplitude * max(points)`。

## 任意波与 ARB0

首版只写入易失性 `ARB0`，不会复制到非易失性波形槽。点值必须是 `[-1, 1]` 内的
有限归一化数值。驱动以 `SOUR:WAVE:ARB:DATA` 写入首批数据，后续使用
`SOUR:WAVE:ARB:APPEND`，每条命令最多包含 100 点。

GUI 接受单列 `.csv` 或 `.txt` 文件。每个非空行只能有一个数值，可选第一行
`value`。浏览器完成格式和点数校验后，API 只接收数值数组，不接收或保存原始文件。

## 控制 API

控制目标接口为：

```text
PUT /api/control-targets/{mapping_key}/current-source
```

直流设置支持 `current_ma`、`output`、`confirm_output_enable`、
`current_range_ma`、`autorange`、`compliance_v`、`analog_filter` 和
`output_response`。`waveform` 是独立嵌套请求，`action` 可为 `configure`、
`configure_and_start` 或 `abort`。直流字段不能与 `waveform` 混用。

`configure_and_start` 必须携带 `confirm_start: true`。配置前会先中止现有波形并关闭
输出；配置完成后保持输出关闭，启动才继续执行 `ARM` 和 `INIT`。停止不要求确认。
切换到直流会先中止波形；改变量程或输出响应会先关闭输出，若要重新开启必须再次确认。

6221 快照包含直流回读、波形配置和任意波点数。驱动没有可靠的 SCPI 查询可以确认
内部波形此刻是否仍在运行，因此快照不会提供推测性的 `running` 字段。

## 故障处理与硬件验收

6221 的写入、任意波上传或回读失败时，服务会依次尝试中止波形、关闭输出和将直流
电流归零。安全关断本身失败时，响应会同时保留原始异常和各项关断异常。

由于当前设备尚未接入，以下项目需要连接真实硬件后完成：

1. 核对 `*IDN?` 与 VISA Resource。
2. 在输出关闭状态写入并回读 0 A。
3. 以物理安全限值内的低幅正弦验证配置、启动和停止。
4. 上传短任意波并核对 `ARB:POIN?`。
5. 确认停止及异常处理后输出关闭且电流归零。

首版不验收相位标记或外部 Trigger Link。
