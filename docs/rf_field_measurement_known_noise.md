---
title: 已知噪声下 RF 灵敏度（DirectAW）
type: RF_Field_Sensitivity_AW_Known_Noise
scan_mode: nested_scan
experiment_id: rf-sensitivity-aw-known-noise
defaults_file: params/experiments/rf-sensitivity-aw-known-noise.yaml
mapping_keys:
  Z_magnetic_field:
    role: scan
  X_magnetic_field:
    role: fixed
  Y_magnetic_field:
    role: fixed
  lockin_r:
    role: detection
required_devices: [GS200, 信号发生器, 锁相放大器]
learned_notes: []
---

# 已知噪声下 RF 灵敏度（DirectAW）

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


每次运行固定一种已知周期伪噪声，选择一个或多个现有最优控制版本，仅测量注入噪声 ON 下的 RF 灵敏度谱。RF 载频在本轮固定，谱的横轴是 RF 解调后噪声时序的傅里叶频率，单位 Hz；灵敏度单位为 fT/√Hz。每个控制版本独立校相、拟合响应斜率、测量噪声 PSD 和频响补偿，不能跨版本复用斜率。

“已知”指所合成的信号源电压谱；线圈处的磁场谱仍需独立频响校准，不用 RF 载频处的换算系数自动推断噪声频带的磁场谱。

这是给定噪声条件下各完整控制方案的性能测量。没有注入 OFF 对照，不能从本实验单独确定噪声造成的退化；各版本控制峰值、周期与均方根可不同，比较结论不等同于控制波形形状的独立因果效应。

当前默认参数见[参数文件](../params/experiments/rf-sensitivity-aw-known-noise.yaml)。运行时参数、设备映射和安全限值以各自 `experiment_config.yaml` 为准，GUI 保存默认值只影响下一次实验。

## 来源、参数与预检

`CONTROL_WAVEFORM_FILES` 从 `oc_sens/<版本>/waveforms/optimal_control_waveform.csv` 选择，允许一个或多个不重复版本；除 `vN` 外也支持自定义命名的控制目录（如中文备注名），目录内参数文件的版本号不要求与目录名一致，缺少参数文件时以波形时间轴的重复频率作为设计 RF 频率。预检核对参数文件、波形周期、所选版本原设计 RF 频率与本轮 `Z_RF_FREQ` 一致，不自动缩放时间或控制幅度。导入、schema、预检和历史浏览均不连接仪器。

| 参数 | 含义与约束 |
| --- | --- |
| `NOISE_BAND_STARTS_HZ` / `NOISE_BAND_STOPS_HZ` | 非空等长谱段边界，位于 Z 任意波 Nyquist 内，谱段不重叠 |
| `NOISE_BAND_PSDS_V2_PER_HZ` | 等长正数数组，规定设计谱形及谱段相对强度 |
| `NOISE_REPEAT_FREQ_HZ` / `NOISE_SEED` | 周期伪噪声重复频率与种子，同轮各控制共用同一噪声实现 |
| `RF_AMP_START` / `RF_AMP_STOP` / `RF_AMP_POINTS` | RF 有符号 Vpp 扫描，其他响应、温控与锁相参数沿用 [DirectAW](rf_field_measurement.md) |

实际噪声幅度不再单独设置：对齐方法与 [XY 已知噪声谱](noise_spectrum_xy_ctrl_known_noise.md)一致，取幅度 `Vpp = 2 × 设计波形峰值`，缩放系数为 1，实际注入谱等于设计谱（V²/Hz）。`raw/noise_waveform.npz` 同时保存实际谱与设计谱供分析核对；设计 PSD 增大时对齐幅度随之增大，仍受 Z 安全限值与仪器 0.001 Vpp 下限约束。

任意波采用固定点数。RF 载频及启用带宽扫描时的全部扫频点必须是噪声重复频率的正整数倍，避免周期截断制造额外频谱；预检拒绝不相容频率，不自动舍入。检查 Z 和 XY 波形点数、幅度下限、总输出极值、当前映射、锁相输入线路与实际选件；辅助输出缺少安全电压范围时禁止启动。

## 接线与逐点归一化

Z 噪声和 RF 共用 `Z_magnetic_field` 的同一物理输出与 Z 线圈，不添加占用同一端口的第二物理量。其余接线沿用 DirectAW：`Time_sequence_2` 主输出分路连接 XY 和 Z 的 Ext Trig，RF 回采选择物理辅助通道或 MFLI 内部 AuxOut。

每点先在物理电压单位求和：`u(t) = n(t) + A_RF/2 × sin(2π f_RF t + φ_RF)`。取 `M = max|u|`，上传 `u/M` 并设置 `Vpp = 2M、offset = 0`；不能只改变整个任意波的 Vpp，否则噪声也会改变。负 RF 幅度仅改变正弦分量的符号。校相相位也仅作用于正弦分量，总波形 Burst 相位固定为零。继承的 `z_burst_phase_deg` 与正负场相位数据键在本实验表示 RF 分量等效相位，不能将它们作为整体任意波的 Burst 相位写入。

