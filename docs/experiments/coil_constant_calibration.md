---
title: Z 线圈常数标定
type: Coil_Constant_Calibration
scan_mode: nested_scan
experiment_id: coil-constant-calibration
defaults_file: params/experiments/coil-constant-calibration.yaml
mapping_keys:
  main_magnetic_field:
    role: optional_bias
  Z_magnetic_field:
    role: scan_drive
  coil_drive_amplifier:
    role: power_amplifier
  coil_current_scope:
    role: sense_resistor
  X_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: detection
  temperature:
    role: optional_control
required_devices:
  - GS200
  - DG4000
  - Aigtek 1200B
  - DHO900
  - DG900
  - 锁相放大器
learned_notes:
  - 主磁场手填电流与其拉莫尔频率；Z 电流为 0 处用该频率作锚点，逐档电流按预估线圈常数算出的预测中心做一次扫频。
  - 主磁场开启且足够大时全程不过零，f(I) 单支线性、斜率带符号；主磁场关闭或偏置不足时按 f=|K·(I−I0)| 过零处理。
  - 默认 15.555 mA ≈ 150 kHz（按 9.643 kHz/mA）；±200 mA 时 Z 场约 ±120 kHz，过零在 −249 mA（轴外）。
  - DHO900 只开采样电阻通道、AUTO 触发测直流电压，不需要同步通道与触发电平。
  - 主磁场关闭时只关 GS200 输出、不写电流；开启时只按参数写一次。
  - 本实验只交付电流—场常数；π 工作点由测得的 K 离线换算，不再在本实验里预览。
---

# Z 线圈常数标定

