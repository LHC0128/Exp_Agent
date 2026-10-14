---
title: Mx Z 实际电流-耦合强度标定
type: Mx_Z_Current_Coupling_Calibration
scan_mode: nested_scan
mapping_keys:
  Z_magnetic_field:
    role: dc_scan
  rf_coil:
    role: resonance_scan
  scope_waveform:
    role: CH3_low_side_sense
  lockin_r:
    role: resonance_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2 / MFLI（按全系统选择）
  - SDS
learned_notes:
  - 电流标定绑定主场、温度、光功率、采样电阻和示波器配置。
  - BIDIRECTIONAL_SCAN 决定是否在正向后执行反向扫描，用于报告斜率和零电流中心磁滞。
experiment_id: mx-z-current-coupling-calibration
defaults_file: params/experiments/mx-z-current-coupling-calibration.yaml
---

# Mx Z 实际电流-耦合强度标定

## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-current-coupling-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `mx-z-current-coupling-calibration`。每个 Z DC 工作点稳定后，SDS CH3 记录采样电阻电压，再执行 Y RF 共振频扫。离线分析拟合共振中心与实际电流和采样电阻电压的关系，保存 `K_Z_Hz_per_A`、`K_Z_Hz_per_Vsense`、零电流中心、线性度、重复性和正反向磁滞。

该实验不会覆盖历史 `mx-z-field-calibration` 的 `K_Z_Hz_per_V`。入口为 `experiments/Mx_Z_Current_Coupling_Calibration.py` 和 `experiments/Mx_Z_Current_Coupling_Calibration_plot.py`。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

在 GUI「仪器控制 → 设备库」的单张“锁相放大器”卡片中全系统选择 HF2 或 MFLI；探测信号接所选设备的主电压输入，SDS CH3 仍用于采样电阻电压。共振扫频沿用 Mx Z 标定的预测中心与扫频窗口，预检检查完整窗口、解调器和振荡器索引是否在设备能力范围内。MFLI 最高 5 MHz，不会自动降低频率或回退 HF2。连接后按设备库设置并回读参考时钟，原始锁相数据保留实际型号与速率。统一接口和验收见 [锁相模块说明](lockin_amplifier.md)。

## 离线分析与结果检查

重新分析指定运行目录：

```powershell
agent_exp_env\Scripts\python.exe experiments\Mx_Z_Current_Coupling_Calibration_plot.py "data\Mx_Z_Current_Coupling_Calibration\0914_172809_mx_z_current_coupling_calibration"
```

GUI 的自动分析和重新分析会通过 `python -m` 执行共享分析模块；模块必须保留
`if __name__ == "__main__"` 入口，确保真正调用 `main()`。

结果写入本次运行的 `results/`：

- `analysis.json` / `analysis.yaml`：标定是否有效、有效频扫数量、各组拟合参数及排除原因。
- `calibration_results.npz`：实际电流、共振中心、接纳掩码和标定系数。
- `current_coupling_calibration.png`：电流与共振中心关系，标出被排除的拟合中心。
- `frequency_response_fits.png`：每组原始频扫与洛伦兹拟合，标注接纳状态。

“分析完成”表示分析程序执行完毕；标定是否有效应检查 `analysis.json` 的 `success`。
即使所有频扫都被排除，仍输出诊断图和排除原因，此时耦合系数为 NaN，不可用于后续标定。
分析保留现有线宽、扫描边界等判据，不修改原始数据或采集参数。

## SDS CH3 设置

已有 `Z_Coil_Current_Frequency_Response` 运行表明，当前接线下采样电阻电压最大约为 ±0.82 V。GUI 的通道、耦合、阻抗、探头倍率、采样率、时长与量程读取参数文件的 `SENSE_SCOPE_*` 字段。工作流在每个 Z 直流点自动居中，并按配置阈值和尝试上限调节量程；只有打开 `SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK` 才允许缩小量程。

若更换采样电阻、线圈或驱动范围，应先在示波器上确认波形没有贴边，再在 GUI 中调整 `SENSE_SCOPE_INITIAL_SCALE_V_DIV`。初始量程应覆盖预计峰值，且必须位于 `SENSE_SCOPE_SCALE_MIN_V_DIV` 与 `SENSE_SCOPE_SCALE_MAX_V_DIV` 之间；不要把初始量程重新设为 0.01 V/div。
