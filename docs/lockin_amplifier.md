# HF2 / MFLI 锁相放大器控制模块 (`lockin_amplifier`)

两种设备均使用 LabOne 的 `zhinst.core` Python Core API。LabOne 是服务器、用户界面和编程接口的整体软件平台；Python Core API 是其中的 Python 通信接口。

## 源码结构与兼容入口

型号实现分别位于 `src/lockin_amplifier/hf2.py` 和 `mfli.py`，负责连接默认值、硬件与选件能力、输入来源、clockbase 节点类型、输出电压换算和 Scope 设置。公共通信位于 `base.py`，解调配置、DAQ、Aux 和 Scope 采集继续复用公共模块。

包级导入和 `lockin_amplifier.instrument` 旧导入入口保持有效，`LockinInstrument` 的原默认 HF2 行为保留。实验参数、能力字典和采集结果格式沿用现有接口。阅读顺序、创建示例和无硬件验证命令见 [锁相放大器源码阅读指南](../src/lockin_amplifier/README.md)。

## 设备选择与连接

在 GUI「仪器控制 → 设备库」的单张**锁相放大器**卡片内，选择全系统使用的 HF2 或 MFLI。
选择先更新全部锁相映射草稿，并保留每个 mapping 的解调器索引，点击“保存设备库”后才生效。
已有设备配置草稿时可继续选择；运行中或排队中的硬件任务允许编辑草稿，结束后才能保存。
设备库与映射两份修订号一起校验，保存冲突或失败保留草稿。其他浏览器的过期请求须刷新后再操作。

