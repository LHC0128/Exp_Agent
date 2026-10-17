# HF2 / MFLI 锁相放大器控制模块 (`lockin_amplifier`)

两种设备均使用 LabOne 的 `zhinst.core` Python Core API。LabOne 是服务器、用户界面和编程接口的整体软件平台；Python Core API 是其中的 Python 通信接口。

## 源码结构与兼容入口

型号实现分别位于 `src/lockin_amplifier/hf2.py` 和 `mfli.py`，负责连接默认值、硬件与选件能力、输入来源、clockbase 节点类型、输出电压换算和 Scope 设置。公共通信位于 `base.py`，`acquisition.py` 集中实现解调采集选择、能力校验、档位选择和停流恢复；解调配置、DAQ、Aux 和 Scope 采集复用公共模块。

包级导入和 `lockin_amplifier.instrument` 旧导入入口保持有效，`LockinInstrument` 的原默认 HF2 行为保留。实验参数、能力字典和采集结果格式沿用现有接口。阅读顺序、创建示例和无硬件验证命令见 [锁相放大器源码阅读指南](../src/lockin_amplifier/README.md)。

## 设备选择与连接

在 GUI「仪器控制 → 设备库」的单张**锁相放大器**卡片内，选择全系统使用的 HF2 或 MFLI。
选择先更新全部锁相映射草稿，并保留每个 mapping 的解调器索引，点击“保存设备库”后才生效。
已有设备配置草稿时可继续选择；运行中或排队中的硬件任务允许编辑草稿，结束后才能保存。
设备库与映射两份修订号一起校验，保存冲突或失败保留草稿。其他浏览器的过期请求须刷新后再操作。

