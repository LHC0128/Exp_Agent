# RIGOL DHO900 与统一示波器接口

## 本轮范围

- `dho900.DHO900Instrument` / `DHO900Acquisition` 提供基础控制与单帧/多通道读取。
- `oscilloscope.create_oscilloscope(config)` / `create_scope_acquisition(scope)` 按型号选择 SDS 或 DHO900，实现相同的实验侧调用。
- `AcquisitionConfig`、`ChannelConfig`、`TriggerConfig`、`AcquisitionResult` 复用现有 SDS 类型，不迁移旧类型或修改旧公开导入。
- 设备库新增 `rigol_dho900_192_168_124_21`，通信资源 `TCPIP0::192.168.124.21::INSTR`。HTTP 地址是 LXI 网页，不是 SCPI 传输地址。具体子型号、序列号和 VISA 可达性均待在运行目录确认。
- **现有正式实验、GUI 实时控制和 `scope_waveform` 的 SDS 映射保持不变。** 新工厂不因条目入库就接管旧程序；不要只改映射而继续直接构造 `SDSInstrument`。

本功能与 T9 并行：新连接入口在构造 VISA 管理器之前执行 `require_hardware_access()`，并登记到门控回归清单。开发目录不能连接真实设备，也不创建放行标记；导入、构造、配置校验、设备库加载均不自动扫描硬件。本文示例只供用户在获得许可的**运行目录环境**使用，本轮只运行假仪器测试。

## 两台共用的调用方式

```python
from lab_workflows.common import load_mapping
from oscilloscope import (
    AcquisitionConfig, ChannelConfig, TriggerConfig,
    create_oscilloscope, create_scope_acquisition, save_to_npz,
)

# 后续实验迁移时，映射须经设备库校验后解析；本轮未切换此映射。
config = load_mapping()["scope_waveform"]
scope = create_oscilloscope(config)  # 构造不连接；SDS 与 DHO900 使用相同调用
acquirer = create_scope_acquisition(scope, maximum_points=1_000_000)
request = AcquisitionConfig(
    sampling_rate=1_000_000, sampling_time=0.001,  # 1000 点；实机还须通过回读校验
    memory_management=None,
    channels=[ChannelConfig(1, scale=0.5, impedance="ONEMeg", probe=1)],
    trigger=TriggerConfig(mode="SINGle", source="C1", slope="RISing", level=0),
)
try:
    scope.connect()
    results = acquirer.acquire_all(request)
finally:
    scope.disconnect()

# 放在实验要求的温控恢复之后保存，不在关温控采集窗口内写文件。
save_to_npz("capture.npz", results, request.to_dict())
```

如果只从库里选择设备而不使用物理量映射，先调用 `load_device_definitions()`，然后将选中条目的 `to_config()` 交给同一个工厂。DHO900 类型为 `oscilloscope`，型号可用系列 `DHO900` 或实际 `DHO914/DHO914S/DHO924/DHO924S`；SDS 的既有 `sds_acquisition/SDS` 键不变。未知型号拒绝，不按 IP 或身份查询自动改选另一台设备。

采集器共同提供 `apply_config(config)`、`start_acquisition(config)`、`stop_acquisition()`、`acquire_channel(channel, timebase_scale, horiz_divisions=10, trim_points=0)`、`acquire_all(config)`。分步读取前调用方须确认示波器停止；`acquire_channel()` 不替调用方停止正在运行的采集。传入 `check_cancelled` 可在等待、通道与分块之间协作取消；取消和异常均向调用方传播，整次采集会尝试 STOP。它不控制信号源或温控，也不关闭任何内置发生器输出；实验安全收尾仍由工作流负责。

## 兼容范围与不等价功能

| 功能 | DHO900 基础接口 |
| --- | --- |
| 连接、身份、显式复位、断开 | 与 SDS 同名；连接仅查身份、不复位；只接受真实 DHO900 子型号回读 |
| 时基、偏移、通道开关、量程、耦合、探头比 | 与 SDS 同名；C1--C4 转为 RIGOL CHANnel1--4 |
| 输入阻抗 | 固定 1 MΩ；`ONEMeg` 接受，不发送不存在的阻抗命令；50 Ω 明确拒绝。返回值是型号常量，不伪称查询回读 |
| 采样率 | 协调时基和离散深度后严格回读；`set_sampling_rate()` 是实际率校验，不发送不存在的 SRATe 写命令 |
| 独立存储管理 AUTO/FSRate/FMDepth | 不支持，不伪造 FSRate；完整配置要求 `memory_management=None` |
| 采集类型 | Normal、Peak、Average（平均次数 2--65536 二次幂）；ERES/ULTRA 不纳入基础接口 |
| 触发 | EDGE；AUTO/NORMal/SINGle、上升/下降/交替边沿；DHO900 不支持 EXT，SDS FTRIG 完整帧协议明确拒绝 |
| 触发状态 | TD/STOP/WAIT/RUN/AUTO 规范为 SDS 已用的 Trig'd/Stop/Ready/Arm/Auto；未知回读报错 |
| 单帧采集 | 内部 SINGLE 后等待 STOP；AUTO 额外 TFORce，Normal/Single 等真实边沿。完成后保持 SINGLE/STOP，不重启 AUTO/NORMAL，原请求保留在快照 |
| 波形 | RAW、ASCII 实际电压、分块读完整停止帧、按 preamble 构建时间轴；不套用 SDS 二进制 preamble 或 ADC 公式 |
| 保存 | 统一 `oscilloscope.save_to_npz()` 默认 voltage_time；也可 CSV。SDS 旧保存接口不变 |

