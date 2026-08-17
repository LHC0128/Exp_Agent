---
title: Mx Keithley 6221 最优控制 XYZ 平衡场
type: Mx_Keithley_6221_Optimal_Control_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  CONTROL_VERSION: v4
  KEITHLEY_CALIBRATION_SOURCE_RUN: 0813_183404_mx_6221_main_field_cal
  KEITHLEY_CURRENT_RANGE_MA: 100.0
  X_FIELD_START_V: -0.05
  X_FIELD_STOP_V: 0.05
  X_FIELD_POINTS: 11
  Y_FIELD_START_V: -0.05
  Y_FIELD_STOP_V: 0.05
  Y_FIELD_POINTS: 11
  Z_FIELD_START_MA: -0.02
  Z_FIELD_STOP_MA: 0.02
  Z_FIELD_POINTS: 5
  DEMOD_FREQUENCY_HZ: 12000.0
  TRIGGER_FREQUENCY_HZ: 12000.0
mapping_keys:
  keithley_6221_main_field:
    role: triggered_optimal_control_current
  main_magnetic_field:
    role: z_balance_scan
  Time_sequence_2:
    role: common_trigger_preserved_on_exit
  X_magnetic_field:
    role: x_dc_balance_scan
  Y_magnetic_field:
    role: y_dc_balance_scan
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_r_detection
required_devices:
  - Keithley 6221
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - 6221 接 Z 小磁场线圈输出最优控制，GS200 接独立 Z 主磁场线圈扫描平衡场。
  - Time_sequence_2 CH2 接 6221 Trigger Link Line 1；Line 2 不接。
  - 外触发固定 Line 1、IGNORE=OFF、单周期，触发频率等于控制波形重复频率。
  - v4 波形重复 12 kHz、理论 f_rf 12 kHz；解调锁定 f_rf，触发锁定重复频率。
  - 控制重复频率允许与 rf 频率不同，但 f_rf 必须是重复频率的正整数倍。
  - 正常、取消和异常结束均保持 6221 最优控制与共同触发继续运行。
  - 6221 仅在 Compliance 命中或查询失败时执行紧急关断。
---

# Mx Keithley 6221 最优控制 XYZ 平衡场

## 实验目标

仿照 `mx-z-optimal-control-xyz-balance` 的 XYZ 直流补偿场三维网格扫描，但将
Z 周期最优控制从 DG4000 任意波改为 Keithley 6221 内部 ARB0 电流任意波；
GS200 继续驱动独立的 Z 主磁场线圈作为 Z 平衡场扫描源。原实验不修改，两种
硬件方案保持独立。

## 频率与标定

`CONTROL_VERSION` 默认 `v4`，理论波形重复频率 12 kHz、理论 rf 频率 12 kHz。
GUI 中 `TRIGGER_FREQUENCY_HZ`（=重复频率）与 `DEMOD_FREQUENCY_HZ`（=f_rf）
均为只读派生字段，切换 `CONTROL_VERSION` 时自动刷新。

电流换算读取
`data/Mx_Keithley_6221_Main_Field_Calibration/0813_183404_mx_6221_main_field_cal`
的自由斜率和截距：

```text
K_f = 635.7501911871854 Hz/mA
f_0 = 327.26386959353465 Hz
I(t) = CONTROL_SCALE * (Omega_ctrl(t) - f_0) / K_f
```

v4 波形约 1 万点、Ω 幅度约 ±20.5 kHz，换算电流包络约 -32.8 到 +31.8 mA；
20 mA 档无法覆盖，默认选择 100 mA 档。预检按实际包络校验所选档位，
`raw/applied_control_waveform.npz` 记录档位覆盖结果。

## 触发与接线

```text
Time_sequence_2 CH2
        |
  6221 Trigger Link Line 1（Line 2 不接）
```

`Time_sequence_2` 输出 0 到 5 V、50% duty 方波，6221 在下降沿响应，配置为
`EXTR:ENAB ON`、`EXTR:ILIN 1`、`EXTR:IGN OFF`、一个周期、`ARM`、`INIT`。
触发间隔 inactive 值按任意波幅度和偏置反算为物理 0 mA，波形末点也固定为
0 mA。

