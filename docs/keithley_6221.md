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
    resource: TCPIP0::169.254.100.22::1394::SOCKET
    reference_clock: null
    connection: {}
    capabilities: {}
```

当前仓库已经配置设备实例 `keithley_6221_4503331`，并通过独立物理量
`keithley_6221_main_field` 绑定到 Z 方向小磁场线圈用途；对应安全范围为 `-100 到 100 mA`。
`main_magnetic_field` 仍保留给 GS200，用于独立的 Z 主磁场线圈。`mx-keithley-6221-main-field-calibration`
仍是 6221 独占主线圈的历史标定实验；`mx-keithley-6221-optimal-control-rf-sensitivity`
则允许 GS200 同时以非零电流驱动主磁场线圈。

## Python API

驱动的电流单位为 A，Compliance 单位为 V，频率单位为 Hz，时间单位为 s；GUI 和
电流源控制 API 的电流单位为 mA。

```python
from keithley_6221 import Keithley6221Instrument

with Keithley6221Instrument(
    "TCPIP0::169.254.100.22::1394::SOCKET"
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
- `set_compliance_test()` / `is_in_compliance()`

波形方法包括：

- `set_waveform_function()`、`set_waveform_frequency()`、
  `set_waveform_amplitude()`、`set_waveform_offset()`
- `set_waveform_duty_cycle()`、`set_waveform_ranging()`、
  `set_waveform_duration()`
- `upload_arbitrary()`、`get_arbitrary_point_count()`
- `arm_waveform()`、`start_waveform()`、`abort_waveform()`

连接与诊断方法包括 `idn()`、`reset()`、`clear_status()`、
`wait_for_operation_complete()`、`get_error()` 和 `raise_for_errors()`。

本仓库已在固件 `D04 /700x` 的真机上确认 Ethernet SCPI 使用 TCP 端口 `1394`，
PyVISA Resource 形式为 `TCPIP0::<IP>::1394::SOCKET`。驱动会为 SOCKET 会话自动设置
换行读写终止符。该固件会把 `FIXED` 量程回读为 `FIX`，并以约 `9.9e37` 表示无限
时长，驱动会统一转换为公开 API 使用的 `FIXED` 和 `INF`。

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

GUI 将导入波形的数值统一解释为 Hz，接受以下两种 `.csv` 或 `.txt` 文件；空行和
以 `#` 开头的注释行会被忽略：

- 单列 `frequency_Hz`，重复频率需在界面手动设置。
- `time_s,frequency_Hz` 两列时域波形，可带表头。时间轴必须严格递增、等间隔，
  GUI 使用 `1 / (点数 * 时间步长)` 自动填写重复频率，并以 Hz 范围的中心和半跨度
  将第二列居中归一化到 `[-1, 1]`。

可选再导入 `mx-keithley-6221-main-field-calibration` 生成的 `analysis.yaml` 或等价
JSON。GUI 读取 `K_f_Hz_per_mA` 和 `f_0mA_Hz`，按
`I_mA = (f_Hz - f_0mA_Hz) / K_f_Hz_per_mA` 换算，并自动填写 6221 电流峰值幅度和
偏置；换算包络必须位于当前 mapping 的安全范围内。没有标定文件时，Hz 数据只决定
归一化波形形状，实际电流继续由界面中的峰值幅度和偏置手动指定。

例如理论输出 `time_s,Omega_ctrl_Hz` 可以直接导入，GUI 会显示 Hz 范围和推导频率；
导入标定后还会显示 Kf、f0、R² 和实际电流范围。浏览器完成格式、点数和安全范围校验
后，API 只接收归一化数值数组，不接收或保存原始文件与标定文件。

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

设备已通过 Ethernet 接入；以下真实硬件验收项目仍需在确认 INTERLOCK、6221 独占
线圈且 GS200 已物理断开后逐项执行：

1. 核对 `*IDN?` 与 VISA Resource。
2. 在输出关闭状态写入并回读 0 A。
3. 以物理安全限值内的低幅正弦验证配置、启动和停止。
4. 上传短任意波并核对 `ARB:POIN?`。
5. 确认停止及异常处理后输出关闭且电流归零。

通用控制页首版不验收相位标记。外部 Trigger Link 仅由下述专用实验工作流使用。

## 6221 最优控制 RF 灵敏度实验

实验 `mx-keithley-6221-optimal-control-rf-sensitivity` 使用标定结果
配置指定标定运行的自由斜率和截距，将理论控制频率换算成 6221 电流任意波，
不再使用 DG4000 作为 Z 主场控制源。实验按完整控制包络校验并选择固定电流档位，
使用 `FAST`、模拟滤波关闭、`15 V` Compliance，并在 `Time_sequence_2` 的每个下降沿
触发一个 ARB0 周期；`IGNORE=OFF` 按手册在重触发时立即重启当前周期。

当前主线圈真机诊断中，直流 10 mA 在 1 V 下正常；30 kHz、约 +/-5.9 mA 的 ARB0
动态输出在 5 V 下进入 Compliance、6 V 开始通过；30 kHz 外部重复触发时 12 V
仍曾失败、13 V 连续 2 秒通过。因此该实验固定使用 15 V 并保留运行中 Compliance
检查。这个 15 V 结论不能直接外推到其他线圈、接线或波形。

接线为 `Time_sequence_2 CH2 -> BNC T -> 6221 Line 1 + Y RF DG4000 Ext Trig`，
6221 `Line 2` 不接。触发频率由控制波形时间轴自动锁定，GUI 中只读显示；波形末点
为 0 mA，任意波在触发间隔期间保持 inactive 0 mA。GS200 必须物理断开，程序仍会
保持 GS200 `0 mA + output off`。

## 主场标定实验

正式实验 `mx-keithley-6221-main-field-calibration` 使用 6221 独占 Z 主线圈，
按 `5、6、7、8、9 mA` 扫描，并在 `50、60、70、80、90 kHz` 预测中心附近
各扫描 `+/-10 kHz`。实验固定 `1 V` Compliance，每个电流稳定后及每个 Y RF
采集点前后检查 Compliance，命中后立即关断并终止。

运行前必须将 GS200 从 Z 主线圈物理断开并在 GUI 中明确确认。程序仍会连接 GS200，
将其设为 `0 mA` 并关闭输出；任何退出路径都不恢复两台电流源的运行前非零状态，
而是保持二者 `0 mA + 输出关闭`。两台电流源不得并联连接在线圈上。
