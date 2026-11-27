# 研究脚本（scripts/）

存放“新数据来了还会再跑一次”的分析与对比脚本，以及自带结论的研究记录。这里的代码进 git、
可被检索，但不被任何模块导入、不在 pytest `testpaths` 内、注册表不认识它；只受
`python -m tools.check` 的语法编译与本索引校验约束。

## 三档存放

| 档位 | 位置 | git | 判据 |
|---|---|---|---|
| 一次性 | `.cache/` | 否 | 只为这一次观察服务，明天不需要重跑 |
| 研究记录 | `scripts/<data_type>/` | 是 | 新数据来了会再跑一次对比或分析 |
| 正式 | `lab_workflows/experiment_modules/<模块>/` 等 | 是（含测试/GUI） | 给别人用、进论文、进正式实验流程 |

判定顺序：还会看第二遍吗？不会 → `.cache/`。会看但不会重跑 → 结论写进对应研究 README，删脚本。
会重跑 → `scripts/`。别人也要用或要进论文 → 升正式档（见 AGENTS.md「探索档与正式档」）。

动手写新分析前先查下方索引：同一问题已有脚本就改它，不另写一份。运行目录 `data/` 下不放代码。

## 目录约定

```
scripts/
  README.md                       # 本文件；索引块由 python -m tools.gen_script_index 生成
  <data_type>/                    # 等于注册表 data_type，即运行目录 data/ 下的顶层目录名
    <名称>.py                     # 单文件脚本：模块 docstring 首行写它回答的问题
    <YYYYMMDD>_<名称>/            # 研究目录：README.md + 脚本 + 结论图
  _shared/                        # 跨实验对比
  _devices/                       # 仪器探针
```

研究目录的 `README.md` 以 YAML frontmatter 开头（生成器据此出索引）：

```markdown
---
title: π 宽度调查
conclusion: 一句话结论
data:                             # 读取的运行目录，相对数据根
  - pi_width_scan/1117_175856_pi_width_scan
---
```

正文写：来源运行、各脚本回答的问题、关键结论、异常与待查、复现命令。

## 内容拆分

- 进 git：代码、README、少量结论图（单个研究目录的非代码文件不超过 3 MB，超出生成器警告）。
- 留数据目录：`.npz`、原始波形、中间过程图，放 `data/<data_type>/_studies/<研究目录名>/`，README 写明位置。
- 子目录不要命名为 `data/`、`results/`、`tests/`：它们被根 `.gitignore` 忽略，`--check` 会报错。
  输出图用 `figures/`。

## 运行

输入路径一律经 `lab_workflows.common.data_root()`，不写死 `D:\...`。开发目录运行前设数据根：

```powershell
$env:LAB_DATA_ROOT = "D:\Code\exp_agent_run\data"
agent_exp_env\Scripts\python.exe scripts\<data_type>\<脚本>.py
```

索引里标“需硬件”的条目会连接真实仪器，只能在运行目录执行，并且同样守 AGENTS.md「硬件安全红线」：
输出限值校验、`try/finally` 安全收尾、关温控窗口内不写盘；运行前逐次征得授权。

## 索引

<!-- BEGIN GENERATED SCRIPT INDEX: 由 python -m tools.gen_script_index 生成，勿手改 -->

