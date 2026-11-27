---
title: 标准 Mx 射频相图与 theta/y 噪声谱路径
conclusion: 标准 Mx 相图确为圆且切线斜率恒等于半径（24/24 段，R=16.448 mV）；噪声帧曾因动态短时间常数被 5 kHz 分量污染，已改静态采集并加帧体检，θ/y 路径待重跑
data:
  - rf_phase_sensitivity/_studies/1127_133853_mx_std_full（24 点相图 + 6 段噪声）
  - rf_phase_sensitivity/_studies/1127_133601_mx_std_smoke（12 点相图 + 2 段噪声）
---

# 标准 Mx 射频相图与 theta/y 噪声谱路径

## 回答的问题

标准 Mx 构型（GS200 常数主场、Z 脉冲链关闭、MFLI Out1 连续 5 kHz 载波）下：

1. 射频相位整周扫描得到的 X/Y 相图是否为圆，各相位处切线斜率是否接近（对应任务清单 C3，尚未证实）。
2. 同一工作点上，用 theta=atan2(y-y0, x-x0) 的功率谱直接得到相位噪声，和用 y 的功率谱
   除以斜率 |dy/dtheta| 得到的相位噪声，是否一致（对应 O1，决定正式实验流程）。

## 脚本

- `measure.py`：采集 + 分析 + 离线重算。GS200 电流只由主磁场标定式 `I=(f-b)/K` 反算
  （默认 K=11089 Hz/mA、b=219.7 Hz，5 kHz 对应 0.4314 mA），不提供手填覆盖。
- 采集链：预热丢弃 + 整周调相扫描（默认 24 点）+ 回采首点 → 圆拟合与逐段切线斜率
  → 按 |dY/dphi| = R|cos phi| 选工作点、±5° 细扫定斜率 → 静态解调配置下独立温控窗口分段噪声
  → 帧体检 → Welch PSD → 三条路径（theta / y 除斜率 / 切向投影）对比。

## 安全收尾

`finally` 内恢复王湾 Pump 原状态、调用 `preparation.park`（关 MFLI Out1/MOD、Z/X 归零回读、
停 Demod 传输、恢复温控与主场），最后统一断开设备；收尾任一项失败即按失败退出。

## 复现

```powershell
$env:LAB_DATA_ROOT = "D:/Code/exp_agent_run/data"
agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --dry-run
agent_exp_env/Scripts/python.exe scripts/rf_phase_sensitivity/20261127_mx_standard_phase_noise/measure.py --tag mx_std_stage1
```

## 上机记录

- `1127_132447`（13:23，第一次）：`STATIC_POINT_DURATION_S=5.0` 超上限 1.0 s、噪声总 Welch 段数 <2，
  参数校验即退出，未连接任何仪器。
- `1127_132520_mx_std_smoke`（13:25，第二次）：参数通过、设备连接与时钟同步完成、TEC103 缺失按可选
  设备跳过；卡在共享 `z_chain_common.rf_phase.Carrier.begin_carrier()` 的 MF-MOD 回读——
  `sidebands/0/oscselect` 与 `sidebands/1/oscselect` 回读均为 2，而统一布局（Demod n ↔ Osc n）期望
  (1, 2)。载波 Osc 1、5 kHz、0.25 Vpk、2° 调制深度、1 V 量程、偏置 0、外时钟全部匹配。
  安全收尾 `completed: true`、无错误：MFLI Out1/MOD 关闭、边带关闭、Z/X 归零并 OFF、Temp_Switch 回
  5 V ON、GS200 回运行前状态（输出 OFF）、王湾 Pump 恢复 ON；0 帧数据。
- `1127_133118_mx_std_smoke`（13:31，第三次）：参数与设备连接正常，卡在脚本自身
  `NameError: _StandardCarrier__lab_check_cancel`。根因是 GUI 代跑执行器
  （`lab_workflows/script_runner.py`）会在每个 `for`/`while` 体首注入取消检查 `__lab_check_cancel()`；
  该名字落在类体内被 Python 按类名改写（mangling）成 `_StandardCarrier__lab_check_cancel`，全局不存在。
  安全收尾同样 `completed: true`、0 帧数据。
