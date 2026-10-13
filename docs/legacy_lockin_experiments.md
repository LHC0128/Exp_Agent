# 旧模式锁相实验与兼容性

旧模式继续读取实际脚本常量及 GUI 保存的参数，不新增另一套默认值。锁相连接已改用 `create_lockin()`，所有实验使用设备库卡片的全系统选择。采集前的能力检查和原有输出收尾均保留。

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
| `rf-sensitivity-aw` | [RF_Field_Sensitivity_AW.py](../experiments/RF_Field_Sensitivity_AW.py) |
| `rf-sensitivity-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep.py](../experiments/RF_Field_Sensitivity_AW_FreqSweep.py) |
| `rf-sensitivity-constxy-frequency` | [RF_Field_Sensitivity_ConstXY_FreqSweep.py](../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py) |
| `rf-sensitivity-direct-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py](../experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py) |
| `xy-aw-voltage-calibration` | [XY_AW_Voltage_Calibration.py](../experiments/XY_AW_Voltage_Calibration.py) |
| `xy-dc-voltage-calibration` | [XY_DC_Voltage_Calibration.py](../experiments/XY_DC_Voltage_Calibration.py) |
| `xy-direct-aw-dc-calibration` | [XY_DirectAW_DC_Calibration.py](../experiments/XY_DirectAW_DC_Calibration.py) |
| `z-field-bandwidth` | [Z_Field_Bandwidth_Measurement.py](../experiments/Z_Field_Bandwidth_Measurement.py) |

## 已接入的旧模式入口

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
5. 按现有采集流程运行，记录当前设备库/映射与实际采样率。使用新版公共 DAQ 的记录还包含实际型号和回读能力；原数据目录和字段保持稳定。
6. 结束和取消继续执行原实验 `try/finally` 收尾；断开通信不能代替关闭输出。旧分析脚本仍从原始数据离线分析，选择 MFLI 不修改历史 HF2 数据。

HF2/MFLI 的同名函数、Scope 独立记录、输出安全规则和实机验收见 [锁相接口说明](lockin_amplifier.md)。没有新增连续原始 stream、硬件分段采集，也未迁移 Notebook 或未注册的三份硬编码 HF2 调试脚本。