当前默认参数：[参数文件](../../params/experiments/coil-constant-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认值，单次运行参数以运行目录的 `experiment_config.yaml` 为准。

## 目的

`线圈驱动链路电学特性` 测出 `Z_magnetic_field → Aigtek 1200B → 阻尼电阻 → Z 线圈 → 10 Ω 采样电阻 → 功放地` 这条链路的电流传递与脉冲面积之后，本实验在真实原子上标定**电流 → 磁场**常数：固定主磁场（GS200 电流或 OFF），在若干档 Z 线圈直流电流下测 Mx 磁力仪的 Y RF 共振中心，用 `f(I)` 拟合出 `K`（Hz/mA 与 nT/mA）与过零电流 `I0`，并同轮给出该增益下"信号源驱动电压 → 场"的换算。

结果用于把实测 `∫I dt` 换算成脉冲面积：`2π·K·∫I dt`。电压—场常数只在准静态且增益固定时成立，是操作换算而不是可迁移的物理量。

## 接线与映射

| 映射键 | 设备 | 用途 |
|---|---|---|
| `Z_magnetic_field` | DG4000 通道 | 驱动功放输入；DC 0 V 起步，逐电流点写电平 |
| `coil_drive_amplifier` | Aigtek 1200B | 线圈驱动功放；A02 按参数写入增益与输入/输出阻抗 |
| `coil_current_scope` | DHO900 | `SCOPE_CURRENT_CHANNEL` 测采样电阻电压（I = V/R_sense），只开这一路、AUTO 触发 |
| `main_magnetic_field` | GS200 | 主场：设已知电流（同时手填对应拉莫尔频率）或只关输出 |
| `rf_coil` | DG4000 通道 | Y RF 正弦，逐频点换频 |
| `lockin_r` | HF2 / MFLI | Demod R 解调，全部频点只采 R |
| `Pump_laser_power`、`Probe_laser_power`、`Pump_modulation`、`Time_sequence`、`Temp_Switch`、`X_magnetic_field` | 按映射 | 固定光功率、Pump 连续载波与常开门控、温控门控、X 场关闭 |

1200B 输出须 DC 耦合且返回端对地；DHO900 地夹接返回地，直流测量不需要接同步通道。

## 参数

字段、单位与范围以 GUI schema 为准，下表只写含义。

| 键 | 含义 |
|---|---|
| `AMPLIFIER_GAIN_DB`、`AMPLIFIER_INPUT/OUTPUT_IMPEDANCE_OHM`、`AMPLIFIER_WRITE_SETTINGS` | A02 写入 1200B 的增益与阻抗，并把信号源负载配成与输入阻抗一致；写入值就是预算依据，1200B 无回读 |
| `SENSE_RESISTOR_OHM`、`DAMPING_RESISTOR_OHM`、`COIL_RESISTANCE_OHM`、`RESISTOR_RATED_W` | 回路电阻与功耗校验；采样电阻填实测值 |
| `MAIN_FIELD_ENABLED`、`MAIN_FIELD_MA`、`MAIN_FIELD_CENTER_HZ` | 主场两种状态：关闭时只关输出；开启时按 `main_magnetic_field` 限值校验后写电流。`MAIN_FIELD_CENTER_HZ` 是**手填**的该主场电流对应的拉莫尔频率，作为 Z 电流为 0 处的扫描锚点 |
| `CURRENT_START/STOP/STEP_MA`、`BIDIRECTIONAL_SCAN` | 电流轴（含端点、整数步进）；双向时正向到端点后反向返回，不重复端点 |
| `INITIAL_HZ_PER_MA`、`PREDICTION_POLARITY` | 窗口中心的预测斜率与电流极性：`预测中心 = |锚点 + 极性·斜率·I|`，只用于粗扫定位；选反时粗扫峰贴边会明确报错，对称回退仍可救回 |
| `FREQUENCY_HALF_WIDTH_HZ`、`FREQUENCY_STEP_HZ` | 每个电流点的扫频窗（围绕预测中心）；需要粗测时把半宽/步进调大单独跑一轮，再换小窗精测 |
| `NEAR_ZERO_SCAN_FLOOR_HZ` | 预测 |中心| 低于此值的电流点只测电流、不扫 Y RF（主磁场关闭或偏置不足时用） |
| `NEAR_ZERO_FIT_FLOOR_HZ` | 拟合中心绝对值低于此值的曲线剔除，可在分析中按目录覆盖 |
| `Y_RF_AMPLITUDE_VPP`、`FREQUENCY_DURATION_S`、`SYSTEM_SETTLE_S`、`TEMP_SWITCH_ON_LAG_S`、`R_BAD_POINT_STD_THRESHOLD_V`、`R_POINT_MAX_ATTEMPTS` | Y RF 幅度与逐频点采集、温控门控、R 坏点重采 |

## 安全

- `params/safety_limits.yaml` 的 `z_pulse_coil_current` 必须有有限上下界；连接前核对全部计划电流（含反向）、驱动电平（`Z_magnetic_field`）、电阻平均功耗（不超过额定一半）、Y RF、主场、光功率/调制/温度。
- A02 在功放输出关闭、信号源停输出时写增益与阻抗；带载改设置被安全回调拒绝；开输出前重做整次预检。
- A04 的零偏与直流 I–V 只记录 `k`、零偏与线性指标，不设判据、不中止；链路已由 `线圈驱动链路电学特性` 表征。
- 输出写入前仍按 `z_pulse_coil_current` 与 `Z_magnetic_field` 限值校验（A04 实测 `k` 与偏置后预测更准）；运行期不设实测电流判据，实测电流只作为拟合横轴。
- 主磁场关闭时只关 GS200 输出、不写电流；开启时只按参数写一次。收尾保持本次设定（关→关、开→该电流），运行前 GS200 状态记录在 `experiment_config.yaml` 的 `main_field_before`。

## 步骤与采集时序

| 节点 | 职责 |
|---|---|
| A01 | 参数、映射、锁相能力与安全限值预检；主场/锚点与过零几何预览 |
| A02 | 连接设备、写入功放设置、记录 GS200 初态 |
| A03 | 参考时钟同步并严格回读 |
| A04 | 功放零偏（关/开两态）与直流 I–V 多点核对，记录 `k`、`b` 与线性指标（不做判据） |
| A05 | 初始输出：GS200 两模式（写入主场电流或只关输出）、X/Z 辅助场归零、光功率、温控开关 5 V ON |
| A06 | TEC 只下发界面目标温度 |
| A07 | Pump 连续载波与常开门控；Y RF 正弦（初始频率取主场锚点）与 R 解调准备 |
| A08 | 电流轴 × 逐点 Y RF 扫频：每个电流点在按预估线圈常数算出的预测中心扫频 |
| A09 | 安全收尾并封存本轮 |
| A10 | 安全结束会话 |

每个原子采集窗口（采样电阻电压、每个 R 频点）均按普通气室六步顺序执行：设置实际仪器配置 → 关闭 `Temp_Switch`（0 V，输出保持 ON）→ 唯一 `SYSTEM_SETTLE_S` → 读入内存 → 恢复 5 V 与 `TEMP_SWITCH_ON_LAG_S` → 写盘。故障也先恢复温控再抢救数据。TEC 只写目标温度，未知实测温度为 `null`。

每次换电流后有 `CURRENT_SETTLE_TIME_S` 专用等待。每个电流点的流程是：按 A04 记录的 `I = k·V + b` 反算驱动电压 → 门控测一帧采样电阻电压得到**实测电流** → 用实测电流和**预估线圈常数**算出预测中心 `锚点 + 极性·斜率·I` → 在该中心 ±`FREQUENCY_HALF_WIDTH_HZ`、按 `FREQUENCY_STEP_HZ` 做一次 Y RF 扫频。需要先粗测时可把半宽/步进调大单独跑一轮，再换小窗精测。拟合横轴与窗口预测都用实测电流，不用目标电流。

## 过零几何与拟合

- `f(I) = |K·(I − I0)|`；全局加权最小二乘给出 `K`、`I0`、R² 与残差。
- 主磁场开启且足够大时全程不过零：数据落在单支，`geometry.crossing_inside=false`，`K` 由直线斜率给出，同时输出**带符号**的 `K_signed_Hz_per_mA`（正电流升频为正）。
- 过零在电流轴内时（主磁场关闭或偏置不足），以 `I0` 分两支各做加权直线拟合：斜率应为 `+K` 与 `−K`，支间一致性 `|s_left+s_right|/max(|s|)` 与两支交点写入结果；两支各需 `MIN_BRANCH_POINTS` 点。带符号 `K` 取正场侧支路的斜率。
- 过零附近：预测 |中心| 低于 `NEAR_ZERO_SCAN_FLOOR_HZ` 的点只测电流；拟合中心低于 `NEAR_ZERO_FIT_FLOOR_HZ`、粗扫未找到峰或拟合幅度不高于 5 倍噪声的曲线剔除并保留原因。
- 电压—场换算取正场侧支路（单支时为全部点）的 `f–V` 线性拟合；闭合比 `S_f/(1000·K·k)` 应接近 1，用于检查原子标定与电学 I–V 自洽。

## 数据目录与离线分析

```text
data/Coil_Constant_Calibration/MMDD_HHMM_tag/
  experiment_config.yaml
  raw/
    amp_zero_off_NN.npz、amp_zero_on_NN.npz、dc_check_K.npz
    current_scan_index.npz
    current_000/sense_current.npz、frequency_IIII_attempt_JJ.npz、frequency_scan.npz
                  frequency_IIII_attempt_JJ.npz、frequency_scan.npz
  results/
    curve_fits.json、calibration_evaluation.json、calibration_results.npz
    analysis.yaml、analysis.json
    frequency_response_fits.png、coil_constant_calibration.png、coil_voltage_response.png
    coil_constant_residuals.png、dc_iv_check.png
```

采集与离线分析入口分别为 `experiments/Coil_Constant_Calibration.py` 和 `experiments/Coil_Constant_Calibration_plot.py`。分析只接收明确运行目录，不改写 `raw/`；`LINEAR_R_SQUARED_MIN`、`GYROMAGNETIC_RATIO_HZ_PER_NT`、`MIN_ACCEPTED_POINTS`、`MIN_BRANCH_POINTS`、`BRANCH_SLOPE_TOLERANCE`、`NEAR_ZERO_FIT_FLOOR_HZ` 可按目录覆盖。分析完成与质量通过分开记录：`success=false` 时仍导出诊断图与排除原因。

## 已知限制与待查

- 主场电流与锚点频率都要手填，两者必须对应同一实际主场；扫描窗围绕按预估线圈常数算出的中心，预测偏差超过 `FREQUENCY_HALF_WIDTH_HZ` 或极性选反时，失配点会被分析剔除——把半宽调大（或翻转极性）单独跑一轮粗测即可找回。
- 主磁场关闭、残余场较小且 ±200 mA 轴穿过零点时，曲线折叠为 `|K(I−I0)|`，近零区只测电流；若想始终单支，可开主磁场并保证 `|锚点| > 斜率 × |电流上限|`。
- 主线插入 1200B 后，`Z_magnetic_field` 的 V→I 与 `mx-z-field-calibration` 的 `Z_INITIAL_HZ_PER_V`（仅预测参数）不再一致；本实验不改该实验与历史数据。
- 只测 `|K|` 时场的绝对方向是约定；需要双极脉冲或相位门时给一个压过剩磁的已知主场，由“随电流升频的那一支”定符号。
- 1200B 直流 I/V 与正弦值的差异尚未查明原因；本实验以逐点实测电流为准，但直流线性与示波器直流精度仍待真机复核。
