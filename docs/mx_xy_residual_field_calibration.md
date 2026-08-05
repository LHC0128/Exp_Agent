---
title: Mx XY 剩磁二维校准
type: Mx_XY_Residual_Field_Calibration
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  SCAN_MAIN_FIELD_MA: 0.0
  X_FIELD_START_V: -0.05
  X_FIELD_STOP_V: 0.05
  X_FIELD_POINTS: 21
  Y_FIELD_START_V: -0.05
  Y_FIELD_STOP_V: 0.05
  Y_FIELD_POINTS: 21
  POINT_REPEATS: 1
  SCOPE_SAMPLE_RATE: 20000
  SCOPE_DURATION: 0.5
mapping_keys:
  main_magnetic_field:
    role: fixed_scan
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  scope_waveform:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Temp_Switch:
    role: point_gate
required_devices:
  - GS200
  - DG900
  - DG4000
  - SDS
learned_notes:
  - TEC103 为可选控制设备；COM3 被外部温控软件占用时跳过设温和稳定等待。
  - 平衡探测器输出定义为 SDS CH1 DC 波形的算术平均电压。
  - 不采集强主场基准，直接拟合原始 PD 均值及线性时间漂移。
  - 主平衡值来自 Bz 近似为零时的二维稳态 Bloch 全局拟合。
  - 固定 X 的 Y 色散拟合保留为独立交叉验证。
---

# Mx XY 剩磁二维校准

## 目标

本实验在 GS200 主场设为扫描电流（默认 0 mA）且 Output ON 时，直接二维扫描
`X_magnetic_field` 和 `Y_magnetic_field`，不采集强主场基准。分析器在 `Bz` 近似
为零的前提下，将原始 PD 均值、线性时间漂移和二维稳态 Bloch 响应一起拟合，以连续
拟合中心确定 X/Y 补偿设置，并同时给出离拟合中心最近的实际网格点。

采集直接使用 SDS CH1，不连接或配置 HF2。Z 辅助场固定为 0 V、Output OFF。

## 扫描流程

1. 初始化时将 X/Y 写为 0 V 并关闭输出，GS200 设为扫描主场电流并保持 Output ON。
2. 按 X 外层、Y 从起点到终点的同向顺序扫描完整笛卡尔网格。扫描阶段两路 X/Y
   Output 始终开启，包括设定值严格等于 0 V 的网格点。

默认 X、Y 均扫描 -0.05 至 +0.05 V，各 21 点，共 441 个网格点。每次采集前关闭
温控并等待 0.3 s，SDS 以 20 kSa/s 记录 0.5 s，随后恢复温控为 5 V ON 并等待
1 s。二维 Bloch 拟合要求 X 轴至少 3 点；Y 轴至少需要 7 点，以同时支持二维拟合
和逐 X 色散交叉验证。自动量程需要重采时，每次尝试都重复完整温控门控。

## 原始数据

```text
data/Mx_XY_Residual_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    xy_residual_scan_axes.npz
    grid_X000_Y000_R000.npz
    ...
  results/
```

每个采集文件保存完整时间轴和电压波形、实际采样率、PD 均值与标准差、主场及 X/Y
设定、输出状态、采集时间、顺序和自动量程诊断。

## 离线分析

分析器直接使用每个网格点的原始 PD 均值及实际采集时间：

```text
PD_mean(X,Y,t) = C + D*(t-t0) + A*v/(1+u^2+v^2)
```

其中 `D` 是全局线性时间漂移，`t0` 是全部网格采集时间的平均值。该干扰项用于降低
无强磁基准时缓慢零点漂移对 X/Y 曲面的偏置。分析器同时保存原始均值、拟合漂移、
漂移修正均值、Bloch 空间拟合面、总拟合面和残差。

SDS CH1 的 offset 在整个实验中固定为 0 V，不执行自动 offset 居中。每个网格点及
每次重复采集都根据当前波形自适应缩小或放大 scale；若量程改变，则在同一网格点重新
执行完整温控门控并采集，直到量程合适或达到最大尝试次数。

Pump 沿 Z、Probe 测量 `Px`。在 `Bz≈0`、各向同性稳态近似下，空间部分采用：

```text
PD_spatial(X,Y) = C + A*v/(1 + u^2 + v^2)
u = (X-X*)/Wx
v = (Y-Y*)/Wy
```

`X*`、`Y*` 是连续补偿中心，`Wx`、`Wy` 分别吸收有效弛豫尺度和两路线圈的
电压-磁场换算。分析使用多初值 `soft_l1` 稳健最小二乘；主结果为连续的 `X*`、
`Y*`，`best_x_field_v` 和 `best_y_field_v` 则是离该中心最近的实测网格点，不进行
网格外推。结果显式保存 `bz_assumption: approximately_zero`、二维拟合面、残差面、
RMSE、决定系数和边界诊断。拟合中心靠近扫描边界、宽度触及约束或 `R²<0.8` 时，
`quality_warnings` 会给出提示。

作为交叉验证，分析器还会对每个固定 X 用稳健最小二乘拟合 Y 色散线：

```text
delta(Y) = offset + amplitude * 2*gamma*(Y-Y0) / ((Y-Y0)^2 + gamma^2)
```

其中 `Y0` 是 Y 平衡中心，`2*gamma` 是正负峰间距，中心斜率为
`2*abs(amplitude)/gamma`。逐 X 结果按中心斜率最大选择交叉验证点；幅度最大和
线宽最小用于检查其是否指向同一 X 条件。二维 Bloch 与逐 X 方法的 X/Y 差值会写入
结果，所有逐 X 参数和拟合质量保存至 CSV 和 NPZ，但不再决定主平衡点。

结果保存扁平 CSV、原始和漂移修正均值矩阵、最佳 X/Y、线性漂移率及拟合质量。
`pd_mean_surface.png` 显示原始 PD 均值三维曲面，不再包含强磁基准面；
`bloch_2d_fit.png` 并列显示漂移修正测量面与二维 Bloch 拟合面，
`bloch_2d_residual_map.png` 显示总模型残差，`fitted_time_drift.png` 显示拟合的线性漂移。

## 安全结束状态

正常完成、取消、异常和 Ctrl+C 均恢复 GS200 的源模式、电流、输出、量程和限流；
X/Y/Z 归零关闭，SDS 停止并恢复 DC 耦合，温控恢复为 5 V DC + Output ON。最佳
补偿值只写入结果，不留在硬件输出上。
