---
title: XY 控制 Demod3 R 噪声谱
type: Noise_Spectrum_XY_Demod3_R
execution_mode: typed_workflow
scan_mode: nested_scan
experiment_id: noise-spectrum-xy-demod3-r
defaults_file: params/experiments/noise-spectrum-xy-demod3-r.yaml
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  X_magnetic_field_AM:
    role: fixed
  Y_magnetic_field_AM:
    role: fixed
  lockin_r:
    role: detection
  Temp_Switch:
    role: fixed
required_devices:
  - GS200
  - DG4000
  - DG900
  - HF2
learned_notes:
  - Demod0 通过所选 AuxOut 和物理线缆接入第二路主电压输入，再由独立 Demod3 解调；默认来源为 Demod0 Y。
  - 只保存 sample.r 的算术平均值，不保存连续采样数组；R 的单位为 V，不是 PSD。
  - 内层按温控恢复批次分组，包含不足一组的末组；每批恢复温控后才写盘。
  - TEC 可连接时仅写界面目标温度，不启停或判稳；串口不可用告警跳过。
---

# XY 控制 Demod3 R 噪声谱

## 离线模块与运行目录

本实验清单显式声明 `run_step_id=A11`、`finish_step_id=A12`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.noise_spectrum_xy_demod3_r.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## 目的与入口

本实验是 `noise-spectrum-xy` 的独立硬件变体。原实验采集 Demod0 Y 时域波形并计算
Welch PSD；本实验通过物理回环，在不同 DirectAW 控制包络下扫描 Demod3 解调频率，
记录二维平均 R 矩阵。**平均 R 是电压幅值，不是 V²/Hz，不输出 `S_beta` 或 `N_S1`。**

- GUI：`/experiments/noise-spectrum-xy-demod3-r`，运行 / 历史双 Tab。
- 采集：`experiments/Noise_Spectrum_XY_Demod3_R.py`。
- 离线分析：`experiments/Noise_Spectrum_XY_Demod3_R_plot.py <run_dir>`，必须显式给出目录，不选择 latest。
- 当前默认值只来自[参数文件](../../params/experiments/noise-spectrum-xy-demod3-r.yaml)；历史运行以自身的 `experiment_config.yaml` 为准，迁移不改用户默认参数。

## 设备、接线与安全预检

```text
Demod0 Y → AuxOut2 → physical cable → Signal Input 2 DC → Demod3 sample.r
```

以上为默认接线，实际索引、AuxOut 信号选择、比例及偏置以本轮参数快照为准。
AuxOut 的来源解调器必须与配置的 Demod0 一致；Demod3 与 Demod0 使用独立的解调器和
振荡器，Demod3 输入选择必须与第二路主电压输入一致，不自动改接线。

双主电压回采当前仅支持 HF2；设备库选择 MFLI 时，预检明确拒绝，不自动降频、换输入或
回退仪器。物理线缆不能由软件预检证明，启动前须人工核对。仪器通过当前映射和工厂创建，
信号源按物理资源复用，通道号不写死；同次独立物理量占用同一端点时拒绝启动。

连接后统一关闭主电压输入的 AC 耦合与 50 Ω，使用高阻和 DC 耦合；旧键仍可读取，但归一化为
关闭值。参考时钟按设备库配置同步并严格回读，失败停止。锁相采集模块沿用设备库设置，
HF2 自动模式使用 DAQ；模块、最终实际率和实际时长由采集元数据记录，此规则不解除 MFLI 禁止启动限制。

所有物理输出在写入前校验安全限值。AuxOut 复用公共锁相安全接口，必须有设备专属的明确
电压上下限，按来源输入量程及比例/偏置检查预计总电压；缺限值或越界时禁止写入。
K/B 标定反算包络必须处于有效范围和 XY 安全范围。AW 满量程逐点自动设置为
`2 × |V_env|`，偏置为零；零包络上传全零波形并保持 XY 输出 OFF，过小非零包络拒绝。

## 可勾选的采集流程

| 节点 | 实际职责 | 依赖 |
| --- | --- | --- |
| A01 | 无硬件参数、映射、型号能力及安全预检 | 无 |
| A02 | 连接复用与锁相输入初始化 | A01 |
| A03 | 所有已连接时钟设备同步与严格回读 | A02 |
| A04 | 主场电流、Pump/Probe 光功率、辅助场与温控初态 | A03 |
| A05 | TEC 仅下发 UI 目标温度 | A04 |
| A06 | Pump 载波/脉冲门控及两路连续方波外触发 | A05 |
| A07 | Demod0 配置及共享自动校相 | A06 |
| A08 | AuxOut 安全配置与第二路主输入回环 | A07 |
| A09 | DirectAW 整体相位反馈校准 | A08 |
| A10 | Demod3 连续 R 采集配置与实际速率/设置快照 | A09 |
| A11 | 二维批次扫描、增量保存及本轮安全收尾 | A10 |
| A12 | 无前置的显式安全结束、断开 TEC 并释放会话 | 无 |

两步自动校相沿用公共算法，不增加相位扫描。未收敛或信号不足时保存可用诊断并阻断后继。
A11 外层按控制频率升序，K/B 反算 DirectAW 包络；每个外层点的内层 Demod3 轴始终升序，
不改为蛇形。每点时间常数仍为 `max(DEMOD3_TC_MIN_S, DEMOD3_TC_PERIOD_FRACTION / f)`。
连续 `sample.r` 在内存求均值，不保存其时序数组。普通单点读取失败留 NaN 和错误记录、
继续后续点；取消与中断不作为坏点吞掉。

### 采集时序与故障抢救