- 对策：载波改为模块级函数（不再用类），并用执行器同一套 AST 注入 + 桩设备离线验证
  `_carrier_begin` / `_carrier_set_phase`；脚本内不再出现带 `for`/`while` 的类方法。
- 结论：标准 Mx 全程不用 PM 边带，脚本改为自带 `StandardCarrier`（只写并回读载波/读出节点，边带保持
  关闭且不校验其振荡器），其余公共步骤镜像 `preparation.prepare_measurement`；`--dry-run` 现在也做
  参数校验，避免再以非法参数占用上机窗口。

## 首轮结果与修正（`1127_133601_mx_std_smoke`、`1127_133853_mx_std_full`）

两轮都 `completion_status: completed`，安全收尾无错误、Pump 已恢复，GS200 由公式给 0.4311 mA，
射频 5 kHz / 0.5 Vpp。

- 相图：`1127_133853_mx_std_full` 24 点 × 15°，圆拟合 R=16.448 mV、圆心 (0.30, −0.43) mV，
  除首点外残差 ≤0.66 mV，闭合。**C3 的两部分都成立**：
  - 相图是圆：残差 rms 0.787 mV（4.8% R）；
  - 各相位切线斜率接近：按实测相位角差分、弦长按 `2R sin(Δφ/2)` 反解，24/24 段切线斜率
    中位 16.448 mV/rad、散布 <1%，即斜率恒等于半径。
- 修正此前两条误报：
  1. 把 `|dY/dθ|` 当成了「斜率」。相图为圆时 `|dY/dθ| = R|cos φ|` 必然在 0 与 R 之间变化，
     它是切线在 Y 上的投影，不是切线斜率；斜率是 `|dS/dφ| = R`。
  2. 用弦长除「指令步长」并把换相位后的首点算进散差，得到 2.50 的相对散差。首点残差 +3.08 mV、
     实测转角 101°（应为 15°），一段假值把整个区间拉满。
- 噪声帧无效（两轮都中招）：`_capture_noise` 走 `dynamic=True`，把 Demod 4 切到 1 µs 时间常数 +
  53.57 kSa/s，5 kHz 射频分量没被滤掉。帧内是一个 −4999.9 Hz、幅度 490.8 mV 的旋转相量
  （相位抖动 0.01°），直流只有 16.5 mV，噪声谱测的是它自身的漂移。相图帧走静态路径
  （1 ms、6.70 kSa/s）是干净的（std 2.79 mV），所以相图结论不受影响，此前报的
  0.65 / 130 / 834 / 2918 µrad/√Hz 全部作废。

## 修改后的流程

1. 相图：首点前先采一段丢弃（`mx_std_warmup_00`），正文 24 点，末尾回采首点
   （`mx_std_repeat_00`）量整圈漂移。
2. 工作点：按圆模型取 `|dY/dφ| = R|cos φ|` 最大的相图点作细扫中心，±5°、步长 1° 细扫，
   用实测相位角回归 `dY/dφ`，与 `R|cos φ|` 互校后定最终工作点。
3. 噪声：沿用相图的静态解调配置（1 ms、6.70 kSa/s），6 段 × 4 s。
4. 帧体检：每帧求直流幅度与 100 Hz 以上最强单音，单音 > 直流 20% 或 std > 直流 5 倍即报错。
   用新代码重算上述两轮的噪声帧，会在第一帧直接拒绝。

## 待查

- 静态配置下噪声谱的真实水平、θ 与 y 是否一致，需按新流程重跑一轮。
- 预热丢弃是否消掉首点瞬态：看 `first_point_drift_v` 与 24 段切线斜率里是否还有离群段。
- 相图残差 0.787 mV 里有多少来自温控窗口与相位改写本身的抖动，需多轮比较。
