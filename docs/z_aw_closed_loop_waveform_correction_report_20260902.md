---
title: Z 任意波实际电流闭环校正复测报告
type: experiment_report
experiment: z-aw-closed-loop-waveform-correction
date: 2026-09-02
---

# Z 任意波实际电流闭环校正复测报告

## 结论

当前闭环失败的首要原因不是迭代公式本身，而是用于逆滤波的电流频率响应相位不可靠。旧频响标定只有单次采集（`SCOPE_REPEATS=1`），在 250 Hz--2.5 kHz 内出现约 60--180 度的跳变；闭环迭代因此把测量噪声和随机相位当成线圈响应，命令电压逐轮放大，最终发散。

0824 的 `oc_sens/v1` 结果证明，在频响与目标频谱相容时，现有闭环算法可以收敛；0902 的 `oc_broadband_v2/v5` 结果则不满足这一条件。因此暂不建议直接增加迭代次数或提高逆滤波增益。

## 两次闭环结果

分析器已重新生成以下文件：

- [0824 波形对比 CSV](../data/Z_AW_Closed_Loop_Waveform_Correction/0824_094157_z_aw_closed_loop_waveform_correction/results/waveform_comparison.csv)
- [0824 波形对比图](../data/Z_AW_Closed_Loop_Waveform_Correction/0824_094157_z_aw_closed_loop_waveform_correction/results/waveform_comparison.png)
- [0902 波形对比 CSV](../data/Z_AW_Closed_Loop_Waveform_Correction/0902_090517_z_aw_closed_loop_waveform_correction/results/waveform_comparison.csv)
- [0902 波形对比图](../data/Z_AW_Closed_Loop_Waveform_Correction/0902_090517_z_aw_closed_loop_waveform_correction/results/waveform_comparison.png)

| 运行 | 理论结果 | 最佳 hold-out NRMSE | 相关系数 | 峰值比 | RMS 比 |
|---|---|---:|---:|---:|---:|
| 0824 | `oc_sens/v1` | 0.0241 | 0.99994 | 1.037 | 1.011 |
| 0902 | `oc_broadband_v2/v5` | 0.7409 | 0.78365 | 0.912 | 1.159 |

0902 的迭代频谱 NRMSE 为 `0.085 -> 0.212 -> 0.374 -> 0.545`，而命令范围和实测峰值电流持续扩大（峰值约 `1.21 -> 1.90 mA`）。最佳轮次是第 1 轮，继续迭代属于恶化。

## 已尝试的修正方法

1. **原有复数频响逆滤波 + 阻尼迭代**：在 0902 数据上只能改善相关系数，不能降低 hold-out 误差；说明形状和幅值被不同频率分量同时拉偏。
2. **降低迭代阻尼、增加正则化**：可减慢命令发散，但不能恢复错误相位，不能作为根本修正。
3. **hold-out 选优和回滚**：现有程序已经选择第 1 轮作为最佳轮次，避免把第 2、3 轮的过冲命令冻结为最终输出。
4. **相干重触发**：已修改频响采集工作流。每次重复先让 SDS 进入单次等待，再对共同触发和 Z 输出执行相位初始化，随后先开 Z 正弦、再开 CH4 触发。这样把输出启动相位与示波器下降沿绑定，避免连续运行造成随机周期相位。

## 硬件复测

### 相干触发短扫

运行目录：[0902_102903_0902_frequency_response_coherent_test2](../data/Z_Coil_Current_Frequency_Response/0902_102903_0902_frequency_response_coherent_test2)

100 Hz 和 5 kHz 的重复幅值稳定，但 1.73 kHz、3.37 kHz 的相位标准差仍约 60 度，说明仅改变输出开关顺序仍不足以保证每次正弦从同一相位开始。

### 相位初始化和启动顺序验证

运行目录：[0902_103451_0902_frequency_response_phase_order_test](../data/Z_Coil_Current_Frequency_Response/0902_103451_0902_frequency_response_phase_order_test)

加入两路 `phase_init` 并调整启动顺序后，100 Hz 相位重复标准差约 0.13 度，16.7 kHz 约 11.3 度；33 kHz 以上受信噪比和拟合残差影响，可靠频点仅到 16.7 kHz。

### 目标频带扫频

运行目录：[0902_103637_0902_frequency_response_target_band_test](../data/Z_Coil_Current_Frequency_Response/0902_103637_0902_frequency_response_target_band_test)

目标频带内的复测结果为：

| 频率 | 相位均值 | 重复标准差 | 拟合相关系数 | 可靠性 |
|---:|---:|---:|---:|---|
| 100 Hz | -1.2 度 | 0.0 度 | 0.9985 | 可靠 |
| 5.8 kHz | -55.9 度 | 0.6 度 | 0.9965 | 可靠 |
| 11.5 kHz | -61.9 度 | 8.1 度 | 0.9908 | 可靠 |
| 17.2 kHz | -78.9 度 | 24.5 度 | 0.9808 | 不可靠 |

这说明新的采集时序已显著改善相位一致性，但仍应把闭环逆滤波频带限制在可靠频点之间，并对频响做平滑和正则化；不能把单个高频拟合点直接外推到整个任意波频谱。

## 推荐的下一次闭环方案

1. 使用新的相干采集时序，重新标定 100 Hz--约 12 kHz 的电流频响，至少每点 5 次；以重复相位标准差小于 5 度、拟合相关系数大于 0.99 作为纳入标准。
2. 仅在目标波形实际含能频带内插值复数频响；带外频点保持基线命令，不做逆滤波。DC 分量单独使用 100 Hz 增益近似，不参与复数相位更新。
3. 首轮使用 `ITERATION_DAMPING=0.05--0.10`、较强正则化，并保留一组 hold-out；任何 hold-out NRMSE 恶化超过 5% 即回滚。
4. 先离线用新频响回放 0902 target，确认预测命令不超过电压和电流限值，再进行硬件闭环。硬件测试从 1 轮开始，达到目标即停止，不以固定迭代次数为目标。
5. 若仍出现幅值偏差而形状相关性保持较高，优先做整体增益校正或 DC 偏置校正，不要继续增强频域相位补偿。

## 代码与数据变更

- [频响工作流](../lab_workflows/experiment_modules/z_coil_inductance_frequency_response/workflow.py) 增加相干重触发回调，并在每次重复中初始化两路 DG 相位。
- [闭环离线分析器](../lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/analysis.py) 已输出 target/实测/hold-out 逐点对比 CSV、波形图和 `final_waveform_report.yaml`。
- 新增结果集选择、固定根目录和版本目录说明仍见 [闭环实验文档](z_aw_closed_loop_waveform_correction.md)。

## 风险与限制

本报告未使用新的频响实际运行一次闭环，因为当前复测在 17 kHz 以上仍有不稳定点，且需要先确认目标波形频谱能量确实落在可靠带宽内。旧频响和 0902 闭环数据保留不变，可用于追溯和复现。