每批第一点的实际配置在温控开启时完成，然后关闭 `Temp_Switch`，等待唯一的
`SYSTEM_SETTLE_S`，将数据采到内存。组内换频后重新等待；当前批次结束后先恢复温控，
再保存均值矩阵、采集元数据和扫描快照，最后按 `TEMP_RECOVERY_TIME_S` 等待恢复。
批次大小由 `TEMP_RECOVERY_BATCH_POINTS` 决定，末组不足时仍独立恢复/保存。

目录和初始配置在采集窗口之前创建。关闭温控期间不写状态文件、配置或分析结果。
TEC 只下发目标温度，实际温度未知记 null，不进行启停或温度判稳。
理论时长为总点数乘稳定等待与采集时长，再加总批次数乘恢复时间，不包含校相、通信和磁盘耗时。

取消、组内后续配置失败或温控恢复失败时，先尝试恢复，再抢救本批已读均值，并保留原始异常。
矩阵经临时文件原子替换，磁盘失败不会截断上一份完整矩阵；若主文件替换失败，另存
`raw/demod3_r_mean_matrix_recovered.npz`，分析优先读取这一明确抢救文件。不承诺在磁盘完全不可写时仍能保存。
统一规则见[采集时序说明](../acquisition_timing.md)。

### 参数、草稿和会话

保存参数与基础/高级分类布局、派生预览、预检、完整运行、取消和任务日志均位于流程容器顶部。
60 个可见参数在使用节点内编辑，高级项折叠；主场/光功率等初始化条件只在设置节点展示。
`SYSTEM_SETTLE_S` 在 A07/A09/A11 共用同一份值，波形参数在校相与扫描节点共享。
展示归属与失效起点独立，修改后预览实际需要重跑的已完成节点。

分步会话须先“应用参数”，再显式选择并重跑受影响节点；应用、预检或保存默认值都不启动仪器步骤，
草稿不改变已执行记录或收尾策略。缺前置时明确拒绝，不自动补选。
等待下一步仍保持设备会话并占用全局硬件锁，刷新可恢复；后端重启不能凭磁盘状态恢复硬件。

每轮扫描新建目录，不覆盖旧 raw。A11 收尾后，XY 和外触发模式已改变，再扫描必须重跑准备和校相；
A12 无前置结束。执行中使用任务取消，空闲时手动结束；异常、取消、stdin EOF 或后台关闭均先安全收尾再释放锁。

## 数据、独立分析与历史

```text
data/Noise_Spectrum_XY_Demod3_R/MMDD_HHMMSS_demod3_r/
  experiment_config.yaml
  acquisition_state.json
  raw/
    demod3_r_mean_matrix.npz
  analysis_state.json
  results/
    demod3_r_matrix.npz
    calibration.npz
    r_fit_params.npz
    r_spectra.npz
    demod3_r_matrix.png / .pdf
    calibration.png / .pdf
    r_spectra.png / .pdf
    analysis.yaml
    analysis.json
```

原矩阵四键保持 `r_mean_V`、`control_frequency_Hz`、`xy_envelope_voltage_V` 和
`demod3_frequency_Hz`。参数 schema 继续为 v2，新原始格式独立标记 `raw_format_version: 1`；
未标记 raw 版本的原 schema v1/v2 四键文件按已发布结构兼容，不把未知格式默认为 v1，不重写历史快照。

| 节点 | 科学职责与产物 | 依赖 |
| --- | --- | --- |
| B01 | 检查身份、格式、矩阵形状和有限递增轴，导出 `demod3_r_matrix.npz` | 无 |
| B02 | 原 R 脊线稳健线性标定、逐列洛伦兹拟合，导出标定/拟合参数/掩码 | B01 |
| B03 | 原矩阵图、标定图、四参数曲线和原 JSON/YAML 报告 | B02 |

至少六个脊线标定内点才能进入拟合；历史中的非有限 R 继续按原有限值掩码排除，不阻断其他有效列。单列样本不足或拟合失败按原规则保留 NaN 和 `fit_mask`，
不通过插值伪造有效结果。原四参数 `gamma_Hz`、`fit_amplitude`、`r_baseline_V`、
`frequency_offset_Hz` 及原科学图保持。分析完成与列拟合是否有效分别表达。

分析列可选择“下一次实验”或具体已结束目录。此实验没有额外分析覆盖参数；具体目录只使用其
采集快照与 raw，切换目录不改变下一次实验参数。运行中的目录禁止分析；完整分析与分阶段分析
使用相同目录互斥。内容指纹检查输入与产物，失效结果不能当作前置；重算只归档受影响节点和后继，
不会移动仍使用的 B01 输入，坏 raw 在移动旧产物前拒绝。

历史 Tab 提供本实验列表、参数快照、状态、图片和文件下载、填回及重新分析。
读取和填回不改历史配置，不自动运行或保存默认值；保存默认值只保存下一次实验参数和统一分类。
旧参数迁移复用模型，显式 `SYSTEM_SETTLE_S` 优先于旧切频等待键。

## 结束输出与验证边界

正常、异常、取消和通信结束均恢复温控为 5 V DC + ON，归零关闭 X/Y DirectAW、Z 场和两路非时序外触发。
`Time_sequence`、`Time_sequence_2` 和 Pump 门控保持当时状态；主磁场、Pump/Probe 光功率、Pump 载波及锁相配置保持。
正常结束只断开 TEC，不能以断开通信代替关闭危险输出。收尾失败不得报告安全完成。

验证使用假仪器、显式临时运行目录和 mock API；当前检出没有此实验真实历史运行，历史资格与数值兼容由
合成的原四键格式及原分析器对照验证，不宣称完成真实历史或硬件验收。物理回环、实际触发、时钟锁定、
温漂及绝对增益仍须用户实机确认。后端无热重载，新增接口须安全结束原有会话后由用户自行重启加载。
