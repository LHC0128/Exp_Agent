# 旧模式锁相实验与兼容性

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


旧模式继续读取实际脚本常量及 GUI 保存的参数，不新增另一套默认值。锁相连接已改用 `create_lockin()`，所有实验使用设备库卡片的全系统选择。采集前的能力检查和原有输出收尾均保留。

## 解调时序的采集模块

所有正式解调时序复用设备库 `acquisition_mode`。自动模式下 HF2 使用 DAQ；MFLI 按当次请求率
≤70 kSa/s 使用 DAQ、>70 kSa/s 使用 Scope，显式模式优先。Scope 使用最近硬件档位并保存真实时间轴，
要求 MF-DIG、单解调器、无触发、单段及足够内存；不支持的要求在启动输出前拒绝，不更换接线或回退模块。
原采样率键、文件名和信号键继续保留，逐条 NPZ 或 `.npy` 的 `_acquisition.npz` 旁文件保存模块、
请求率、解调通信率、最终率、时长和时间轴来源。FFT/Welch 使用最终率，旧数据按原运行快照解释。
量程、停流恢复及选件规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

旧入口的时序阶段分别声明为：XY v2 的 `HF2_DAQ_RATE/HF2_DAQ_DURATION`、光子噪声的
`HF2_DEMOD_RATE/ACQ_DURATION`、RF/XY 扫描的 `DEMOD0_RATE` 或 `HF2_DEMOD0_RATE` 与固定单点时长、
Z 带宽的 `HF2_DEMOD0_RATE/POINT_DURATION`、静磁优化的 `HF2_DEMOD_RATE/DAQ_DURATION` 触发色散
和 `HF2_NOISE_RATE/NOISE_DURATION` 噪声。静磁优化的色散触发和额外订阅只支持 DAQ，指定 Scope 时拒绝启动。
已有 `ACQUISITION_MODE=demod_rxy` 的单次样本分支继续读单次样本，不应用时序模块切换。

## 双主电压输入限制

正式旧入口连接锁相后同样调用 `initialize_lockin_inputs()`，关闭全部主电压输入的 AC 耦合和
50 Ω 终端，后续输入配置保持 DC 耦合和高阻。Input2 旧耦合/阻抗参数仍可读取，但归一到关闭值，
GUI 隐藏开启选项；光子散粒噪声和可选 BB 监控也使用公共初始化。

以下正式实验需要第二路主电压输入进行回采，目前 MFLI 禁止启动；保持原 HF2 物理接线，或另行完成 Aux Input 链路验证后再迁移。不会自动替换电流输入、调整频率或回退设备。

| 实验 ID | 采集入口 |
| --- | --- |
| `aw-const-omega-awscale-check` | [AW_ConstOmega_Calibration_Check_AWScale.py](../experiments/AW_ConstOmega_Calibration_Check_AWScale.py) |
| `aw-const-omega-check` | [AW_ConstOmega_Calibration_Check.py](../experiments/AW_ConstOmega_Calibration_Check.py) |
| `noise-spectrum-xy-demod3-r` | [Noise_Spectrum_XY_Demod3_R.py](../experiments/Noise_Spectrum_XY_Demod3_R.py) |
| `rf-sensitivity-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep.py](../experiments/RF_Field_Sensitivity_AW_FreqSweep.py) |
| `rf-sensitivity-constxy-frequency` | [RF_Field_Sensitivity_ConstXY_FreqSweep.py](../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py) |
| `rf-sensitivity-direct-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py](../experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py) |
| `xy-aw-voltage-calibration` | [XY_AW_Voltage_Calibration.py](../experiments/XY_AW_Voltage_Calibration.py) |
| `xy-dc-voltage-calibration` | [XY_DC_Voltage_Calibration.py](../experiments/XY_DC_Voltage_Calibration.py) |
| `xy-direct-aw-dc-calibration` | [XY_DirectAW_DC_Calibration.py](../experiments/XY_DirectAW_DC_Calibration.py) |
| `z-field-bandwidth` | [Z_Field_Bandwidth_Measurement.py](../experiments/Z_Field_Bandwidth_Measurement.py) |

## 已接入的旧模式入口

`rf-sensitivity-aw`、`rf-sensitivity-aw-r` 与 `rf-sensitivity-aw-known-noise` 使用强类型工作流，已支持明确选择辅助输入物理线路或 MFLI 内部 AuxOut 线路，不属于上述限制清单。旧参数缺少线路键时仍解释为原 HF2 AuxOut2 → Signal Input2，不能直接在 MFLI 启动；实机增益与带宽仍需验证。详见 [DirectAW](rf_field_measurement.md) 与 [DirectAW R](rf_field_measurement_r.md)。