当前设备定义来自 [devices.yaml](../params/devices.yaml)：HF2 为 `dev18246`，MFLI 为 `dev30661`，服务器地址 `192.168.124.109:8004`，接口 `PCIe`，目标时钟 `EXT`。HF2 使用 API Level 1，MFLI 使用 API Level 6。服务器需可访问；服务器连通但设备连接失败时，驱动仍会报错。
`host:port` 是电脑访问数据服务器的地址，`interface` 是数据服务器访问仪器测量单元的接口。当前使用 MFLI 内置服务器，它向 Python API 报告内部 `PCIe`，电脑到仪器仍通过网线连接；独立 PC 上的服务器可报告 `1GbE` 或 `USB`。接口以服务器 `/zi/devices` 中的 `INTERFACE` 为准，扫描时不再按型号推断，未报告接口时返回明确错误。此区别见 [MFLI 官方连接说明](https://docs.zhinst.com/mfli_user_manual_cn/getting_started/labone_software_startup.html#available-devices)。
MFLI 的离线声明包含 MF-F5M、MF-MD、MF-DIG、MF-PID；连接后实际选件以设备回读为准。

正式 Python 实验通过 `lab_workflows.devices.create_lockin(load_mapping()[key])` 选择驱动。历史变量 `hf2`、`hfi`、`HF2_*` 参数和 `hf2_configuration` 数据键继续保留，代表当前映射的锁相设备。Notebook 和未注册的 `quick_test_scan.py`、`optimize_scan.py`、`optimize_scan_round2.py` 仍为原有 HF2 入口。

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
需要其他来源时显式设置 `input_source`：MFLI 支持 `voltage0/current0/aux_input0/aux_input1`。这些来源的单位、噪声、带宽和接线不同，不能自动互换。

`set_demod_frequency()` 按当前或同时提交的新 harmonic 换算振荡器频率。振荡器是共享资源，GUI 显示共用的解调器；改频可能影响这些解调器及关联主输出。启用了 ExtRef/PLL/MOD 的设备拒绝普通改频，须按对应功能另行设置。**外部 10 MHz 时钟 EXT 与外部解调参考 ExtRef 是不同设置。**

## Python 使用

以下为结构示例，参数是教学示例；正式实验默认值以其 YAML 或实际脚本参数区为准：

```python
from lab_workflows.common import load_mapping
from lab_workflows.devices import create_lockin
from lockin_amplifier import DemodulatorConfig, DAQConfig, ScopeConfig, demod, daq, acquire_scope

instr = create_lockin(load_mapping()["lockin_r"])
instr.connect()
try:
    demod.set_reference_frequency(instr, freq=100_000, osc_idx=0)
    actual_rate = demod.configure_demodulator(instr, DemodulatorConfig(
        demod_index=0, input_channel=0, rate=10_000, time_constant=0.001, order=4,
    ))
    demod.set_demod_phase(instr, demod_idx=0, phase_deg=45)
    samples = daq.acquire_data(instr, DAQConfig(
        duration=0.1, signal_paths=["sample.x", "sample.y", "sample.r"],
    ), demod_idx=0, actual_rate=actual_rate, strict_cleanup=True)
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
请求速率与网格点数不等于硬件采样率；实际速率和实际型号保存到采集快照。

连续模式使用 `trigger_type=0`。边沿触发使用 `trigger_type=1`，`trigger_channel=0/1` 对应 Aux Input 1/2，节点分别为 `/dev.../auxins/0/sample.AuxIn0` 与 `/dev.../auxins/0/sample.AuxIn1`；触发延迟可为负值。
`cancellation_check` 在等待轮询时执行，取消仍清理模块；`strict_cleanup=True` 会使独立清理失败成为失败，保留原有采集/取消异常并记录清理告警。

## 内置 Scope

`ScopeConfig` 指定 `sources`、`sample_rate_hz`、`length`、`records`。HF2 来源为 `voltage0/voltage1`，单次只能记录一路；MFLI 来源为 `voltage0/aux_input0/aux_input1`，MF-DIG 可同时选择两种来源。
采样率为设备最高 Scope 速率除以 `2**n`，n=0–15。HF2 length 必须为 2048；MFLI 按来源数量检查共享内存。

Core Scope Module 完成组包和缩放，返回值直接作为电压，不再重复乘 channel scaling。HF2 额外用当前输入量程设置 `externalscaling`。
每条记录、每个来源单独返回一个 `ScopeResult`；以记录的 `dt` 建立时间轴，并按官方 Scope 示例使用末端/触发时间戳进行对齐。缺少触发时间戳时从记录起点计时并标明来源。
丢样、丢触发、传输失败、空记录和异常尺寸会报错；正常、异常和取消均清理模块并尽力恢复原 Scope 设置。
本入口采集普通独立波形记录，尚未实现原始连续 stream 或硬件 segmented recording。

## GUI 设置、采集与安全范围

「设备控制」读取所选锁相设备后，可设置解调相移、频率、谐波、速率、时间常数和阶数；也提供 Signal Output 与 Aux Output 的回读和设置。
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

需要 Demod0 → Aux Output → 第二路主电压输入 → Demod3 的实验目前只允许 HF2。
MFLI 的 Aux Input 回采链路尚未验证，不自动改变接线、回退设备或降低频率。受影响实验清单见 [旧模式锁相实验与兼容性](legacy_lockin_experiments.md)。

离线预检检查常见锁相频率参数，以及 Bell Bloom Z、Mx 主场、Mx Z 和实际电流耦合标定的完整预测扫频窗口；底层 setter 继续校验每次实际改频。新增其他派生扫频流程时需补充对应窗口检查。没有锁相依赖的实验不会按 MFLI 频率上限限制其他仪器。

自动化验证使用假仪器。首次实机使用时核对 LabOne 服务、设备型号/选件、外部 10 MHz 接线与锁定、输入量程，以及实际采样率、Scope 电压和记录长度，再比较两机的标准输入测量。

## 官方手册

- [Python Core API 参考](https://docs.zhinst.com/labone_api_user_manual/reference/python/index.html)
- [HF2 节点参考](https://docs.zhinst.com/hf2_user_manual/nodedoc.html)
- [MFLI 节点参考](https://docs.zhinst.com/mfli_user_manual/nodedoc.html)
- [Scope Module 与 HF2 缩放](https://docs.zhinst.com/labone_api_user_manual/modules/scope/index.html)
- [官方 Scope 时间轴示例](https://docs.zhinst.com/zhinst-toolkit/en/latest/examples/scope_module.html)

模块实现位于 `src/lockin_amplifier/`，型号入口为 `hf2.py` / `mfli.py`，公共通信为 `base.py`，配置、解调、DAQ、Aux 和 Scope 使用共享模块；[HF2 / MFLI Notebook 示例](../examples/lockin_amplifier_demo.ipynb) 通过 `MODEL` 开关演示全部公共功能。
