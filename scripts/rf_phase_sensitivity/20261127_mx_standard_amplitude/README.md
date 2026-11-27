---
title: 标准 Mx 三档载波幅度下的相位灵敏度与噪声底
conclusion: 标准 Mx 构型（GS200 定标主场、无脉冲、MFLI Out1 连续 5 kHz 载波）在 0.15 Vpp 处相位响应最大（相图半径 37.78 mV、切线斜率 37.83 mV/rad），0.05 Vpp 为 33.03 mV，0.5 Vpp 已饱和降到 16.41 mV；三档切线斜率都等于半径（1.000-1.001，离散不超过 0.9%），theta 与 y 两条噪声路径在 3-50 Hz 一致（0.998-1.001）；相位噪声底 0.05/0.15/0.5 Vpp 分别为 125.7/107.0/232.6 urad/rtHz
data:
  - mx_standard_rf_phase_sensitivity/1127_154410_mx_std_vpp005（0.05 Vpp，24 点相图 + 4 段静态噪声）
  - mx_standard_rf_phase_sensitivity/1127_153650_mx_std_vpp015（0.15 Vpp，24 点相图 + 4 段静态噪声）
  - mx_standard_rf_phase_sensitivity/1127_153151_mx_std_vpp050（0.5 Vpp，24 点相图 + 4 段静态噪声）
  - mx_standard_rf_phase_sensitivity/1127_151343_mx_std_vpp050（0.5 Vpp，采集完整但收尾记失败，未进入对比）
---

# 标准 Mx 三档载波幅度下的相位灵敏度与噪声底

## 回答的问题

标准 Mx 构型在 5 kHz 载波、主场由标定式反算的条件下，把 Y 射频幅度从 0.05 Vpp 提到
0.5 Vpp，相图半径（相位响应）与 3-50 Hz 相位噪声底如何变化，切线斜率是否仍然等于半径，
theta 与 y 两条噪声路径是否仍然一致。

## 脚本

`compare.py` 读正式实验各轮 `results/summary.json`，按 `Y_RF_VPP` 归并（同幅度取最新一轮），
打印表格并画 `figures/amplitude_compare.png`、写 `figures/amplitude_compare.json`。只读运行目录，
不连接仪器。

```
D:\Code\exp_agent\agent_exp_env\Scripts\python.exe ^
  scripts\rf_phase_sensitivity\20261127_mx_standard_amplitude\compare.py ^
  --data-root D:\Code\exp_agent_run\data
```

## 结果

| Y_RF_VPP | 相图半径 R | 切线斜率中位 | 斜率/R | 斜率离散 | theta 3-50 Hz | y 路径 3-50 Hz | theta/y | 100 Hz 工频 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05 Vpp | 33.031 mV | 33.03 mV/rad | 1.000 | 0.9% | 125.7 urad/rtHz | 126.1 urad/rtHz | 0.999 | 80.9 urad/rtHz |
| 0.15 Vpp | 37.780 mV | 37.82 mV/rad | 1.001 | 0.7% | 107.0 urad/rtHz | 107.1 urad/rtHz | 1.001 | 49.5 urad/rtHz |
| 0.5 Vpp | 16.414 mV | 16.41 mV/rad | 1.000 | 0.9% | 232.6 urad/rtHz | 232.7 urad/rtHz | 0.998 | 78.5 urad/rtHz |

三档都是 24/24 段全部通过帧体检，工作点都落在 286-287 度、噪声段相位偏离 0.1-0.4 度，
GS200 电流 0.4311 mA（标定式给 5 kHz）。

- **响应在 0.15 Vpp 处最大，0.5 Vpp 已进入饱和分支**：0.5 Vpp 的相位响应只有 0.05 Vpp 的
  一半，说明这时的射频驱动已越过相位响应的最优区，继续加大只是把原子响应推离共振。
  这与第七节脉冲构型三档 K0 的表现同向（0.15 Vpp 平台、0.5 Vpp 饱和）。
- **圆与切线斜率的关系在三档都成立**：不管幅度大小，相图都是圆，逐段实测切线斜率的中位值
  与半径之比都在 1.000-1.001，离散不超过 0.9%。切线的斜率就是半径，与"Y 投影 |dY/dphi| =
  R|cos phi|"只在工作点处取最大值并不矛盾。
- **两条噪声路径始终等价**：三档 theta 与 y 路径的 3-50 Hz 比值都在 0.998-1.001，所以正式流程
  取 y 路径（K=R）不会引入路径选择误差。
- **噪声底并非随幅度单调**：0.15 Vpp 最低（107.0 urad/rtHz），0.05 Vpp 次之（125.7），0.5 Vpp
  最高（232.6）。0.5 Vpp 的信噪比损失来自响应饱和，而不是读出噪声变大。

## 读数链说明与后续

这三轮用的是本轮登记的窄带静态读出（DEMOD_TC = 1 ms、DEMOD_RATE = 5000 Sa/s），噪声谱在
3-50 Hz 只受部分滤波影响。按用户要求，下一步把 A05/A06 切到脉冲实验的高带宽档
（1 us / 50 kSa/s）、补做基带频率响应，并用脉冲实验同一套泄漏扣除与 √Lorentzian 回补重测
这三档，届时本记录的三档数字作为窄带对照保留。

## 一轮失败数据

`1127_151343_mx_std_vpp050` 采集了完整的 24 点相图与 4 段噪声（离线分析可用），但结束会话时
对已断开的 MFLI 重复做了一次安全收尾，把已完成的收尾覆盖成失败、运行目录记成 failed。
缺陷已在 `workflow.py` 修掉（同一会话只收尾一次）并加了假仪器回归用例，重跑
`1127_153151_mx_std_vpp050` 收尾干净通过。对比只用干净轮次。
