---
title: 标准 Mx 射频相图与 theta/y 噪声谱路径
conclusion: 脚本已就绪，等待首轮上机实测后补结论
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
- 采集链：整周调相扫描（默认 24 点）→ 圆拟合与逐段斜率 → |dy/dtheta| 最大处切宽带 Demod 4
  → 独立温控窗口分段噪声 → Welch PSD → 三条路径（theta / y 除斜率 / 切向投影）对比。

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

## 待查

- 标准 Mx 下相图是否真为圆、半径与残差多少，需首轮数据判断。
- 相图若接近点（半径远小于读出噪声），说明当前 Z 常数控制下原子响应未建立，需先调主磁场或射频幅度。
- 是否需要在 Z 通道加常数 DC 偏置（而不是单纯关闭脉冲链），首轮不建议引入，先看零偏置结果。
