---
title: 原子自旋投影噪声示波器测量
type: Projection_noise
scan_mode: point_by_point
mapping_keys:
  main_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed
  X_magnetic_field:
    role: fixed
  Y_magnetic_field:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  scope_waveform:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Temp_Switch:
    role: temp_gating
  temperature:
    role: fixed
required_devices:
  - GS200
  - DG900
  - DG4000
  - SDS
learned_notes:
  - TEC103 为可选控制设备；串口被占用时跳过设温，普通采集从不启停 TEC 或判稳。
  - XY、Z 辅助场初始化及收尾归零关闭；Pump 载波/门控从初始化到正常、异常、取消与 EOF 收尾均不修改，保留当前状态。
  - PD 原始信号接 SDS CH1，直接在 Larmor 频率附近观察自旋噪声峰。
  - MEASURE_FIELD_OFF_CONTROL 控制是否测量 GS200 输出关闭背景，取值见参数文件。
  - 关闭对照组选项时，直接对 `field_on` PSD 做带常数背景的洛伦兹拟合。
  - 每帧采集期间温控开关为 0 V DC + output ON，帧间恢复 5 V DC + output ON。
experiment_id: projection-noise
defaults_file: params/experiments/projection-noise.yaml
---

# 原子自旋投影噪声示波器测量

## 采集时序与系统稳定

GS200 状态、光功率与 SDS 实际配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集索引和会话快照。目录及初始配置提前保存。可选背景仍为整段 `field_off` 后接整段 `field_on`，不是逐帧 OFF/ON 配对；量程跨阶段继承。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。主场打开后的专用等待在温控开启时完成，温控恢复和 SDS 触发协议等待保留。参数迁移和异常数据保护见[统一采集规范](../acquisition_timing.md)。


