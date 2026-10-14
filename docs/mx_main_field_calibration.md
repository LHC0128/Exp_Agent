---
title: Mx 主磁场频率标定
type: Mx_Main_Field_Calibration
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
  X_magnetic_field:
    role: fixed_off
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - 扫描范围应不跨越总场零点，使用普通线性模型。
  - 设置主场电流后不单独等待；首个频点的温控关闭统一等待同时覆盖主场稳定。
  - 频率到磁场使用可配置的 GYROMAGNETIC_RATIO_HZ_PER_NT，取值见参数文件。
experiment_id: mx-main-field-calibration
defaults_file: params/experiments/mx-main-field-calibration.yaml
---

# Mx 主磁场频率标定

## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-main-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标和构型

本实验在 Mx 构型下标定 GS200 `main_magnetic_field` 电流与 Y RF 共振中心频率、主磁场大小之间的关系：

\[
f(I)=K_f I+f_0,
\qquad
B(I)=\frac{f(I)}{\gamma}=K_B I+B_0.
\]

主场与 Pump 光沿 Z，Probe 光沿 X，`rf_coil` 沿 Y。DG4000 `Z_magnetic_field` 在整个实验中固定为 0 V 且 Output OFF，避免辅助 Z 场混入 GS200 标定。

## 扫描和采集

GS200 电流轴由参数文件中的起点、终点、点数与扫描方向生成。

每个电流点的局部扫频预测中心为：

\[
f_\mathrm{pred}(I)=f_\mathrm{ref}+K_\mathrm{initial}(I-I_\mathrm{ref}).
\]

局部频率窗口与步进分别读取参数文件的 `FREQUENCY_HALF_WIDTH_HZ` 和 `FREQUENCY_STEP_HZ`。`MAIN_FIELD_INITIAL_HZ_PER_MA` 只用于定位局部扫频窗口，不作为最终标定结果。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率和配置的幅度，并同步 锁相放大器 振荡器。
2. 将温控开关设置为 0 V DC、Output ON。
3. 只等待一次 `SETTLE_TIME_S`，同时覆盖温控关闭、RF 频点稳定以及每个电流点首频点的主场稳定。
4. 按 `FREQUENCY_DURATION_S` 采集 Demod0 R。
5. 在 `finally` 中恢复温控为 5 V DC、Output ON，并按 `TEMP_SWITCH_ON_LAG_S` 等待。

若单次记录超过配置的标准差门槛，完整保存该尝试并按 `R_POINT_MAX_ATTEMPTS` 重采。采集阶段不执行共振拟合。

## 离线分析

分析器从 `raw/main_field_scan_index.npz` 和各 `raw/current_*/frequency_scan.npz` 重建频率响应。每条曲线拟合带基线 Lorentzian，再对有效中心执行标准不确定度加权线性拟合。单曲线中心需位于扫描窗口内且距边缘不少于一个 HWHM，最终线性 R² 需满足配置的质量阈值。

频率换算磁场时使用 `GYROMAGNETIC_RATIO_HZ_PER_NT`；其值读取参数配置，并作为配置常数处理，不额外传播其系统不确定度。

结果目录为：

```text
data/Mx_Main_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    main_field_scan_index.npz
    current_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    main_field_frequency_calibration.png
    main_field_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Main_Field_Calibration.py` 和 `experiments/Mx_Main_Field_Calibration_plot.py`。

## 安全结束状态

运行前记录 GS200 的源模式、电流设定、输出开关、量程和限流。正常、取消或异常结束后均恢复该状态；Z 辅助场、X 场与 Y RF 归零关闭，温控恢复为 5 V DC、Output ON。Pump/Probe 光功率、Pump 载波/门控和 锁相放大器 配置按标准策略保留，正常结束只断开 TEC。