每点在开启输出前回读总 Vpp、偏置与重复频率，超出已知噪声实验沿用的回读容差时拒绝采集；请求值与实际回读分别保存。零 RF 工作点上传纯噪声并保持 Z 输出 ON。数字求和与归一化保存完整波形快照；实际输出仍可能受 DAC 量化、输出量程、线圈频响与仪器本底影响，正式结论需要实机确认。

## 分步运行与恢复

运行页采用 DirectAW 的采集、分析两列节点。顶部提供保存参数与布局、只读采集预览、预检、完整运行和任务状态；参数在节点中按基础/高级分类编辑，共享过程参数同步。会话草稿先显示失效范围，应用后需显式重跑，不自动执行前置。

| 节点 | 行为与依赖 |
| --- | --- |
| A01–A07 | 顺序预检、连接、参考时钟同步、初始输出、目标温度设置、Pump 与共同触发 |
| A08 | 关闭控制输出进行 Bell-Bloom / Demod0 校相，依赖 A07 |
| A09 | 噪声参数与控制版本编辑；逐版本上传 XY、迭代校相，依赖 A08 |
| A10–A12 | 逐版本 RF 输入线路准备、RF 正弦项相位扫描与 RF 解调器校相，顺序依赖 A09；测量期间噪声 ON |
| A13 | 各版本独立扫描 RF 幅度并逐点保存，依赖 A12 |
| A14 | 各版本零 RF、噪声 ON 的 X 时序采集，依赖 A13 |
| A13B | 可选带宽扫频，依赖 A14；按所选线宽模式启用 |
| A15 | 恢复响应配置，关闭 XY 与 Z，恢复温控；依赖 A14 |
| A16 | 无前置的安全结束会话 |
| B01–B04 | 独立顺序校验输入、各版本响应拟合、Welch PSD、灵敏度与聚合 RF 响应/灵敏度及 Z RF 相位对比图 |

每个阶段依次激活全部所选版本，恢复该版本自己的 XY 相位和 RF 解调设置；任一校相质量不合格保存诊断并禁止后继。重跑创建新运行目录，仅复制仍有效的前置数据，不覆盖历史原始数据。

等待分步命令时设备连接、输出及全局硬件锁保持。取消、通信 EOF、后台关闭和异常执行安全收尾：关闭 XY 与 Z，温控恢复 5 V DC + ON，保持主场、光功率、Pump 门控和共同触发。清理错误不覆盖原始错误。流程原图：[可编辑 Excalidraw](../lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/RF%20已知噪声灵敏度.excalidraw)。

## 数据与离线分析

```text
data/RF_Field_Sensitivity_AW_Known_Noise/<timestamp>_<tag>/
  experiment_config.yaml
  raw/noise_waveform.npz
  controls/<version>/
    experiment_config.yaml
    raw/                  # 该版本响应、噪声、校相及逐点 Z 合成波形
    results/              # 该版本独立分析结果
  results/                # 聚合摘要、NPZ 与多版本对比 PNG/PDF
```

每个控制子运行保持独立 `raw/` 与 `results/` 契约。实际噪声 PSD、实际采样率、RF 载频与任意波重复频率分别记录；任意波的仪器频率回读不是 RF 载频。

离线入口为 `experiments/RF_Field_Sensitivity_AW_Known_Noise_plot.py <run_dir>`，无需控制波形来源目录或在线仪器。响应斜率、Welch 和频响补偿沿用 DirectAW 算法，逐版本计算后叠加展示，结果采用 `lab_workflows.plotting` 的 `paper` 配置与英文图注。分析重算归档受影响结果，保留仍使用的前置。

离线分析兼容本实验参数 `schema_version` 为 2、3、4 的运行；版本 4 取消手动噪声幅度参数，响应与噪声原始记录仍沿用既有格式。父运行和各控制子运行均核对实验身份、采集方法、采集完成与校相通过状态，分析使用各自保存的参数，不迁移或降级历史配置。

多版本对比图直接用所选控制版本的名称作为图例，支持自定义中文名称。聚合 `full_analysis.png` / `.pdf` 仅展示 RF 响应与灵敏度两幅图，各子运行的噪声 PSD 结果仍保存。离线分析另从各子运行的 A11 `raw/z_phase_scan.npz` 绘制 `z_phase_response_comparison.png` / `.pdf`，按相位排序，在 X、Y、R 三个面板中叠加全部控制版本；不依赖采集阶段已经生成的相位图片。

运行分析结果与历史 Tab 除聚合响应/灵敏度图和 Z RF 相位响应对比图外，按控制版本逐张展示 `controls/<版本>/results/full_analysis.png`：上半幅同时绘制 X、Y、R 响应，叠加 X 色散拟合、斜率和线宽注释；下半幅绘制原始/补偿灵敏度、平坦频带及其中位数。页面按控制版本标注图名，点击图片下载 PNG，旁边提供该版本 PDF 下载，下载文件名包含版本名称；缺失结果不会显示损坏的图片链接。指标、参数快照与诊断仍可查看，支持填回参数和重新分析。具体目录的 Welch 分析草稿与下一次实验参数隔离，保存默认值不写回历史采集配置。单次运行内支持多控制对比。
