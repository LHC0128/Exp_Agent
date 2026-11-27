---
title: 标准 Mx 射频相位图与噪声路径
type: mx_standard_rf_phase_sensitivity
experiment_id: mx-standard-rf-phase-sensitivity
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/mx-standard-rf-phase-sensitivity.yaml
mapping_keys:
  Z_magnetic_field:
    role: fixed_z_carrier_chain_disabled
  X_magnetic_field:
    role: fixed_x_bias
  Time_sequence_2:
    role: common_trigger
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: mf_mod_carrier_and_demod4_readout
  main_magnetic_field:
    role: calibrated_main_field
  temperature:
    role: optional_tec_target_only
required_devices:
  - MFLI
  - GS200
  - DG4000/DG900
  - TEC103（可选设温）
learned_notes:
  - 11-27 两轮实测（研究目录 1127_140556_mx_std_full2，24 点相图 + 6 段噪声）：标准 Mx 构型下相图是圆，R = 16.424 mV、残差 rms 0.033 mV；逐段切线斜率中位 16.422 mV/rad，与 R 之比 1.000（散布约 ±0.6%）。
  - 同一轮中 theta = atan2(y-y0, x-x0) 与 y/K 两条路径在 3–50 Hz 的相位噪声中位比为 1.0009，即两条路径等价；报告相位噪声取 y 路径，因为它只依赖工作点斜率 K = R|cos phi|。
  - "|dY/dphi| = R|cos phi| 只是切线在 Y 上的投影，不是斜率本身：报告斜率只用弦长按 sin(dphi/2) 反解得到的 |dS/dphi|。"
  - 标准 Mx 不打 Z 脉冲：1200B 功放已物理旁路，Z 信号源输出关闭并回读；噪声必须用相图那条静态解调配置（时间常数 DEMOD_TC、传输率 DEMOD_RATE），切高带宽会把 5 kHz 射频分量原样放过。
  - 主磁场电流只由标定式反算，界面不提供手填电流；改 CAL_K_HZ_PER_MA 或 CAL_B_HZ 会改变反算电流，越界值在预检阶段即被拒绝。
---

# 标准 Mx 射频相位图与噪声路径

## 实验目的

回答两个问题：标准 Mx 构型（不打 Z 脉冲）下射频相位图是否仍为圆、各相位处的切线斜率是否等于半径；
在同一工作点用 theta 与用 y 两种算法求相位噪声谱，结果是否一致。

测量量是 **MFLI Demod 4 的同步 X/Y 直流相量**：整周改变 MF-MOD 载波相移得到相图，在 |cos φ| 最大的
工作点采集时间序列得到相位噪声。实验不校准器件本征噪声，只给出当前装置与读出链的等效输入相位噪声。

## 构型与接线

- **MFLI Signal Output 1 → 串联大电阻 → Y 方向射频线圈**：只输出 MF-MOD 相干载波（载波幅度按峰值写入），
  **PM 上下边带全程关闭**，因此不存在 2° 峰值边带调制的附加频谱成分。
- **MFLI Signal Input 1 ← 探测器**；读出固定 **Demod 4 / Osc 4**（Demod n ↔ Osc n：载波由 Demod 1 / Osc 1 产生，
  上下边带用 Osc 2 / Osc 3 但保持关闭）。
- **GS200（`main_magnetic_field`，电流模式）供常量主场**；运行前状态快照，结束阶段恢复。
- **Z 双脉冲链保持关闭**：`Z_magnetic_field` 通道 burst、调制、SYNC 与输出全部关闭并逐项回读；
  1200B 功放已物理旁路，按下述 `z_drive_amplifier_enabled = False` 处理，默认不建立功放会话。
- **`Time_sequence_2` 提供 5 kHz 方波共同触发**（5 Vpp、2.5 V 偏置、50% 占空比，打开 SYNC），
  属于共享输出，安全收尾时保持。
- **`Temp_Switch` 是温控门控**：关 0 V DC + ON、开 5 V DC + ON，写入后回读；
  TEC 只在勾选下发目标温度时写一次设定，不启停、不判稳。
- 全部时钟设备按设备库同步 10 MHz 外部参考并严格回读；任一设备不符即停止，不自动更换时钟源。

## 参数

参数表以模型 schema 与[默认参数文件](../../params/experiments/mx-standard-rf-phase-sensitivity.yaml)为准，
此处只说明含义与约束（不复制当前默认值）：