当前设备定义来自 [devices.yaml](../params/devices.yaml)：HF2 为 `dev18246`，MFLI 为 `dev30661`，目标时钟 `EXT`。MFLI 使用电脑本地 Data Server `127.0.0.1:8004`，由该服务器通过 USB RNDIS 地址 `192.168.150.246` 连接仪器；实际报告的接口为 `1GbE`，设备库显式保留这一值。HF2 使用 API Level 1，MFLI 使用 API Level 6。服务器需可访问；服务器连通但设备连接失败时，驱动仍会报错。
`host:port` 是 Python 访问数据服务器的地址，`interface` 是数据服务器访问仪器测量单元的接口。访问 MFLI 内置服务器时，即使电脑使用 USB 或网线，接口仍为内部 `PCIe`；使用电脑本地服务器时，本机 USB RNDIS 链路报告为 `1GbE`。接口以服务器实际报告为准，扫描读取 `/zi/devices`，连接后可读取 `/dev30661/system/activeinterface`；不能仅凭接口名判断外部接线。设备库标签用于说明当前物理连接，不会固定服务器的链路。此区别见 [MFLI 官方连接说明](https://docs.zhinst.com/mfli_user_manual_cn/getting_started/labone_software_startup.html#available-devices)。
USB 使用 Windows RNDIS 驱动，虚拟网卡名称可能显示为“以太网”。当前实机已核对：电脑 `192.168.150.245` 经 Zurich Instruments MF Instrument RNDIS 网卡连接仪器 `192.168.150.246:8010`，API 的服务器地址为 `127.0.0.1:8004`。USB 地址变化时更新下述服务器启动参数，而本地服务器地址保持不变。仪器原网口地址为 `192.168.124.109`。连接失败不自动回退。详见 [官方 USB 连接说明](https://docs.zhinst.com/mfli_user_manual_cn/getting_started/device_connectivity_instrument.html)。
MFLI 的离线声明包含 MF-F5M、MF-MD、MF-DIG、MF-PID；连接后实际选件以设备回读为准。

HF2 与 MFLI 使用独立的电脑服务器：HF2 为 `ziServer.exe`（`127.0.0.1:8005`），
MFLI 为 `ziDataServer.exe`（`127.0.0.1:8004`）。使用两种设备时应分别启用服务器，
MFLI 服务器不能代替 HF2 服务器。HF2 USB 设备已被 Windows 识别但 `8005` 无法连接时，
先在 LabOne 连接窗口的“配置”中检查“HF2 数据服务器”，可勾选其“自动开始”避免下次未启动；
本机已启用这一选项，并通过 API 回读及 LabOne 的 `Connected` 状态核对 HF2 连接。

Python 解调时序实验通过 `lab_workflows.devices.create_lockin(load_mapping()[key])` 选择驱动。历史变量 `hf2`、`hfi`、`HF2_*` 参数和 `hf2_configuration` 数据键继续保留，代表当前映射的锁相设备。未注册的 `quick_test_scan.py`、`optimize_scan.py`、`optimize_scan_round2.py` 也使用设备库与采集预检，保留直接脚本入口；Notebook 保持原行为。

### 同时连接 USB 与网线

当前实机仅接 USB，已确认本地服务器使用 USB RNDIS 链路。两线同时连接后应再次核对实际链路；
断开设备或重启服务器后，仅凭序列号和 `interface: 1GbE` 无法明确指定 USB 地址。
需要固定 USB 时，可使用已安装的 LabOne Data Server 提供的 `--device-ip` 参数。
它关闭自动多播设备发现，只保留指定 IP 的设备；这里指定 USB IP，而不指定网口 IP。

1. 停止相关采集并释放实验会话。在 LabOne 连接窗口进入“配置”，关闭电脑本地的“LabOne 服务器”，避免启动同机重复实例。HF2 使用独立服务器，无须随之关闭。
2. 在 PowerShell 启动只发现 USB 地址的本地服务器：

   ```powershell
   Start-Process -FilePath 'C:\Program Files\Zurich Instruments\LabOne\DataServer\ziDataServer.exe' -ArgumentList '--device-ip=192.168.150.246', '--hide-console=1' -WindowStyle Hidden
   ```

3. LabOne 与设备库继续使用 `127.0.0.1:8004`、`dev30661` 和 `interface: 1GbE`，然后新建实验会话。
4. 插回网线后核对电脑 Data Server 的已建立连接，目标应为 `192.168.150.246`，路由应走 Zurich RNDIS 网卡。`1GbE` 显示本身不能证明使用哪条物理链路。

这一限制作用于该 Data Server 的全部设备；若同一服务器还需访问其他仪器，按其帮助说明添加逗号分隔的 IP 列表。
USB 地址变化时应修改启动参数。电脑下次重新启动默认服务器时也须使用相同参数；
本项目不会自动改写 Windows 的 LabOne 服务启动方式；带上述 IP 限制的服务器不会发现仪器的网口地址。
不要通过仪器 IP 打开另一个内置服务器会话争用设备。
该启动方法已核对 LabOne 26.07.2.5 的 `ziDataServer.exe --help-all`；两线同时连接及 A14 噪声采集仍需实机验收。

## 相同函数与硬件差异

正式实验在连接后调用 `lab_workflows.steps.initialize_lockin_inputs()`，将所有主电压输入的
`ac` 和 `imp50` 写为 0 并同步，保留量程与差分。HF2 覆盖两路，MFLI 覆盖实际支持的一路。
后续 `SignalInputConfig` 默认保持 DC 耦合、高阻；正式实验不再提供重新开启这两项的配置。
旧实验参数键继续读取并归一到关闭值，GUI 隐藏这些选项；历史采集快照保持原值。

| 能力 | HF2 | 本机 MFLI（已开启选件） | 公共接口 |
| --- | --- | --- | --- |
| 主电压输入 | 2 路 | 1 路；电流输入不能作为第二路主电压输入 | `configure_signal_input()`、`set_input_range()` |
| 解调器 | 6 个 | MF-MD 提供 4 个 | `configure_demodulator()` |
| 振荡器 | 多频接口最多 6 个 | MF-MD 提供 4 个 | `configure_oscillator()`、`set_reference_frequency()` |
| 工作频率 | 最高 50 MHz | MF-F5M 最高 5 MHz | 启动预检及底层频率校验 |
| 相移 / 滤波 / 数据速率 | 支持 | 支持；实际速率须回读 | `set_demod_phase/rate/time_constant/order/harmonic()` |
| 解调 X/Y/R 时序 | 支持 | 支持 | `daq.acquire_data()` |
| 主输出 | 2 路；底层幅度/偏置为量程比例 | 1 路；底层幅度/偏置为 V | `set_output_amplitude/offset/enable()` |
| Aux Output | 4 路；无相同硬件上下限节点 | 4 路；具有上下限、Preoffset、Tip Protect | `configure_aux_output()`、`set_aux_output_scale/offset()` |
| 内置 Scope | 单通道记录，固定 2048 点，最高 210 MSa/s | MF-DIG 可同时记录两种来源，共享 5 M 点内存，最高 60 MSa/s | `acquire_scope()` |

统一幅度单位为 **Vpeak**，偏置为 **V**，相移为 **deg**，频率为 **Hz**。主输出电压按当前负载和差分模式解释；GUI 显示这些模式，写入时保持当前量程。
Aux 的路由分别设置 `demodselect` 和 `outputselect`，X/Y/R/Theta 对应 0/1/2/3，不能把解调器索引乘以 4 后合并成 outputselect。

`DemodulatorConfig.input_channel` 只表示主电压输入索引。MFLI 的 `input_channel=1` 会明确拒绝，不会默默改为电流输入。
需要其他来源时显式设置 `input_source`：HF2 支持 `voltage0/voltage1/aux_input0/aux_input1`；MFLI 支持 `voltage0/current0/aux_input0/aux_input1/aux_output0..3`，其中 `aux_output0..3` 直接读取 AuxOut1–4 的内部值。两型号的输入编码分别由驱动解析。这些来源的单位、噪声、带宽和接线不同，不能自动互换。

`set_demod_frequency()` 按当前或同时提交的新 harmonic 换算振荡器频率。振荡器是共享资源，GUI 显示共用的解调器；改频可能影响这些解调器及关联主输出。启用了 ExtRef/PLL/MOD 的设备拒绝普通改频，须按对应功能另行设置。**外部 10 MHz 时钟 EXT 与外部解调参考 ExtRef 是不同设置。**

## 解调时序的模块选择

设备库每台锁相保存全局 `acquisition_mode: auto | daq | scope`，旧配置缺字段时按 `auto`。
GUI「仪器控制 → 设备库」的锁相卡片提供“自动／DAQ／Scope”选择，沿用整批保存、修订校验及硬件互斥。
模式在 `create_lockin()` 创建会话时固定；保存新模式只影响新会话，运行中的会话使用启动快照。
各实验继续使用原采样率参数，不新增实验级模式参数。

| 设备模式 | HF2 解调时序 | MFLI 解调时序 |
| --- | --- | --- |
| 自动，请求 ≤70 kSa/s | DAQ | DAQ |
| 自动，请求 >70 kSa/s | DAQ | Scope |
| 指定 DAQ | DAQ | DAQ |
| 指定 Scope | 能力检查后拒绝：不支持解调来源 | Scope，检查 MF-DIG 和采集要求 |

边界依据当次请求值，不能根据设备取整后的通信率重新选模块。70 kSa/s 是系统选择阈值，
不是对所有连接、信号组合都成立的通信带宽保证。指定 DAQ 时仍可能因传输负载丢样而报错。
Scope 选绝对距离最近的 `60 MHz / 2^n` 档位（n=0–15），等距取高档；
例如请求 200 kSa/s，选 234.375 kSa/s。记录长度按请求时长和该档位取整，实际时长由点数和返回 `dt` 计算。

三个速率分别记录：`requested_rate` 是模块选择的请求值，`demod_stream_rate` 是 `/demods/n/rate`
通信回读值，`actual_rate` 是最终波形时间轴对应的速率。`configure_demodulator()` 返回通信率；
`result_rate(results)` 返回最终率，FFT、Welch 和频率轴均使用后者。

解调 Scope 首版只支持同一个解调器、无触发/延迟、单行网格、无额外订阅节点、单段定长采集。
支持 `sample.x/y/r/theta`；三个以上分量同步采 X/Y，派生 `R=hypot(X,Y)` 和 `theta=atan2(Y,X)`。
相位请求统一从 X/Y 计算并以 rad 返回，记录 `derived_signals`；电压分量为 V，MFLI 电流输入分量为 A。
直接低层 Scope 的原生 theta 单位标为 `native_phase`，实验等价接口通过上述派生保证相位单位。
解调来源需要 MF-DIG，MF-MD 决定可用解调器数；两通道共享 5 M 点内存。
无等价实现的触发、多行、节点组合或超内存记录在离线预检报错，连接后再按真实选件校验，均在启动输出前完成。
不回退模块、不缩短记录、不自动更换接线；原有型号、输入、频率及输出安全限制仍生效。

Scope 准备和采集阶段暂停所有解调器向主机的数据流，保持滤波与内部 Aux 信号链。
Scope 请求采样率只用于模块选择及 Scope 档位配置，不写入 `/demods/n/rate`；准备函数返回保留的主机传输率。
DAQ 路径仍将当次请求率写入解调器并回读，因此 HF2 正式 DAQ 采集不沿用低速校相传输率。
结束、准备失败、读取失败、超时、取消及断开前恢复原 rate/enable；嵌套采集阶段到外层结束才恢复。
Scope 沿用 LabOne 当前 `limitlower/limitupper/bwlimit`，记录这些值，检测削顶后报错；不试采、不自动调量程。
范围过宽对小噪声的量化精度、COM 状态及噪声底影响仍需实机比较。相关能力见
[MFLI Scope 官方说明](https://docs.zhinst.com/mfli_user_manual/functional_description/scope.html)。

各实验阶段的请求率、时长、解调器和信号在 `lab_workflows/lockin_acquisition.py` 声明；
GUI 预检及运行日志显示模块和预计／实际率。低层 `DAQCollector` 仍是专用 DAQ 接口，
色散与 Demod3 R 的正式采集路径通过公共选择器分派。

## Python 使用示例

以下为结构示例，参数是教学示例；正式实验默认值以其 YAML 或实际脚本参数区为准：

```python
from lab_workflows.common import load_mapping
from lab_workflows.devices import create_lockin
from lockin_amplifier import DemodulatorConfig, DAQConfig, ScopeConfig, demod, daq, acquire_scope, result_rate

instr = create_lockin(load_mapping()["lockin_r"])
instr.connect()
try:
    demod.set_reference_frequency(instr, freq=100_000, osc_idx=0)
    requested_rate = 10_000
    stream_rate = demod.configure_demodulator(instr, DemodulatorConfig(
        demod_index=0, input_channel=0, rate=10_000, time_constant=0.001, order=4,
    ), for_acquisition=True)
    demod.set_demod_phase(instr, demod_idx=0, phase_deg=45)
    samples = daq.acquire_data(instr, DAQConfig(
        duration=0.1, grid_cols=round(stream_rate * 0.1), signal_paths=["sample.x", "sample.y", "sample.r"],
    ), demod_idx=0, actual_rate=stream_rate, requested_rate=requested_rate, strict_cleanup=True)
    capture_rate = result_rate(samples)
    waves = acquire_scope(instr, ScopeConfig(sources=["voltage0"], length=2048))
finally:
    instr.disconnect()
```

正式工作流连接后使用 `synchronize_connected_clocks()` 设置并严格回读时钟；断开通信不能代替关闭输出。
输出设置应通过 `lab_workflows.lockin_control.apply_settings()` 复用安全检查。底层 setter 只负责节点写入和物理单位转换，不能替代实验安全限值校验。

## DAQ 记录与时间轴

`DAQConfig.device` 可留空以使用当前连接设备；指定了其他设备 ID 会拒绝采集。
返回每路一个 `DAQResult`，包含 `signal_name`、`demod_index`、`values`、`time`、`config_snapshot` 和 `record_slices`。
所有 burst 与 rows 都会保留；`record_slices` 记录各独立记录在展平数组中的边界，分析时不能越过记录间隙计算连续 FFT。

优先使用模块 timestamp 与设备 clockbase 建立共同时间原点，先做整数差以保留大时间戳精度。
缺少 timestamp 时用实际采样率（Exact）或网格间距建立时间轴，元数据明确标记 `synthetic_rate` 或 `synthetic_grid`。
请求速率、解调通信率与网格速率分别保存到采集快照。DAQ 的最终波形率从返回时间间隔计算，缺时间戳时使用网格或 Exact 的通信率；Scope 的最终率为记录 `1/dt`。

现有 NPZ 和信号键、目录保留，新增 `acquisition_module`、`requested_rate_Sa_s`、
`demod_stream_rate_Sa_s`、`actual_rate_Sa_s`、`actual_duration_s`、`time_source` 和完整逐信号
`acquisition_metadata_json`。`.npy` 波形旁的 `<原文件名>_acquisition.npz` 保存相同元数据和 `time_s`。
Scope 每次是一条独立记录，不跨记录拼成连续时间轴；扫描摘要引用原逐点文件。
新分析优先用逐段元数据，旧数据无新字段/旁文件时继续用原运行快照中的实际率。

连续模式使用 `trigger_type=0`。边沿触发使用 `trigger_type=1`，`trigger_channel=0/1` 对应 Aux Input 1/2，节点分别为 `/dev.../auxins/0/sample.AuxIn0` 与 `/dev.../auxins/0/sample.AuxIn1`；触发延迟可为负值。
`cancellation_check` 在等待轮询时执行，取消仍清理模块；`strict_cleanup=True` 会使独立清理失败成为失败，保留原有采集/取消异常并记录清理告警。

## 内置 Scope

`ScopeConfig` 指定 `sources`、`sample_rate_hz`、`length`、`records`。HF2 来源为 `voltage0/voltage1`，单次只能记录一路；MFLI 支持 `voltage0/aux_input0/aux_input1`，MF-DIG 还提供 `demod0..3_x/y/r/theta` 来源并可同时记录两路。解调实验经统一接口请求分量，适用前述更严格的单段约束。
采样率为设备最高 Scope 速率除以 `2**n`，n=0–15。HF2 length 必须为 2048；MFLI 按来源数量检查共享内存。

Core Scope Module 完成组包和缩放，返回值直接作为来源对应的物理量，不再重复乘 channel scaling；具体单位记录在采集快照中。HF2 额外用当前输入量程设置 `externalscaling`。
每条记录、每个来源单独返回一个 `ScopeResult`；以记录的 `dt` 建立时间轴，并按官方 Scope 示例使用末端/触发时间戳进行对齐。缺少触发时间戳时从记录起点计时并标明来源。
丢样、丢触发、传输失败、空记录和异常尺寸会报错；正常、异常和取消均清理模块并尽力恢复原 Scope 设置。
本入口采集普通独立波形记录，尚未实现原始连续 stream 或硬件 segmented recording。

## GUI 设置、采集与安全范围

「设备控制」读取所选锁相设备后，可设置解调相移、频率、谐波、速率、时间常数和阶数；也提供 Signal Output 与 Aux Output 的回读和设置。该页解调时序的请求率由本次网格点数除以采集时长确定，独立于解调通信回读率；Scope 使用最近档位及对应点数，数据文件与运行快照记录最终速率。
X/Y/R 与内置 Scope 采集沿用全局任务队列，支持取消，预览每条最多 2000 点，完整数据保存在：

```text
data/LockinAcquisition/<时间戳>_<型号标签>/
  experiment_config.yaml
  raw/acquisition.npz
```

NPZ 包含 `values_N`、`time_N`、`record_slices_N`；配置记录实际型号、选件、时间轴来源和每条记录元数据。不同硬件波形不会伪装成相同采样率。

[安全限值](../params/safety_limits.yaml) 的键使用设备库逻辑 ID，例如 `hf2_dev18246_signal_output_0`、`mfli_dev30661_signal_output_0`，以及 `{设备库ID}_aux_output_0` 至 `_3`。
**本任务尚未获得这些输出的安全电压上下限，未填写数值；GUI 输出写入保持禁用，关闭主输出允许执行。**

配置限值后，主输出检查所有启用混频分量和偏置的总电压，并拒绝未经核对的外部信号叠加；写入失败会尽力关闭受影响通道。
MFLI Aux 写入时设置硬件上下限、清除 Preoffset，并拒绝可能覆盖限幅的 Tip Protect。HF2 Aux 用主输入量程保守估算电压边界，不能提供 MFLI 的同等硬件限幅保证。

## 不等价实验与实机验收

仍要求第二路主电压输入的实验仅允许 HF2。DirectAW 系列（含 R 与已知噪声版本）已显式支持可选辅助通道：HF2 采用物理线路，MFLI 采用辅助输入物理线路或 AuxOut 内部线路，并在启动输出前检查真实 MF-MD 选件。两种线路的带宽、增益和相位响应仍待实机确认，`aux_input_feedback_verified` 不标记为已验证。其他双主电压实验继续拒绝 MFLI，不自动改变接线、回退设备或降低频率。受影响清单见 [旧模式锁相实验与兼容性](legacy_lockin_experiments.md)。

离线预检检查常见锁相频率参数，以及 Bell Bloom Z、Mx 主场、Mx Z 和实际电流耦合标定的完整预测扫频窗口；底层 setter 继续校验每次实际改频。新增其他派生扫频流程时需补充对应窗口检查。没有锁相依赖的实验不会按 MFLI 频率上限限制其他仪器。

自动化验证使用假仪器。首次实机使用时核对 LabOne 服务、设备型号/选件、外部 10 MHz 接线与锁定、输入量程，以及实际采样率、Scope 电压和记录长度，再比较两机的标准输入测量。

## 官方手册

- [Python Core API 参考](https://docs.zhinst.com/labone_api_user_manual/reference/python/index.html)
- [HF2 节点参考](https://docs.zhinst.com/hf2_user_manual/nodedoc.html)
- [MFLI 节点参考](https://docs.zhinst.com/mfli_user_manual/nodedoc.html)
- [Scope Module 与 HF2 缩放](https://docs.zhinst.com/labone_api_user_manual/modules/scope/index.html)
- [官方 Scope 时间轴示例](https://docs.zhinst.com/zhinst-toolkit/en/latest/examples/scope_module.html)

模块实现位于 `src/lockin_amplifier/`，型号入口为 `hf2.py` / `mfli.py`，公共通信为 `base.py`，配置、解调、DAQ、Aux 和 Scope 使用共享模块；[HF2 / MFLI Notebook 示例](../examples/lockin_amplifier_demo.ipynb) 通过 `MODEL` 开关演示全部公共功能。
