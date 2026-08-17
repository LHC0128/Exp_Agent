---
title: Mx Keithley 6221 最优控制 平衡-灵敏度一体化
type: Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  CONTROL_VERSION: v4
  KEITHLEY_CALIBRATION_SOURCE_RUN: 0813_183404_mx_6221_main_field_cal
  BALANCE_COARSE_X_START_V: -0.05
  BALANCE_COARSE_X_STOP_V: 0.05
  BALANCE_COARSE_Y_START_V: -0.05
  BALANCE_COARSE_Y_STOP_V: 0.25
  BALANCE_FINE_ENABLED: true
  BALANCE_FINE_SPAN_V: 0.02
  BALANCE_FINE_POINTS: 11
mapping_keys:
  keithley_6221_main_field:
    role: triggered_optimal_control_current
  main_magnetic_field:
    role: z_balance_and_rf_fixed
  Time_sequence_2:
    role: common_trigger
  X_magnetic_field:
    role: x_balance_and_rf_fixed
  Y_magnetic_field:
    role: y_balance_and_rf_offset
  rf_coil:
    role: y_rf_burst
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
  - 平衡阶段 X/Y 为 DC 输出，RF 阶段 Y 通道切换为带 DC 偏置的外触发 Burst 正弦。
  - 6221 关断策略与 RF 灵敏度实验一致：ABORT + 输出关 + 归零，不保持运行。
  - 平衡细扫中心取自粗扫逐层复线性模拟合工作点，X/Y 半宽与 Z 相对范围可配。
  - 剩磁漂移快时（>0.05 V/10 min）平衡与 RF 阶段之间的失配仍不可避免，一体化只能最小化时间差。
---

# Mx Keithley 6221 最优控制 平衡-灵敏度一体化

## 实验目标

把「XYZ 平衡定位工作点」与「Y RF 灵敏度测量」合并到同一次运行：
保持 6221 外部触发 Z 最优控制，先执行平衡粗扫（`BALANCE_COARSE_*`），在进程内
用逐层复线性模拟合（`R = sqrt((a·s+c)² + (b·s+d)²) + e0`）得到工作点，再按
`BALANCE_FINE_ENABLED` 以拟合零点为中心做细扫，最后把拟合工作点注入
X/Y DC、GS200 电流与 Y RF DC 偏置，紧接着完成 Y RF 相位校准、带符号幅度
扫描与零 Y RF 噪声采集。平衡与灵敏度之间只有设备切换时间（秒级），
最大程度减小剩磁漂移引起的失配。

## 流程

1. 连接设备、时钟同步、温控、6221 最优控制与共同触发、HF2 配置；
2. 平衡粗扫（Z 外层、X/Y 蛇形，逐点温控门控采集 R）→ `balance_round_0_coarse_scan.npz`；
3. 进程内拟合 → 工作点 + 下一轮建议；
4. 可选细扫（以粗扫工作点为中心）→ `balance_round_1_fine_scan.npz`；
5. 最终轮另存 `xyz_balance_scan.npz`（兼容单实验离线分析）；
6. X/Y/GS200 切换为平衡点 → Y RF 相位校准 → 幅度扫描 → 噪声采集；
7. 结束关断 6221（ABORT + 输出关 + 归零）、归零关闭共同触发、恢复 X/Y/GS200
   运行前状态与温控 5 V ON。

## 数据与分析

- 平衡部分：`raw/xyz_balance_scan.npz`（+ 每轮 npz），复用 XYZ 平衡场分析
  （网格最小点 + 逐层复线性模拟合 + 耦合斜率 + 外推建议）；
- RF 部分：`raw/phase_scan.npz`、`raw/amplitude_scan.npz`、`raw/noise_*.npz`，
  复用 RF 灵敏度分析（色散拟合、PSD、平坦段检测）；
- 汇总 `results/analysis.yaml`：`balance`（工作点/耦合/警告）与
  `rf_sensitivity`（斜率/灵敏度/拟合）两个子节 + 合并 warnings；
- 子分析完整结果另存 `balance_analysis.yaml` 与 `rf_analysis.yaml`。

## 注意事项

- 父类的 `FIXED_PARAMS.X/Y/main_magnetic_field` 仅在平衡失败时作为回退补偿值。
- 平衡拟合工作点标记 `extrapolated` 时仍在窗口外，RF 阶段会带警告继续；
  建议扩大 `BALANCE_COARSE_*` 范围重跑。
- 6221 波形自身失真/漂移（见 0816 示波器验证：二次谐波 2.3%、三次谐波
  29%、DC 偏差 ~0.8 mA）会引入失谐，标定过期时应先重跑
  `mx-keithley-6221-main-field-calibration`。
