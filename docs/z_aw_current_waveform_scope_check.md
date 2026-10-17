---
title: Z 任意波实际电流波形验证
type: Z_AW_Current_Waveform_Scope_Check
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: arbitrary_output
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_low_side_sense_and_CH4_trigger
required_devices:
  - DG4000
  - SDS
learned_notes:
  - 命令电压仍由独立的历史 Z 电压标定生成，比较目标则由实际电流耦合标定生成。
  - CH3 采样电阻电压是闭环判据；线圈端电压实验仅保留为诊断。
experiment_id: z-aw-current-waveform-scope-check
defaults_file: params/experiments/z-aw-current-waveform-scope-check.yaml
---

# Z 任意波实际电流波形验证

当前默认参数：[参数文件](../params/experiments/z-aw-current-waveform-scope-check.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `z-aw-current-waveform-scope-check`。选择 `theory` 时目标电流为 `CONTROL_SCALE * Omega_ctrl / K_Z_Hz_per_A`，命令电压由 `COMMAND_Z_CALIBRATION_SOURCE_RUN` 对应的独立 Z 电压标定换算；隐藏兼容键 `Z_CALIBRATION_SOURCE_RUN` 保留但不替代命令标定选择。选择 `corrected_run` 时直接加载闭环冻结运行中的目标电流、命令电压、Vpp 和 offset，不再经过旧电压标定，也不按当前 `CONTROL_SCALE` 重新缩放冻结目标。实测电流为 `V_R/R_s`。

分析保留原周期延迟搜索、固定单周期重采样、形状相关系数、NRMSE、仿射增益和偏置、峰峰值比例、频谱 NRMSE、重复标准差以及 CH3 饱和诊断。量程和偏置与测量值一起换算为安培，避免用电流数值直接比较伏特量程。报告中的历史 `*_v` 指标键保留；在本实验中对应的偏置、RMSE 和峰峰值单位为 A，增益及比例无量纲。饱和与相似度只作诊断，没有新增自动通过阈值。

该实验用于闭环前基线、每轮快速验证和最终冻结波形确认。入口为 `experiments/Z_AW_Current_Waveform_Scope_Check.py` 和 `experiments/Z_AW_Current_Waveform_Scope_Check_plot.py`。

## 接线、预检与采集

- Z 任意波与 `Time_sequence_2` 必须映射到同一信号源的不同通道；按设备库创建型号驱动，连接后严格同步并回读参考时钟，失败不回退设备。
- SDS CH3 接低端采样电阻电压，CH4 接共同触发；采样电阻低端与信号源 BNC 外壳共地，CH3 不跨接线圈。
- `SENSE_RESISTOR_OHM` 必须与电流耦合标定一致；额定功率和 `MAXIMUM_CURRENT_A` 必须为有效正值。目标电流的 RMS 功率按额定功率的一半校验，峰值电流不超过用户上限；Z 输出包络及触发电平均通过安全限值检查后才允许输出。
- 闭环来源要求标定运行名和 SHA 与当前电流标定一致。A03 加载实际命令与电流目标；A05 复制来源并逐项检查原件和副本哈希，再保存目标和原始帧。外部来源变化时须重新执行 A03，不能把新标定悄悄用于旧输出。

本实验是纯电学波形工具，不执行气室温控门控，不连接 TEC、锁相或 GS200。先配置任意波与外部下降沿 Burst，再准备 Scope，Scope 就绪后才开启共同触发。每帧等待真实 CH4 触发并停止 SDS 后读入内存、保存；保留既有坏帧重试、量程/偏置自动调整及取消检查点。每次 A05 创建新目录，重跑不覆盖旧 raw/config；后续读取或写盘失败时保留已保存帧和原始异常。

正常结束、异常、取消和通信 EOF 均复用公共安全收尾：停止 SDS，将 Z 写为 DC 0 V 并关闭输出，释放该会话连接；共同触发保持最后状态，包括配置中断时的 OFF。恢复失败明确记录失败，不把断开通信当作输出恢复成功。

## GUI：运行与历史

运行 Tab 复用 XY 采集/分析两列流程，保存参数与布局、派生预览、预检、完整运行、取消及任务日志位于流程顶部。参数在实际配置节点内分基础/高级编辑，初始化条件只展示在配置节点；同一参数草稿统一保存。应用参数使相关节点失效，须显式重跑，保存默认值和预检不会自动执行步骤。等待下一步仍保持设备连接、输出和全局硬件锁；刷新可恢复存活会话，后端重启不会从磁盘恢复硬件。

| 节点 | 职责及依赖 |
| --- | --- |
| A01 | 参数、来源、安全与接线预检，无硬件副作用 |
| A02 | 依赖 A01；连接映射设备，同步及回读时钟 |
| A03 | 依赖 A02；加载目标电流，配置任意波和共同触发 |
| A04 | 依赖 A03；配置 Scope，确认触发就绪后启用共同触发 |
| A05 | 依赖 A04；冻结来源，重复采集并逐帧保存；重跑新建目录 |
| A06 | 无前置；显式安全结束，执行中改用任务取消 |
| B01 | 独立加载明确目录的快照、目标电流与原始帧，检查格式及触发 |
| B02 | 依赖 B01；V/R 换算、周期对齐和相似度/频谱评估 |
| B03 | 依赖 B02；从有效缓存绘图并导出原报告 |

分析列默认操作下一次实验；本实验没有额外分析覆盖参数。选择具体已结束目录后，B01–B03 只使用该目录采集快照和 raw，不加载当前默认值或外部标定来源。运行/历史切换保留草稿；保存默认值只保存下一次实验设置与统一布局，历史填回只更新草稿，不自动保存、运行或分析。

历史 Tab 显示本实验的运行、原参数快照、结果状态、双面板科学图和下载；结果缺失仍可查看原始参数。分步分析与重新分析共用目录互斥，正在写入的运行拒绝分析。输入/产物内容指纹变化使缓存失效；重算只归档受影响阶段和后继产物，身份、格式、字段、时间轴或触发无效时，不先移走既有科学结果。后续科学评估失败保留失败阶段记录及已归档结果，禁止执行依赖的绘图阶段。可编辑原图为 [workflow.excalidraw](../lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/workflow.excalidraw)，GUI 同时提供下载。

## 原始格式与离线入口

新采集保存独立 `raw_format_version: 1`，参数仍为 schema v1；两者独立判断。无 raw 标记的旧运行只在身份、`data_type` 和 `measurement_mode: current` 符合已验证历史契约时兼容，不重写历史格式。关键文件为：

```text
experiment_config.yaml
raw/applied_control_waveform.npz        # 含 target_current_a
raw/source_current_coupling_analysis.yaml
raw/source_manifest.yaml               # 理论或闭环来源，旧运行可无新增电流标定副本
raw/scope_capture_*.npz                 # CH3/CH4 原始波形及实际采样率、量程、偏置
results/analysis_inputs.json
results/current_metrics.json
results/current_waveforms.npz
results/current_waveform_similarity.png # 同时输出 PDF
results/current_similarity_report.yaml
results/current_similarity_report.json
```

离线脚本必须显式传入运行目录，缺参报错，不自动选择 latest。测试和科学对照须指向临时运行副本，例如：

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Z_AW_Current_Waveform_Scope_Check_plot.py "C:\Temp\<临时运行副本>"
```

通过 GUI 运行采集时会自动调用离线分析器。分析成功后，运行配置写入
`analysis_status: completed`；如果分析失败，任务会明确标记为失败，并在
`experiment_config.yaml` 中保存 `analysis_status: failed` 和 `analysis_error`，原始采集数据仍保留，可从运行记录中重新分析。
