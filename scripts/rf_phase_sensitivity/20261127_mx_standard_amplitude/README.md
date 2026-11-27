---
title: 标准 Mx 三档载波幅度下的相位响应与相位灵敏度（新读数链）
conclusion: 0.15 Vpp 的相位响应最大（相图半径 31.5 mV、切线斜率 31.5 mV/rad），0.05 Vpp 为 28.4 mV，0.5 Vpp 饱和降到 13.6 mV；三档切线斜率都等于半径（1.000-1.002，离散 ≤1.4%）。3-50 Hz 相位灵敏度（y 走线口径）0.05/0.15/0.5 Vpp 分别为 193.1/189.7/438.1 urad/rtHz，θ 诊断比值 1.013/1.004/0.999。A05 频响 37/37 点全部接受（29-31 点 clean），H0 0.0333/0.0370/0.0140 V/rad、fc 78.8/105.9/232.0 Hz，H0/K0 1.18/1.19/1.03，低频闭合 1.002/1.004/0.990；650-900 Hz 部分频点音调信噪比低于 5，按 advisory 处理（不阻止出图）。
data:
  - mx_standard_rf_phase_sensitivity/1127_182021_mx_std_v3_vpp005（0.05 Vpp，24 点相图 + 11 点细扫 + 37 点调相扫频 + 4 段宽带噪声）
  - mx_standard_rf_phase_sensitivity/1127_183307_mx_std_v3_vpp015（0.15 Vpp，同上）
  - mx_standard_rf_phase_sensitivity/1127_184422_mx_std_v3_vpp050（0.5 Vpp，同上）
  - 旧链窄带对照：1127_154410/1127_153650/1127_153151（TC 1 ms、5 kSa/s，仅 3-50 Hz 与 24 点相图）
---

# 标准 Mx 三档载波幅度下的相位响应与相位灵敏度

## 回答的问题

标准 Mx 构型（GS200 按标定式给主场、无 Z 脉冲、MFLI Out1 连续 5 kHz 载波）下，把 Y 射频幅度
从 0.05 Vpp 提到 0.5 Vpp，相图半径（相位响应）、基带频响与相位灵敏度如何变化；切线斜率是否
仍等于半径；θ 与 y 两条噪声路径在新读数链下是否一致。

## 脚本

`compare.py` 读各轮 `results/summary.json`，按 `Y_RF_VPP` 归并（同幅度取最新一轮），打印表格并
画 `figures/amplitude_compare.png`、写 `figures/amplitude_compare.json`。只读运行目录，不连接仪器。
每轮各自的灵敏度谱由 B01 写成该运行目录的 `results/phase_noise.png`（已做 √Lorentzian H0 归一化），
按需求不合并三档。

```
D:\Code\exp_agent\agent_exp_env\Scripts\python.exe ^
  scripts\rf_phase_sensitivity\20261127_mx_standard_amplitude\compare.py ^
  --data-root D:\Code\exp_agent_run\data
```

## 结果（新读数链，宽带档）

| Y_RF_VPP | 相图半径 R | 切线斜率中位 | 斜率/R | 斜率离散 | y 走线 3-50 Hz | θ 诊断 3-50 Hz | θ/y | H0 (V/rad) | fc (Hz) | H0/K0 | 闭合比 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05 | 28.369 mV | 28.381 mV/rad | 1.0004 | 1.2% | 193.1 urad/rtHz | 196.7 | 1.013 | 0.03325 | 78.8 | 1.183 | 1.002 |
| 0.15 | 31.543 mV | 31.543 mV/rad | 1.0000 | 1.4% | 189.7 urad/rtHz | 191.0 | 1.003 | 0.03699 | 105.9 | 1.186 | 1.004 |
| 0.50 | 13.564 mV | 13.586 mV/rad | 1.0016 | 1.4% | 438.1 urad/rtHz | 438.0 | 0.999 | 0.01399 | 232.0 | 1.033 | 0.990 |

- 三档工作点：0.05/0.15 Vpp 在 106°（相位 −179.7°/179.9°），0.5 Vpp 在 286°（相位 0.3°）；GS200 0.4311 mA。
- A05：37/37 点接受，clean 29/31/29；650-900 Hz 的部分频点音调信噪比 < 5，标为 advisory。
- 相位灵敏度口径为 y 走线（工作点切线投影，K = R|cos φ|）；θ 仅诊断，宽带档下已按保留直流低通
  隔离 −5 kHz 载波镜像（见 scripts/mx_standard_rf_phase_sensitivity/20261202_theta_condition）。

## 与旧链窄带轮的对照

旧链（TC 1 ms、5 kSa/s，24 点相图 + 4 段窄带噪声）三档半径 33.03/37.78/16.41 mV、3-50 Hz
θ 路径噪声底 125.7/107.0/232.6 urad/rtHz。新链半径整体低约 12-17%、3-50 Hz 噪声底高约
1.5-1.9 倍：前者来自本轮装置/对准状态（同一趋势：0.15 Vpp 最大），后者来自宽带解调把 50 Hz 以上
噪声一并纳入（这是测 1 kHz 以内频响的代价）。两组数字不可直接混用，本 README 只把旧链作为趋势对照。