以下清单按注册表和当前构造代码核对；简介与运行入口由原实验定义提供，分析器保持离线。

| 实验 ID | 目的 | 设备适用性 |
| --- | --- | --- |
| [`aw-const-omega-awscale-check`](../experiments/AW_ConstOmega_Calibration_Check_AWScale.py) | 使用 AWScale 策略检查恒定 Omega 标定。 | HF2 双主输入，MFLI 禁止启动 |
| [`aw-const-omega-check`](../experiments/AW_ConstOmega_Calibration_Check.py) | 检查恒定 Omega 任意波标定结果。 | HF2 双主输入，MFLI 禁止启动 |
| [`bb-duty-scan-burst`](../experiments/BB_Rpump_DutyScan_Burst.py) | Burst 模式下扫描 Pump 占空比。 | HF2 / MFLI，检查频率与通道 |
| [`bb-power-scan-burst`](../experiments/BB_Rpump_PowerScan_Burst.py) | Burst 模式下扫描 Pump 功率。 | HF2 / MFLI，检查频率与通道 |
| [`noise-spectrum-xy-v2`](../experiments/Noise_Spectrum_XY_Ctrl_v2.py) | 第二版 XY 控制噪声谱流程。 | HF2 / MFLI，检查频率与通道 |
| [`photon-shot-noise`](../experiments/Photon_shot_noise.py) | 测量光子散粒噪声随实验参数的变化。 | HF2 / MFLI，检查频率与通道 |
| [`rf-sensitivity-aw-frequency`](../experiments/RF_Field_Sensitivity_AW_FreqSweep.py) | 扫描 RF 频率的外部 AM 硬件方案。 | HF2 双主输入，MFLI 禁止启动 |
| [`rf-sensitivity-constxy-frequency`](../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py) | 固定 XY 控制下扫描 RF 频率。 | HF2 双主输入，MFLI 禁止启动 |
| [`static-sensitivity-optimize`](../experiments/Static_Magnetic_Field_Sensitivity_Optimize.py) | 全局搜索静磁场灵敏度实验参数。 | HF2 / MFLI，检查频率与通道 |
| [`static-sensitivity-optimize-local`](../experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py) | 在已有结果附近进行局部参数优化。 | HF2 / MFLI，检查频率与通道 |
| [`xy-aw-voltage-calibration`](../experiments/XY_AW_Voltage_Calibration.py) | 标定 XY 任意波控制电压。 | HF2 双主输入，MFLI 禁止启动 |
| [`xy-dc-voltage-calibration`](../experiments/XY_DC_Voltage_Calibration.py) | 标定 XY 直流控制电压。 | HF2 双主输入，MFLI 禁止启动 |
| [`z-field-bandwidth`](../experiments/Z_Field_Bandwidth_Measurement.py) | 测量 Z 磁场控制链路带宽。 | HF2 双主输入，MFLI 禁止启动 |

## 准备、执行和历史数据

1. 在设备库卡片选择锁相设备；如设备库有草稿先保存。检查对应物理量映射和解调器索引。
2. 根据当前脚本参数区或 GUI 的参数来源核对运行参数，默认值不在本文复制。连接前拒绝已知不兼容接线、越界解调器和锁相频率。
3. 按实验接线说明接探测信号、Aux 回环及 DAQ 触发。双输入表中的实验保持 HF2 回环；其他入口的单主输入信号接所选设备的主输入。
4. 旧脚本运行前执行「功能模块 → 参考时钟同步」；已迁移的新模式在连接后自动设置并严格回读。EXT 选择回读不能替代实机锁定检查。
5. 按现有采集流程运行，记录当前设备库/映射、请求率、实际模块和最终波形率。使用新版公共采集入口的记录包含实际型号和回读能力；原数据目录和字段保持稳定。
6. 结束和取消继续执行原实验 `try/finally` 收尾；断开通信不能代替关闭输出。旧分析脚本仍从原始数据离线分析，选择 MFLI 不修改历史 HF2 数据。

未注册的 `quick_test_scan.py`、`optimize_scan.py` 和 `optimize_scan_round2.py` 也从设备库创建锁相会话，显式声明触发色散与无触发噪声，并在连接前检查采集方式。其触发色散仍只支持 DAQ，显式 Scope 会拒绝启动；噪声波形及 PSD 汇总保存实际速率和模块快照。它们继续作为直接采集脚本使用，不纳入 GUI 正式实验清单，也不能用作无硬件测试。

HF2/MFLI 的同名函数、Scope 独立记录、输出安全规则和实机验收见 [锁相接口说明](lockin_amplifier.md)。没有新增连续原始 stream、硬件分段采集，也未迁移 Notebook。