| 键 | 含义 | 单位 | 约束 |
| --- | --- | --- | --- |
| `RUN_TAG` | 运行标签，进入运行目录名 | — | 非空、不含路径分隔符 |
| `Y_RF_FREQ_HZ` | MF-MOD 载波频率，同时决定 GS200 电流 | Hz | 1–20 kHz，需能由标定式反算出限内电流 |
| `Y_RF_VPP` | 载波幅度（写入峰值为其一半） | Vpp | 与偏置叠加后须落在 Signal Output 1 ±10 V |
| `Y_BIAS_V` | 载波直流偏置（`sigouts/0/offset`） | V | 同上 |
| `X_BIAS_V` | X 通道静态偏置 | V | 写后回读，未生效即停止 |
| `CAL_K_HZ_PER_MA` | 主磁场标定式斜率 K | Hz/mA | 正有限值 |
| `CAL_B_HZ` | 主磁场标定式截距 b | Hz | 与 K、频率一起决定反算电流 |
| `PHASE_GRID_STEP_DEG` | 整周相位网格步长（点数 = 360/步长） | ° | 5–60，且 360/步长取整、至少 6 点 |
| `POINT_DURATION_S` | 单点 / 细扫点 / 稳定等待读数的采集时长 | s | 0.05–1.0，且与 `DEMOD_RATE` 相乘不少于 256 样本 |
| `NOISE_SEGMENTS` | 噪声分段数（独立温控窗口） | — | 1–60 |
| `NOISE_DURATION_S` | 单段噪声采集时长 | s | 1–60，且不小于 `WELCH_SEGMENT_S` |
| `WELCH_SEGMENT_S` | 离线 Welch 分段时长 | s | 1–10 |
| `DEMOD_IDX` | 读出解调器索引（固定 Demod 4） | — | 只读，标准 Mx 只能用索引 3 |
| `DEMOD_SIGNAL_RANGE` | MFLI 输入量程 | V | 0.001–3 |
| `DEMOD_ORDER` | 解调滤波阶数 | — | 1–8 |
| `DEMOD_TC` | 读出时间常数（决定分辨率带宽） | s | 1e-6–0.1；相图、细扫与噪声共用同一静态配置 |
| `DEMOD_RATE` | Demod 4 连续流传输率 | Sa/s | 1000–20000 |
| `SYSTEM_SETTLE_S` | 关温控后的系统稳定等待 | s | 0.1–4 |
| `TEMP_SWITCH_ON_LAG_S` | 每次恢复温控后的固定等待 | s | 0.1–30 |
| `SET_TEC_TARGET` / `TARGET_TEMPERATURE_C` | 是否下发 TEC 目标温度与目标值 | °C | 20–130，仅在本项启用时校验 |

界面按基础/高级分组；参数编辑归属与失效范围由 `stages.py` 的清单声明。

## 主磁场标定反算

GS200 电流**只有一条来路**——主磁场标定式反解，界面不提供手填电流键：

```text
I_mA = (Y_RF_FREQ_HZ - CAL_B_HZ) / CAL_K_HZ_PER_MA
```

反算值先按 `params/safety_limits.yaml` 的 `main_magnetic_field` 校验（预检一次、写入前再一次），
写入后按 1% 容差回读电流与输出状态；回读不符即停止，不保留半开的主场。
11-27 主磁场线圈标定给出 K_f = 11089.0 ± 9.8 Hz/mA（R² = 0.999996）与 f0 = 219.7 Hz，
作为本实验的默认 K、b；换样品或换映射后如重新标定，改这两个键即可，无需改代码。

## 采集流程（A01–A06）

- **A01 参数、映射与安全预检**：不连接硬件。校验参数字段、映射设备类型、
  载波包络（含偏置）、X 偏置、共同触发电平、温控电平、反算主场电流与 Demod 4 索引。
- **A02 连接设备、时钟、主场与载波准备**：连接信号源、MFLI、GS200、可选 TEC；同步 10 MHz 并回读；
  温控 ON 并回读；写 X 偏置并回读；配置共同触发方波与 SYNC 并回读；关闭 Z 双脉冲链并回读；
  配置 Demod 4 静态读出（量程、振荡器、时间常数、传输率、相位 0°）；启动 MF-MOD 载波并全节点回读；
  最后按标定式设 GS200 并回读。顺序保证：载波与读出先就绪，主场最后建立。
- **A03 整周相位扫描与圆拟合**：先等原子响应稳定（相邻两次直流相量差不超过 0.1 mV，超时带读数继续并记录），
  在首相位采一段**丢弃做预热**；再按整周网格逐点改写载波相移并采 XY；**末尾回采首点**给出整圈漂移；
  用代数最小二乘圆拟合给出圆心、半径与残差，并算逐段切线斜率 |dS/dphi| 与 |dY/dphi|。
- **A04 工作点选择与细扫**：按圆模型取 |dY/dphi| = R|cos φ| 最大的相图点作细扫中心，
  在该点 ±5°、步长 1° 细扫；用各点**实测相位角**线性回归 dY/dphi，与 R|cos φ| 互校后把载波停在
  最接近切向的点上，作为最终工作点。
- **A05 噪声分段与安全封存**：沿用 A03/A04 的静态解调配置（不切高带宽），按 `NOISE_SEGMENTS` ×
  `NOISE_DURATION_S` 分段采集；每段独立进出温控窗口，恢复温控并等待后才写盘；本轮收尾成功才封存。
- **A06 安全结束会话**：关闭并清零 MFLI Out1 与 MF-MOD、Z/X 通道归零回读、温控恢复 5 V DC + ON、
  GS200 恢复运行前状态、停解调传输并断开设备；共同触发保持。取消、异常与正常结束走同一套收尾，
  收尾失败不记为安全完成，也不覆盖原始采集错误。