| 分组 | 条目 | 结论 / 用途 | 读取数据（相对数据根） | 硬件 |
|---|---|---|---|---|
| _devices | [gs200_wangwan_probe.py](_devices/gs200_wangwan_probe.py) | 网络 GS200（王湾主场，192.168.124.41）只读探测：连接、IDN 与当前状态。 | — | 需硬件 |
| _devices | [main_field_and_z_state_probe.py](_devices/main_field_and_z_state_probe.py) | 只读核对（在运行目录执行）：王湾主磁场 GS200（192.168.124.41）与 Z 通道 DG4000（192.168.124.33）当前状态。 | — | 需硬件 |
| _devices | [scope_frozen_capture_example.py](_devices/scope_frozen_capture_example.py) | 示波器冻结读帧示例：按面板已设好的时基/深度取一帧，不写采样率；顺带实测单帧耗时并校核温控窗口。 | — | 需硬件 |
| _devices | [z_chain_off_minimal.py](_devices/z_chain_off_minimal.py) | 只关 Z 脉冲链（1200B 输出 + Z 通道输出），不连接锁相/温控/主磁场。 | — | 需硬件 |
| _devices | [z_pulse_on_hold.py](_devices/z_pulse_on_hold.py) | 把 Z 脉冲控制场打开并保持（供示波器观察）；--off 关闭并恢复。 | — | — |
| Coil_Constant_Calibration | [20261116_coil_constant_diagnostics](Coil_Constant_Calibration/20261116_coil_constant_diagnostics/README.md) | K=653.73±2.44 Hz/mA（93.39 nT/mA，I0=−2.03 mA），三轮复现差 0.02%；Y RF 幅度只加宽不移动线心；驱动链电流上限约 220 mA，K 只在 \|I\|≤200 mA 内有效 | `Coil_Constant_Calibration/1116_161937_coil_const_coarse3`<br>`Coil_Constant_Calibration/1116_165757_coil_amp050`<br>`Coil_Constant_Calibration/1116_170645_coil_amp500b`<br>`Coil_Constant_Calibration/1116_173127_coil_const_fine`<br>`Coil_Constant_Calibration/1116_181213_coil_const_fine`<br>`Coil_Constant_Calibration/1116_185249_coil_const_300` | — |
| coil_drive_characterization | [20261114_coil_drive_analysis](coil_drive_characterization/20261114_coil_drive_analysis/README.md) | 固定延迟 δ≈−0.21 µs、电感 τ≈0.57 µs（L≈41 µH）；310 kHz 为无源并联谐振（Q≈9），更可能在功放输出网络或电缆上 | `coil_drive_characterization/1114_114241_coil_drive`<br>`coil_drive_characterization/1114_165713_coil_drive`<br>`coil_drive_characterization/1114_170336_coil_drive`<br>`coil_drive_characterization/1114_171514_coil_drive` | — |
| mx_standard_rf_phase_sensitivity | [20261202_theta_condition](mx_standard_rf_phase_sensitivity/20261202_theta_condition/README.md) | 相位噪声的两条口径 θ=atan2(y−y0, x−x0) 与 y/K 只在解调输出里没有比圆半径更大的寄生旋转时才等价。窄带静态档（TC 1 ms）下硬件低通已滤掉 −5 kHz 载波镜像，两路 3-50 Hz 中位比 1.0009；宽带档（TC 1 µs）下该镜像幅值 0.68 V、是圆半径 13.9 mV 的 49 倍，θ 被拖成整周缠绕，比值掉到 0.0022、θ 读数只剩 0.93 µrad/√Hz 的伪迹；对 x、y 先做保留直流 ≤1.2 kHz 低通再取 θ，帧内散布 1.81 → 0.017 rad，比值恢复到 1.0012。因此测量口径固定 y 走线，θ 只作诊断。 | `mx_standard_rf_phase_sensitivity/1127_174802_mx_std_smoke_v2c（0.5 Vpp，宽带档 TC 1 µs、53571 Sa/s，24 点相图 + 4 段噪声）` | — |
| Noise_Spectrum_XY_Ctrl | [peak_diagnosis_0921_1758.py](Noise_Spectrum_XY_Ctrl/peak_diagnosis_0921_1758.py) | Noise_Spectrum_XY_Ctrl 0921_1758 运行的峰脊独立诊断试算；不覆盖正式结果，不修改采集 K/B。 | — | — |
| pi_width_scan | [20261118_pi_width_investigation](pi_width_scan/20261118_pi_width_investigation/README.md) | 原子处 Z 脉冲场约 54% 瞬时到达、约 46% 以 τe≈47 µs 延迟到达，工作 π 宽度由电学 5.12 µs 移到 6.5–6.7 µs（190 mA）；慢拖尾与静态横场产生的 c0 使相位扫描椭圆偏离原点 | `pi_width_scan/1117_175856_pi_width_scan`<br>`pi_width_scan/1118_*（逐条运行见 01_acquisition/runs_index.csv）`<br>`pi_width_scan/_phase_background_diagnostic`<br>`scope_px_snapshot/1118_135547`<br>`bperp_null/1118_191524_bperp_null` | 需硬件 |
| pi_width_scan | [phase_background_three_component_split.py](pi_width_scan/phase_background_three_component_split.py) | 把 11-17 相位扫描（5.0 µs、190 mA、Y RF 5 kHz 0.2 Vpp）分解成 c0、c+1、c-1 三条独立曲线。 | — | — |
| RF_Control_Scale_Sensitivity | [analyze_noise_records.py](RF_Control_Scale_Sensitivity/analyze_noise_records.py) | 区分整组灵敏度偏差与少数高噪声记录拉高平均 PSD（正式模块 noise_diagnostics 的命令行薄入口）。 | — | — |
| RF_Field_Sensitivity_AW | [20261017_paper_comparison](RF_Field_Sensitivity_AW/20261017_paper_comparison/README.md) | 1013_224833（常数）与 1013_231737（优化）两次运行：零场斜率提高 59.4%，3–150 Hz 共同频带灵敏度降低 23.3% | `RF_Field_Sensitivity_AW/1013_224833_rf_sens_aw`<br>`RF_Field_Sensitivity_AW/1013_231737_rf_sens_aw` | — |
| rf_phase_response | [20261119_pulse_params](rf_phase_response/20261119_pulse_params/README.md) | 宽度决定翻转角，\|c−1\| 在 8.03 µs（≈π）附近最大，4/12 µs 明显不足或过量；拖尾 s_f 主要移动中心 \|c0\| 而对 \|c±1\| 影响小；τe 与沿宽是弱参数；零场最大斜率 0.274→1.29 mV/deg | `rf_phase_response/1119_092825_rf_phase_response（基线）`<br>`rf_phase_response/1119_1039*_w4 … 1119_1104*_edge15（14 条件）`<br>`rf_phase_response/1119_1251*_w6r、1119_1253*_w10r（宽度复跑）` | — |
| rf_phase_response | [20261119_pulse_params_at_z06](rf_phase_response/20261119_pulse_params_at_z06/README.md) | 到 Z=−0.06 mA 上重测后，宽度的结论不变（8.03 µs 同时给 \|c0\| 最小与斜率最大），但拖尾的作用反了——它主要压 \|c0\|（10.0→3.0 mV）而斜率几乎不动；s_f 与 τe 叠加能把 \|c0\| 压到 3–4 mV，峰值响应在 (s_f=0.65, τe=45) 为 2.078 mV/deg | `rf_phase_response/1119_1521*_z06w4 … 1119_1546*_z06e15（14 条件）`<br>`rf_phase_response/1119_1614*_g065t45 … 1119_1700*_g085t65（25 点网格）`<br>`rf_phase_response/1119_1722*_h055t35 … 1119_1751*_h075t55（16 个新点，另 9 点复用）` | — |
| rf_phase_response | [20261119_response_optimization](rf_phase_response/20261119_response_optimization/README.md) | 逐步把斜率从 2.078 抬到 2.869±0.005 mV/deg（N_REPEAT=3）：Z 峰位 −0.0681 mA、宽度 8.03 µs、拖尾 0.575/38.3 µs；X/Y 归零未验证（验证运行遇到 Heat_Control 与 Temp_Switch 连接中断） | `rf_phase_response/1119_18* / 1119_19* / 1119_20* / 1119_21*（各阶段逐条见 数据来源.md）` | — |
| rf_phase_response | [20261119_xy_field_scan](rf_phase_response/20261119_xy_field_scan/README.md) | X/Y 静态偏置在 ±50 mV 内只平移中心 c0（X 约 +31.5 mV/V、Y 约 −143.4 mV/V），\|c±1\| 与最佳相位几乎不动；Y 的耦合约为 X 的 4.6 倍 | `rf_phase_response/1119_1254*_xm*/ym*/x0*/y0*（12 个偏置运行）`<br>`rf_phase_response/1119_092825_rf_phase_response（基线）` | — |
| rf_phase_response | [20261119_z_field_scan](rf_phase_response/20261119_z_field_scan/README.md) | Z 主磁场在 −0.07 mA 附近有尖锐共振，\|c0\|、\|c±1\| 与切线斜率一起放大（峰顶斜率约 2.0 mV/deg，是零场的 7 倍）；\|c0\| 峰在 −0.08 mA、\|c±1\| 峰在 −0.06 mA，峰位被 0.02 mA 步长夹在两点之间 | `rf_phase_response/1119_1318*_zm02、1312*_zm01、1322*_z000、1324*_z01、1326*_z02（5 点粗扫）`<br>`rf_phase_response/1119_1334*_zm020 … 1119_1451*_zp020e（21 点细扫）` | — |
| rf_phase_response | [20261120_coupling_check](rf_phase_response/20261120_coupling_check/README.md) | 最优 Z 不随脉宽与拖尾比例移动（6 个位置峰位相差 ≤0.00037 mA，判为不耦合）；配上各自最优 Z 后响应主要由脉冲总转角决定，(w, s_f) 平面上是一个沿总转角方向的脊而不是闭合峰（弱本征值与 0 相容，但与等转角线差 14.2°）；三点对比中网格最优 B(8.60/0.50) 比当前最优 A 高 3.1%±0.5%（5 轮配对，显著），干净 π C 高 1.7%±0.9%（不显著）；B 已设为默认参数 | `rf_phase_response/1120_12*-13*_cc1_*（阶段 1，24 次）`<br>`rf_phase_response/1120_13*-14*_cc2_*（阶段 2，33 次）`<br>`rf_phase_response/1120_15*_cc3_*（阶段 3，15 次）`<br>`rf_phase_response/_studies/20261119_rf_phase_scan/coupling_check（逐点 JSONL 与采集日志）` | — |
| rf_phase_response | [20261120_rf_vpp_scan](rf_phase_response/20261120_rf_vpp_scan/README.md) | 第 1 批 12/23 点（20–1000 mVpp 中已测 9 个网格点）：线性区只到 40–50 mVpp（增益 62.2 mV/deg 每 Vpp，40 mVpp 仍有 98.6% 线性），120 mVpp 已压到 75%；S 在 200 mVpp 达峰 6.53 后回落，读出链路 R 峰 0.316→0.339 V 逼近 0.344 V 饱和线，残差从 5 mV 涨到 123 mV，高段是失真数据；余下 11 点因 Windows 套接字耗尽中断，待续跑 | `rf_phase_response/1120_21*_y_vpp_*（18 个运行）`<br>`rf_phase_response/1120_21*_y_ref*（5 个参考点）`<br>`rf_phase_response/_studies/20261120_rf_vpp_scan（逐点记录与日志）` | — |
| rf_phase_response | [20261120_xy_bias_scan](rf_phase_response/20261120_xy_bias_scan/README.md) | 在 8.6 µs/0.5/38.3 µs/−0.0655 mA 下，c0 零点在 (0.19, 0.00) V、\|c0\|=16.3 mV（c0 对 X 敏感 259 mV/V、对 Y 仅 9.5 mV/V，与 11-19 在 Z=0 测到的方向相反）；S 沿 Y 浅峰，漂移归一后 r=1.00–1.05，X=0.16 行顶点 y=+0.10 V、峰 2.99 mV/deg，S 最大实测点 (0.16, 0.08) 为 3.035；S 极大点与 c0 零点不重合，两者差 6.5%，且 S 与拟合残差负相关（−0.81），随 Y 变化更像工作点/驱动效应而非响应本身变好 | `rf_phase_response/1120_17*_xy_*（第 1 轮 30 个运行）`<br>`rf_phase_response/1120_20*_ys_*（第 2 轮 17 个运行）`<br>`rf_phase_response/_studies/20261120_xy_bias_scan（两轮逐点记录与日志）` | — |
| rf_phase_response | [20261121_locking_phase_scan](rf_phase_response/20261121_locking_phase_scan/README.md) | 解调相位只让相位读数整体旋转：arg(c0) 与 arg(c+1) 的斜率 −1.002±0.002、−1.0002±0.0007；模量与斜率完全不变，S=3.022 的相对散差 2.8%、随相位斜率 −3e-5±5e-5 mV/deg 每度；最佳读相位也不动（斜率 6e-5±8e-4，87.34°±0.16°），因为它是"哪个 φ 给出最大 \|dZ/dφ\|"，整体旋转不改变它。 | `rf_phase_response/1121_11*_demod_*（24 个相位点）`<br>`rf_phase_response/1121_11*_demod_ref*（2 个参考点）`<br>`rf_phase_response/_studies/20261121_locking_phase_scan（逐点记录与日志）` | — |
| rf_phase_response | [20261122_xy_balance_scan](rf_phase_response/20261122_xy_balance_scan/README.md) | 1 Vpp 下扫了两批 XY 网格（窄：X 0.13–0.25、Y ±0.08；宽：X、Y 各 ±0.5）。两瓣峰高的不平衡正比于 c0 在 θ≈−8° 方向上的投影（宽网格 r=0.954、斜率 0.0171±0.0011 每 mV；窄网格 r=0.88、斜率 0.0160，方向一致），\|c0\| 小则两瓣近等高；但截距 −6.2%（窄 −7.5%）说明还有一个与偏置无关的仪器性不对称，要让两瓣完全等高需让 c0 投影约 +3.6 mV 去抵消它。1 Vpp 下 \|c0\| 只有 4–20 mV、平衡区很宽；削顶（\|c3\|/\|c1\|≈39%）是同时存在的第二个限制 | `rf_phase_response/1122_20*_bal_*（28 个运行）`<br>`rf_phase_response/_studies/20261122_xy_balance_scan（逐点记录与日志）` | — |
| rf_phase_response | [20261126_zw_2d](rf_phase_response/20261126_zw_2d/README.md) | 无 1200B、DG 20 Vpp 直驱、无拖尾补偿下，最强响应在 Z 脉宽 9.0 µs + 主磁场 −0.028 mA；0.05 Vpp 时为 122 mV/rad（K0 0.134），0.15 Vpp 平台 228 mV/rad（K0 0.416），0.5 Vpp 进入饱和区（response 190、K0 1.39 为陡边斜率）。三档灵敏度（同工作点、无拖尾）实测 3–50 Hz 相位噪声中位 602 / 220 / 78.4 µrad/√Hz，随 K0 近似反比；0.5 Vpp 数字最好但属强驱动饱和工作点。默认参数已改为 Z 9.0 µs、−0.028 mA、Y_RF_VPP 0.5 Vpp。 | `rf_phase_response/_studies/20261126_zw_2d（粗扫 40 点）`<br>`rf_phase_response/_studies/20261126_zw_2d_stage2a（宽度 6–11 µs）`<br>`rf_phase_response/_studies/20261126_zw_2d_stage2b（宽度 20–40 µs）`<br>`rf_phase_response/_studies/20261126_zw_2d_summary（汇总图）`<br>`rf_phase_response/1126_17*–1126_22*（各点运行目录）`<br>`rf_phase_response/_studies/20261126_zw_2d_fine1（0.05 Vpp 细扫）`<br>`rf_phase_response/_studies/20261127_rf_sweep（射频幅度扫描）`<br>`rf_phase_response/_studies/20261127_fine015（0.15 Vpp 细扫）`<br>`rf_phase_response/_studies/20261127_fine05（0.5 Vpp 小网格）`<br>`rf_phase_sensitivity/1127_002350、1127_024145、1127_031206（三档灵敏度）` | — |
| rf_phase_sensitivity | [20261125_mfmod_live_verification](rf_phase_sensitivity/20261125_mfmod_live_verification/README.md) | Stage 2 电学验证与 Stage 3 实机 1125_074220（0.05 Vpp）已跑通 MF-MOD + Demod 4 全链路（5×开关相偏 <0.037°、0-360°线性残差 ≤0.030°、PM 切换跳变 +0.0027°、K0=0.17120 V/rad、A05 零次重对准、20 Hz AM 仅 0.0073%）。B01 判 partial 并非前期没测出，而是两处既有瓶颈导致 0 个频点过质检门控：5/10 Hz 在 DHO924 降速 500 kSa/s 下发生 CH3 方波谐波混叠假 AM（与改前 DG4000 1124_115154 同现），20/40 Hz 实测信号 5.0-5.9 mV 被 50-54 mV 工频压低拟合 SNR（<5）；离线剔除工频后真实 SNR=50-84，\|H\|/\|K0\|=0.84-1.01，20-40 Hz 预估等效相位噪声中位约 1.48 mrad/√Hz。 | `rf_phase_sensitivity/_mfli_mod_verification`<br>`rf_phase_sensitivity/1124_191042_phase_sensitivity`<br>`rf_phase_sensitivity/1124_191353_phase_sensitivity`<br>`rf_phase_sensitivity/1125_074220_phase_sensitivity`<br>`rf_phase_sensitivity/1124_115154_phase_sensitivity` | — |
| rf_phase_sensitivity | [20261127_mx_standard_amplitude](rf_phase_sensitivity/20261127_mx_standard_amplitude/README.md) | 0.15 Vpp 的相位响应最大（相图半径 31.5 mV、切线斜率 31.5 mV/rad），0.05 Vpp 为 28.4 mV，0.5 Vpp 饱和降到 13.6 mV；三档切线斜率都等于半径（1.000-1.002，离散 ≤1.4%）。3-50 Hz 相位灵敏度（y 走线口径）0.05/0.15/0.5 Vpp 分别为 193.1/189.7/438.1 urad/rtHz，θ 诊断比值 1.013/1.004/0.999。A05 频响 37/37 点全部接受（29-31 点 clean），H0 0.0333/0.0370/0.0140 V/rad、fc 78.8/105.9/232.0 Hz，H0/K0 1.18/1.19/1.03，低频闭合 1.002/1.004/0.990；650-900 Hz 部分频点音调信噪比低于 5，按 advisory 处理（不阻止出图）。 | `mx_standard_rf_phase_sensitivity/1127_182021_mx_std_v3_vpp005（0.05 Vpp，24 点相图 + 11 点细扫 + 37 点调相扫频 + 4 段宽带噪声）`<br>`mx_standard_rf_phase_sensitivity/1127_183307_mx_std_v3_vpp015（0.15 Vpp，同上）`<br>`mx_standard_rf_phase_sensitivity/1127_184422_mx_std_v3_vpp050（0.5 Vpp，同上）`<br>`旧链窄带对照：1127_154410/1127_153650/1127_153151（TC 1 ms、5 kSa/s，仅 3-50 Hz 与 24 点相图）` | — |
| rf_phase_sensitivity | [20261127_mx_standard_phase_noise](rf_phase_sensitivity/20261127_mx_standard_phase_noise/README.md) | 标准 Mx 相图为圆且切线斜率恒等于半径（24/24 段 0.9998R，±0.6%）；同一工作点 θ 与 y 两条噪声路径等价（3-50 Hz 比值 1.0009，246 µrad/rtHz），正式流程取 y 路径（K=R） | `rf_phase_sensitivity/_studies/1127_140556_mx_std_full2（新流程 24 点相图 + 6 段静态噪声）`<br>`rf_phase_sensitivity/_studies/1127_140228_mx_std_smoke3（新流程 12 点相图 + 2 段静态噪声）`<br>`rf_phase_sensitivity/_studies/1127_133853_mx_std_full（旧流程 24 点相图 + 6 段动态噪声，噪声帧无效）`<br>`rf_phase_sensitivity/_studies/1127_133601_mx_std_smoke（旧流程 12 点相图 + 2 段动态噪声，噪声帧无效）` | 需硬件 |
| rf_phase_sensitivity | [noise_breakdown_4states.py](rf_phase_sensitivity/noise_breakdown_4states.py) | 完成 A03/A04 工作点对齐后，按六态（含 Z 常数控制对照）分段测量解调 Y 噪声功率谱（V^2/Hz）并出图。 | — | 需硬件 |
| rf_phase_sensitivity | [px_bias_map_scope.py](rf_phase_sensitivity/px_bias_map_scope.py) | RF 关断、脉冲控制场开启时，扫描 X/Y 静态偏置，测量 Px（DHO924 CH1）时域波形随偏置的变化。 | — | 需硬件 |
| rf_phase_sensitivity | [px_field_states_scope.py](rf_phase_sensitivity/px_field_states_scope.py) | RF 关断条件下，用 DHO924 直接读平衡探测器 Px（CH1）在五种 Z 场 / 泵浦状态下的时域波形并对比。 | — | 需硬件 |
| z_chain_stability | [make_zero_marginal.py](z_chain_stability/make_zero_marginal.py) | 零点的两小时时间序列 + 左侧边缘分布（不新增测量，数据取自运行目录 raw/zero_dg0.csv）。 | — | — |

<!-- END GENERATED SCRIPT INDEX -->
