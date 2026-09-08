---
title: Z 任意波时域差分闭环验证报告
type: experiment_report
experiment: z-aw-closed-loop-waveform-correction
date: 2026-09-02
---

# Z 任意波时域差分闭环验证报告

## 验证方法

新增参数 `CORRECTION_METHOD=time_domain`。该模式不使用复数频响相位做预加重，而是对 CH4 下降沿对齐后的时域误差进行 9 点滑动平均，再用最低可靠频点的幅值增益换算为电压修正：

\[
v_{k+1}(t)=v_k(t)+\alpha\,\frac{LPF(i_{target}(t)-i_k(t))}{|H_{low}|}
\]

本次使用 `oc_broadband_v2/v5`、`ITERATION_DAMPING=0.05`、2 次反馈和 1 次 hold-out。为避免低相关时跨越整个 4 ms 周期误配，闭环对齐窗口限制在触发边沿附近 ±0.5 ms。

## 硬件结果

### 第一次验证

运行目录：[0902_104840_0902_v5_time_domain_closed_loop_test](../data/Z_AW_Closed_Loop_Waveform_Correction/0902_104840_0902_v5_time_domain_closed_loop_test)

第 1 轮完整完成：

| 指标 | 反馈平均 | hold-out |
|---|---:|---:|
| 时域 NRMSE | 0.0942 | 0.0970 |
| 相关系数 | 0.99925 | 0.99855 |
| 峰值比 | 0.9928 | 0.9907 |
| RMS 比 | 1.0209 | 1.0164 |
| 最大绝对误差 | 0.125 mA | 0.112 mA |

这已经明显优于此前 `oc_broadband_v2/v5` 频域闭环的最佳 hold-out NRMSE `0.7409`，并且绝对幅值基本正确。第 1 轮生成的 `raw/iteration_000/feedback.npz`、两次反馈采样和 hold-out 原始波形均已保存。

但该次在进入第 2 轮时 SDS 未捕获到 CH4 下降沿，工作流执行安全关闭，因此没有完整的多轮收敛曲线。

### 第二次验证

运行目录：[0902_105245_0902_v5_time_domain_closed_loop_test3](../data/Z_AW_Closed_Loop_Waveform_Correction/0902_105245_0902_v5_time_domain_closed_loop_test3)

第 1 次反馈采集成功，第 2 次反馈采集时 SDS 再次进入 STOP，未形成完整反馈轮次；输出已自动关闭。该问题属于触发/采集重臂时序故障，不是电流误差算法的数值发散。

## 结论

时域差分方法在一次完整轮次中有效：波形形状相关性达到 `0.9985`，峰值和 RMS 偏差约 1--2%，明显优于旧频域方法。它验证了 `v5` 失败的主要来源确实是旧复数频响相位，而不是目标波形不可实现。

当前尚不能宣称“多轮时域闭环已稳定收敛”，因为 SDS 在连续重复采集时仍可能丢失触发。下一步应优先修复采集重臂：每次重复前确认 SDS 处于等待状态、相位初始化两路 DG，并在未检测到下降沿时自动重新配置而不是直接终止整轮。算法参数建议保持 `ITERATION_DAMPING=0.05`，每轮以 hold-out 指标选优；第 1 轮已经达到实用的幅值精度，不需要盲目增加迭代次数。