### 严格采样策略

目标总点数须在手册深度表中：1000、10000、100000、1000000、5000000、10000000、25000000、50000000。1/2/3--4 个启用通道的最大深度分别为 50M/25M/10M。目标率×时长不在深度表时，**配置前拒绝，不自动选近似值**；这是本轮严格模式的实现范围，不是断言其他配置策略在硬件上永远不可行。

完整配置显式关闭自动 ROLL、切回主时基并关闭延迟扫描；长时基否则可能自动进入不支持触发的滚动模式。配置及每次布防前查询录制/回放状态，启用时拒绝而不覆盖用户设置；检查 SCPI 错误队列，命令拒绝不会被当成成功。实际采样率和时基使用相对误差 `1e-6` 的数值回读容差，不提供科学意义上的近似采样选项。实际率/深度/主时基不符就停止并报告；读帧时再次核对真实 `xincrement` 和整帧点数。

不自动插值或降采样。请求配置与真实回读分开存储；时间轴由 `xorigin + (全局零基索引 - xreference) * xincrement` 构建，不使用请求采样率冒充实际值。深度/时基/通道配置是离散值，能通过静态深度校验不代表真机一定接受，回读仍是必需步骤。

### ASCII 与原始数据

手册明确 ASCII 返回每点实际电压，本轮采用此路径避免假设 WORD 的符号/端序。客户端分块预算默认 10000 点，可由 `connection.read_chunk_points` 调整；它不是厂商保证的单次传输上限。读取先恢复 POINts 与全帧起止窗口，核对 preamble 点数等于存储深度，再逐块读取；任一分块长度、数值、停止状态不符即失败，不补点或返回伪完整帧。

ASCII 不提供原始 ADC 码，所以 `AcquisitionResult.raw_data` 明确为空，`raw_encoding=unavailable_ascii_voltage`。统一 NPZ 默认保存真实 `time` 和 `voltage`，并将逐通道请求/实际配置快照保留在 `config.scope_acquisition_snapshots`，写后可用同一 `load_npz()` 还原；不要把 DHO900 结果传给 SDS 默认 `save_mode="raw"`、紧凑 HDF5 或 BIN。统一包装会拒绝 raw/voltage_only 模式，不伪造 ADC 字段。本轮没有宣称所有 SDS 原始二进制保存格式与 DHO900 等价。ASCII 比二进制传输量大，长记录导出性能需真机确认。

## 依据与验证

- [厂商编程手册](../../docs/instruments/DHO900_ProgrammingGuide_CN.md)：§2.2.2 LAN/LXI；§3.3 深度与只读采样率；§3.6 通道；§3.19 录制/回放；§3.26 主时基与 ROLL；§3.27 触发；§3.28 十字段 preamble、ASCII 和一基起止点。
- [DHO914 产品规格](https://www.rapidonline.com/rigol-dho914-dho900-digital-oscilloscope-125-mhz-1-25-gs-s-12-bit-10-4764)：输入阻抗 1 MΩ。该常量不代表本次远程回读，其他实际子型号应随身份核对。
- 假传输测试覆盖门控先于 VISA、身份错配清理、同名命令翻译、严格率拒绝、ROLL/录制前置、触发超时、分块/时间轴/异常/取消停止、NPZ 往返以及设备库不切 SDS。

```powershell
agent_exp_env\Scripts\python.exe -m pytest src/dho900 src/oscilloscope src/hardware_gate/test_gate.py lab_workflows/test_dho900.py -q --basetemp=.cache/pytest-tmp/dho900
```

## 待查

- 现场 IP 对应的具体子型号、固件、序列号及 VISA/LXI 可达性。
- 本机固件下 SINGLE/TFORce 后的完成状态、ASCII RAW 深度窗口回读语义及大记录传输性能；假仪器测试不等于真机验收。
- WORD 的符号/端序、每次二进制传输的定量上限和实际测量无效值标记；基础接口不依赖这些未确认细节。
- 若要迁移现有正式实验，需逐个替换构造/采集器并处理其 FSRate、FTRIG、原始数据保存和质量条件；本轮不改实验采集时序、温控、数据目录或 T9 状态。