当前默认参数：[参数文件](../../params/experiments/projection-noise.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

## 实验入口

- 采集：`experiments/Projection_noise.py`
- 离线分析：`experiments/Projection_noise_plot.py <run_dir>`
- 稳定实验 ID：`projection-noise`
- 运行目录：`data/Projection_noise/<run>/`

该实验不再使用 HF2 DAQ。PD 原始输出接入 SDS CH1，直接采集时域电压并计算 RF PSD，在主场标定预测的 Larmor 频率附近拟合洛伦兹自旋噪声峰。

## 实验条件

- Pump/Probe 光功率与气室温度分别读取 `FIXED_PARAMS` 对应字段，并执行安全校验。
- Pump 为零时输出 OFF，非零时输出 ON。
- SDS 的通道、请求采样率、记录时长、初始量程与帧数读取参数文件的 `SCOPE_*` 和 `ACQ_REPEATS`。
- 每帧由 FTRIG 强制采集，不使用外部触发。
- 无关辅助场：XY、Z 关闭 Burst 和调制，设置为 `0 V DC + Output OFF`。
- **Pump 载波和门控全程不修改**：不连接其专用信号源、不设置其时钟、波形、幅度、Burst、调制或输出开关。所有正常、异常、取消、Ctrl+C、EOF 和后台关闭收尾也不写这两个通道。用户在运行前自行确认其状态；它们仍参与映射占用检查，拒绝与本次受控物理量占用同一通道。
- Pump/Probe 光功率的 DC 控制是独立物理量，仍按界面参数设置并在结束后保持；“不修改载波/门控”不表示忽略光功率参数。

## GUI 分步运行与历史

页面 `/experiments/projection-noise` 保留「运行 / 历史」两个主 Tab。运行页使用采集／分析双列交互流程图，16 个可见参数全部在实际使用节点编辑；保存参数与统一基础／高级分类、预检、完整运行、任务状态及取消统一位于流程容器顶部。完整入口和分步会话调用同一套业务。

| 节点 | 步骤与依赖 | 编辑或产物 |
| --- | --- | --- |
| A01 | 参数、主场标定与映射预检，无前置 | 目标 Larmor 频率、标定来源；反算电流安全，预检不连接 |
| A02 | 连接与 GS200 完整初态回读，依赖 A01 | 运行标签；同 resource 复用连接，不连接专用 Pump 载波/门控设备 |
| A03 | 已连接设备参考时钟严格回读，依赖 A02 | 不设置未连接的 Pump 信号源 |
| A04 | 辅助场归零与光功率准备，依赖 A03 | Pump/Probe 光功率；GS200 暂时 OFF，温控 ON |
| A05 | TEC 目标温度，依赖 A04 | 仅目标温度，串口失败跳过 |
| A06 | SDS FSRate、量程及实际采样资格，依赖 A05 | 请求率、时长、初始量程、Welch 点数与拟合半宽 |
| A07 | 整段可选 OFF 背景后接 ON 自旋噪声，依赖 A06 | 背景开关、帧数、主场等待、系统稳定和温控恢复等待；波形及索引 |
| A08 | 本轮安全恢复与封存，依赖 A07 | GS200 原态、温控、SDS STOP、XY/Z 关闭；载波/门控保持 |
| A09 | 安全结束会话，无前置 | 空闲时手动结束；执行中使用任务取消 |
| B01 | 原始文件、身份与真实采样资格，无前置 | `projection_inputs.json`；目录须停止写入 |
| B02 | Welch、原自由洛伦兹诊断与出图，依赖 B01 | SDS 分析可覆盖 Welch 点数和拟合半宽；旧 HF2 仅兼容 PSD |

勾选不自动补前置；缺前置或质量未通过时拒绝后继。空闲改参先显示“草稿未应用”及预计需重跑范围，应用成功后仍须显式选择重跑。Welch 和拟合半宽在 A06／B02 共用下一次实验的同一键；光功率、温度等初始化条件只在其设置节点编辑。仅预检不使已完成步骤失效。A08 恢复后再次采集必须从 A04 重新准备；每次扫描新建目录，不覆盖上一轮 raw 和配置。

等待下一步仍占用全局硬件锁并保持输出，不是任务已结束。取消、异常、EOF、后台关闭均自动收尾，恢复失败明确报告失败后才释放任务锁。可下载包内可编辑 `workflow.excalidraw`，不另增静态架构 Tab。

分析列默认编辑“下一次实验”，完整运行使用这份设置；具体已结束目录只允许该目录支持的分析覆盖值，并按目录隔离草稿。覆盖修改会使 B01／B02 失效，重新验证样本数及 Nyquist。保存默认值始终只保存下一次实验参数与统一分类，不改历史采集快照。旧 HF2 分支没有当前 SDS 分析覆盖值，沿用旧运行自身的 PSD 设置。

历史 Tab 只读加载本实验列表、该次参数、状态和已有图；填回参数不自动保存或启动。重新分析和分阶段分析共用目录互斥。输入／产物内容指纹变化会使对应阶段失效，重算归档受影响结果，但不会移动仍在使用的 B01 输入或 raw。离线薄入口必须显式给出 `run_dir`，不默认处理 latest。

代码分别位于本实验包的 `preparation.py`、`acquisition.py`、`analysis_steps.py`、`analysis_plotting.py`；`workflow.py`／`analysis.py` 只负责编排，会话协议与 GUI 容器复用现有实现。参数 schema 保持 v6，新增 raw 格式独立为 v1，默认 YAML 和稳定外部键保持。

## 主场标定与可选对照组流程

从 `MAIN_FIELD_CALIBRATION_SOURCE_RUN` 选择的标定运行读取斜率 K 与截距 b，
按 `I = (TARGET_LARMOR_FREQUENCY_HZ - b) / K` 反算主场电流。

预检要求标定运行成功，并对反算电流执行 `main_magnetic_field` 全局安全校验。

GUI 和默认 YAML 中的 `MEASURE_FIELD_OFF_CONTROL` 决定是否采集关闭电流源的对照组；开关状态以参数文件或历史快照为准。

开启对照组时，采集顺序为：

1. `field_off`：GS200 输出 OFF，按 `ACQ_REPEATS` 采集背景噪声。
2. 保持 GS200 输出 OFF，安全设置反算电流。
3. 打开 GS200 输出，按 `MAIN_FIELD_SETTLE_S` 等待。
4. `field_on`：按 `ACQ_REPEATS` 采集包含自旋噪声的 PD 波形。

关闭对照组时，跳过第 1 步，安全设置并打开 GS200 后只采集 `field_on`。无论是否测量对照组，正常、异常、取消和 Ctrl+C 都恢复 GS200 的源模式、电流、输出、量程和限流到实验开始前状态。

## 每帧温控门控

每个候选帧（一次自动量程尝试）执行完整门控，不是每个阶段只切换一次；内部完整帧读数重试及软复位沿用既有 SDS 协议：

1. 将 `Temp_Switch` 设置为 `0 V DC + Output ON`。
2. 按 `SYSTEM_SETTLE_S` 等待。
3. SDS 按 `SCOPE_DURATION` 采集完整波形；整个采集期间保持 `0 V DC + Output ON`。
4. 在 `finally` 中恢复 `5 V DC + Output ON`，即使采集失败或收到取消请求也执行恢复。
5. 恢复后按 `TEMP_SWITCH_ON_SETTLE_S` 等待，再开始下一帧或下一次自动量程尝试。

帧间、阶段切换期间以及实验结束后均保持 `5 V DC + Output ON`。

## 示波器完整帧与自动量程

- 固定采样率存储模式为 `FSRate`，配置后回读实际采样率和实际点数。
- 每帧执行 `RUN → FTRIG 强制提交一帧 → 等待 FTRIG 完成 → STOP → 读取波形`，不要求输入信号满足边沿触发条件。
- 返回点数少于 preamble 声明值、少于 `WELCH_NPERSEG` 或实际时长不足时重试，达到 `SCOPE_RECORD_MAX_ATTEMPTS` 后失败才终止。
- 初始量程与量程上下限读取参数文件中的 `SCOPE_*` 参数。
- `SCOPE_VERTICAL_DIVISIONS` 表示示波器垂直总格数；自动量程单侧范围为 `V/div × SCOPE_VERTICAL_DIVISIONS / 2`。
- 使用波形中心调整 SDS offset；目标 offset 与当前值的差异不超过配置的 offset 容差比例乘单侧显示范围 时直接接受，超过该范围才重新居中。
- 使用相对中心的半峰峰值调整量程；首次波形同时是候选正式帧，量程和 offset 都满足要求时直接保存，否则才按新设置重新采集。每帧最多尝试 `SCOPE_AUTO_RANGE_MAX_ATTEMPTS` 次，并且只保存最终接受的波形。
- 若测量两个阶段，量程和 offset 状态会跨阶段连续继承。

## 原始数据

```text
data/Projection_noise/<run>/
  experiment_config.yaml
  raw/
    scope_acquisition_index.npz
    field_off/                  # 仅 MEASURE_FIELD_OFF_CONTROL=true 时存在
      waveform_0000.npz
      ...
      waveform_NNNN.npz
    field_on/
      waveform_0000.npz
      ...
      waveform_NNNN.npz
  results/
```

波形采用紧凑 ADC NPZ，只保存原始 ADC 码、preamble 与配置快照；分析时无损重建电压和真实时间轴。索引保存阶段、帧号、实际采样率、实际时长、量程、offset、自动量程尝试次数和 GS200 状态。每个接受帧恢复温控后原子保存并更新索引；自动量程后续失败、取消或恢复失败会尽力另存最新已读 `recovered_*.npz`，标为未接受且不加入科学索引。替换失败保留原文件及 `.pending_*.npz`，不以抢救错误盖掉原始异常。

## PSD 与洛伦兹拟合

每帧使用以下 Welch 参数：

- Hann 窗；
- `WELCH_NPERSEG` 指定分段长度；
- 50% overlap；
- constant detrend；
- 单边 PSD density，单位 `V²/Hz`。

开启对照组时，分别平均 `field_off` 和 `field_on` 的 PSD，并计算：

```text
ΔS(f) = S_field_on(f) - S_field_off(f)
```

关闭对照组时，不生成 `field_off/`，直接使用 `S_field_on(f)` 作为拟合输入。结果中的 `control_group_measured` 和 `analysis_mode` 会分别记录是否测量对照组以及使用 `field_on_minus_field_off` 还是 `field_on_only`；图表标签也会据此区分 PSD 差值和主场打开 PSD。

只在预测中心附近、由 `FIT_HALF_WIDTH_HZ` 指定的窗口内拟合：

$$
S_{\mathrm{fit}}(f)=C+\frac{A\gamma^2}{(f-f_0)^2+\gamma^2}.
$$

`A`、`γ`、`f₀` 和 `C` 均由数据决定；`f₀` 仅限制在拟合窗口内，使用稳健损失降低孤立技术尖峰影响。输出：

- 中心频率 `f₀`；
- HWHM `γ` 和 FWHM `2γ`；
- $T_2=1/(2\pi\gamma)$；
- 洛伦兹面积 $\pi A\gamma$；
- 参数不确定度、R² 和残差。

拟合失败时结果明确标记失败并保存诊断，不回退到旧脚本的固定 `328 Hz` 线宽，也不生成理论替代值。新 SDS 运行不计算 κ̃² 或 PNL。本次迁移不改变原稳健损失、初值或成功判据；优化器收敛不等于全局最优或物理模型已验收，尤其高权重数据仍须核对峰位、边界、R² 与残差。

## 分析结果

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](../plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

```text
results/
  psd_spectra.npz
  lorentzian_fit.npz
  lorentzian_fit.csv
  scope_psd_overview.png
  spin_noise_lorentzian_fit.png
  analysis.yaml
  analysis.json
  projection_inputs.json
  projection_analysis.json
```

阶段记录位于运行根目录 `analysis_state.json`；分阶段分析将覆盖值与状态写入该记录，不改采集参数。旧重新分析／自动分析入口沿用共享适配器的 `analysis_status`／`analysis_error` 更新；`parameters` 和 raw 始终不改写，执行完成与拟合质量分别表达。

所有图中坐标轴、图例、标题和注释使用英文。

## 历史数据兼容

若指定运行目录仍包含旧文件：

```text
raw/waveforms_phase1_light.npz
raw/waveforms_thermal.npz
```

离线入口会自动使用 HF2 X/Y 兼容分析分支，输出 `legacy_psd_spectra.npz` 和兼容图；不会把旧基带数据套入 SDS RF 洛伦兹模型。HF2 仅使用该次运行的 PSD 配置，不读取当前默认值。已验证的未标记 SDS 参数 v2–v6 按原帧格式兼容，schema 1 仅识别上述旧 HF2 双文件；未知 raw 标记或未知未标记版本拒绝。离线 SDS 只读取该次运行参数与标定快照，不要求原主场标定来源目录仍然存在；若早期电流键缺少可用于还原频率的快照，则明确拒绝而不猜频率。