## 扫描与采集

默认 X/Y 为 `-0.05 V` 至 `+0.05 V`、各 11 点，Z 为 `-0.02 mA` 至
`+0.02 mA`、5 点，共 605 个点。数据数组固定使用 `(Z, X, Y)` 索引；采集以
Z 为外层，X/Y 使用跨行、跨平面连续蛇形顺序。每个点先设置三轴补偿值，再将
温控开关设为 0 V DC + Output ON，等待 0.1 s，以实际采样率约 1000 Sa/s 采集
0.2 s Demod0 R；随后恢复温控并等待 2 s。若 `std(R)>0.1 V`，完整保存本次
尝试后重采，最多三次。X/Y 和 GS200 在扫描值为零时也保持 Output ON。

每个采集点前后检查 6221 Compliance；命中或查询失败时依次 `ABORT`、关闭
输出和归零并终止实验。

## 安全结束状态

连接后、修改输出前先保存 X/Y DG4000 通道完整状态和 GS200 的源模式、电流、
量程、限流与 Output 状态。正常完成、取消、异常和 Ctrl+C 均：

- 保持 6221 ARB0 已加载 + ARM + Output ON，继续由共同触发驱动；
- 保持 `Time_sequence_2` 触发输出；
- 恢复 X/Y 通道与 GS200 的运行前状态；
- 温控恢复为 5 V DC + Output ON；Pump/Probe、Pump 载波/门控和 HF2 设置保持；
- 正常结束只断开 TEC。

由于结束时 6221 与触发保持运行，电流持续输出，离开前需人工确认现场安全。

## 数据与分析

采集入口为 `experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py`，
离线分析入口为 `experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance_plot.py`。
运行目录保存理论源文件、6221 标定分析快照、实际电流波形、逐点原始数据和
`raw/xyz_balance_scan.npz`。

分析包含两层工作点估计：

1. **实测网格最小点**（`measured_grid_minimum`）：min(mean R)，保留为对照；
2. **逐层一维 V 形拟合**（`linear_fit`）：对每层 Z 在层最小点的行/列切片上
   拟合 `R = |k(s - s0)| + e`（L1 粗定位 + MAD 迭代剔点 + 最小二乘精修），
   得到连续零点 (x0, y0)(z)。耦合检测对 x0(z)、y0(z) 做线性拟合，斜率
   （V/mA）反映 Z 失谐与 X/Y 补偿的交叉响应；`fitted_workpoint` 取背景 e
   最小层（失谐最小）的拟合零点，并报告外推状态、与网格最小点的差异以及
   `next_scan_suggestion`（零点在窗口外或近边界时建议的下一轮扫描中心）。

同时输出：4 邻域中位数偏差标记的孤立点（瞬态毛刺或窄谷结构，默认绝对
下限 0.15 V）、`balance_linear_fit.npz`（各层零点、耦合斜率、掩码）与
`xyz_balance_linear_fit.png`（工作点层切片拟合 + 零点随 Z 漂移图）。
拟合与网格最小点偏差超过一个扫描步长、零点在窗口外或拟合失败回退时，
均在 warnings 中给出提示。坐标仍为 X/Y 电压与 GS200 电流设定值，不换算
绝对磁场。

### 扫描建议

0814 实测表明剩磁在小时尺度漂移（Z 约 20 nT/h、Y 等效数十 nT），且最优
控制失谐会使零响应点在 (x, y, z) 空间形成随 z 线性漂移的斜谷。因此建议：

- 先以粗网格大范围定位谷底，再以拟合零点为中心第二轮细扫（谷必须落入
  窗口内部，两侧各 ≥3 个点）；
- Y 方向最小值压边界时扩大并平移 Y 范围，直到谷底进入内部；
- 6221 频率标定（f_0mA）需当天重做，否则失谐背景抬高谷底并扭曲形状。
