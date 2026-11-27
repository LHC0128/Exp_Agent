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

## 待查

- 标准 Mx 下相图是否真为圆、半径与残差多少，需首轮数据判断。
- 相图若接近点（半径远小于读出噪声），说明当前 Z 常数控制下原子响应未建立，需先调主磁场或射频幅度。
- 是否需要在 Z 通道加常数 DC 偏置（而不是单纯关闭脉冲链），首轮不建议引入，先看零偏置结果。