## 帧体检（硬性）

每帧写盘前计算直流幅度与 100 Hz 以上最强单音（阈值是模块级参数化常量）：

- 单音幅度超过直流的 20% → 该帧不是滤波后的解调直流，抛错且**不写盘**；
- 标准差超过直流的 5 倍 → 该帧被宽带分量主导，抛错且不写盘；
- 直流幅度低于 0.5 mV → 抛错。

只查 100 Hz 以上单音：低频涨落就是待测噪声本身，不能当单音剔除。同一判据在离线分析里对全部帧复检。

## 数据与产物

```text
data/MxStandardRfPhaseSensitivity/<MMDD_HHMMSS_tag>/
  experiment_config.yaml      # 参数、映射与设备库快照、分步状态、相位/工作点/噪声记录
  raw/mx_std_settle_NN.npz    # 稳定等待读数
  raw/mx_std_warmup_00.npz    # 预热帧（保存但丢弃，不进相图点清单）
  raw/mx_std_coarse_NN.npz    # 整周相图点
  raw/mx_std_repeat_00.npz    # 回采首点
  raw/mx_std_fine_NN.npz      # 工作点细扫点
  raw/mx_std_noise_NN_xy.npz  # 噪声分段
  results/summary.json        # 圆拟合、逐段斜率、工作点、三条噪声路径与结论句
  results/phase_diagram.png|.pdf
  results/noise_paths.png|.pdf
```

原始帧格式为 `raw_format_version = 1`：数组 `time_s`/`x_v`/`y_v`，元数据含 `rate_sa_s`、
`time_source = module_timestamp`、`lockin` 回读、帧标签与采集时的指令相位。参数版本 `schema_version`
与原始格式版本相互独立；参数升级不改写历史原始文件。

## 离线分析

`python -m` 入口读本轮 `experiment_config.yaml`（拒绝仍在写入的目录）与全部原始帧，逐帧复检后：

- 圆拟合：圆心、半径、残差 rms 与最大残差；
- 逐段切线斜率：相邻相量按实测相位角差分，弦长按 `2R sin(dphi/2)` 反解；同时给出段中心角、
  段步长、`accepted` 标记（偏离中位步长超过容差的段被排除）与散布；
- 工作点：细扫回归的局部斜率与圆模型 `R|cos φ|` 并列；噪声帧自身相位角给出噪声工作点；
- 三条相位噪声路径：`theta = atan2(y-y0, x-x0)`、`y/K`、沿切向投影；输出 3–50 Hz 中位 ASD
  与 theta/y 比值，工频单列；
- 两条结论句：相图是否圆、切线斜率是否等于半径；theta 与 y 两条路径是否一致。

图中文字用英文，样式复用 `lab_workflows.plotting` 的 paper 配置。

## 已知结论（11-27 两轮实测）

- **相图是圆**：R = 16.424 mV，残差 rms 0.033 mV；逐段切线斜率中位 16.422 mV/rad，
  与 R 之比 1.000、散布约 ±0.6%，即圆上切线斜率等于半径。
- **|dY/dphi| 不是斜率**：它等于 R|cos φ|，只在 φ = 0°/180° 处取到 R；报告斜率只用 |dS/dphi|。
- **theta 与 y 等价**：两条路径在 3–50 Hz 的相位噪声中位比 1.0009（本轮判据为 |比值 − 1| ≤ 0.1）；
  报告相位噪声取 y 路径，因为它只依赖工作点斜率 K = R|cos φ|，不依赖圆心估计的偏置。
- 高带宽解调（微秒级时间常数）会把 5 kHz 射频分量原样放过，噪声谱被单音主导，因此噪声段沿用相图的
  静态配置，不做动态切换。

## 安全收尾与边界

- 输出量写入前按 `params/safety_limits.yaml` 校验（`main_magnetic_field`、`lockin_r` 的 Signal Output 1、
  `X_magnetic_field`、`Temp_Switch`、`Time_sequence_2`、可选 `temperature`）。
- 生命周期用 `try/finally`：取消、异常与正常结束都进入同一安全收尾，
  复用 `lab_workflows/steps/safety_shutdown.py` 的 `run_safety_shutdown` 与共享 `set_temperature_switch`；
  温控开关恢复失败不记为安全完成，也不覆盖原始采集错误。
- 本轮目录只写一次：重复运行会新建目录，不与旧帧拼接；A03–A05 必须来自同一轮参数快照。

## 运行与历史

运行页提供 `A01–A06` 六个节点与 `B01` 独立分析节点：参数在节点内按基础/高级编辑，顶部提供保存默认值、
预检与完整运行；共享参数同步编辑，改参数会按清单声明失效后继节点，草稿需显式重跑。
历史页只读已结束的运行目录，按 `experiment_config.yaml` 的参数快照与 `raw_format_version` 判定资格；
单次运行的真实参数以该目录的 `experiment_config.yaml` 为准。界面接线由 GUI 侧维护。
