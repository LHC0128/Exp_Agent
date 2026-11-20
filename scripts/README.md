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
| Coil_Constant_Calibration | [20261116_coil_constant_diagnostics](Coil_Constant_Calibration/20261116_coil_constant_diagnostics/README.md) | K=653.73±2.44 Hz/mA（93.39 nT/mA，I0=−2.03 mA），三轮复现差 0.02%；Y RF 幅度只加宽不移动线心；驱动链电流上限约 220 mA，K 只在 \|I\|≤200 mA 内有效 | `Coil_Constant_Calibration/1116_161937_coil_const_coarse3`<br>`Coil_Constant_Calibration/1116_165757_coil_amp050`<br>`Coil_Constant_Calibration/1116_170645_coil_amp500b`<br>`Coil_Constant_Calibration/1116_173127_coil_const_fine`<br>`Coil_Constant_Calibration/1116_181213_coil_const_fine`<br>`Coil_Constant_Calibration/1116_185249_coil_const_300` | — |
| coil_drive_characterization | [20261114_coil_drive_analysis](coil_drive_characterization/20261114_coil_drive_analysis/README.md) | 固定延迟 δ≈−0.21 µs、电感 τ≈0.57 µs（L≈41 µH）；310 kHz 为无源并联谐振（Q≈9），更可能在功放输出网络或电缆上 | `coil_drive_characterization/1114_114241_coil_drive`<br>`coil_drive_characterization/1114_165713_coil_drive`<br>`coil_drive_characterization/1114_170336_coil_drive`<br>`coil_drive_characterization/1114_171514_coil_drive` | — |
| Noise_Spectrum_XY_Ctrl | [peak_diagnosis_0921_1758.py](Noise_Spectrum_XY_Ctrl/peak_diagnosis_0921_1758.py) | Noise_Spectrum_XY_Ctrl 0921_1758 运行的峰脊独立诊断试算；不覆盖正式结果，不修改采集 K/B。 | — | — |
| pi_width_scan | [20261118_pi_width_investigation](pi_width_scan/20261118_pi_width_investigation/README.md) | 原子处 Z 脉冲场约 54% 瞬时到达、约 46% 以 τe≈47 µs 延迟到达，工作 π 宽度由电学 5.12 µs 移到 6.5–6.7 µs（190 mA）；慢拖尾与静态横场产生的 c0 使相位扫描椭圆偏离原点 | `pi_width_scan/1117_175856_pi_width_scan`<br>`pi_width_scan/1118_*（逐条运行见 01_acquisition/runs_index.csv）`<br>`pi_width_scan/_phase_background_diagnostic`<br>`scope_px_snapshot/1118_135547`<br>`bperp_null/1118_191524_bperp_null` | 需硬件 |
| pi_width_scan | [phase_background_three_component_split.py](pi_width_scan/phase_background_three_component_split.py) | 把 11-17 相位扫描（5.0 µs、190 mA、Y RF 5 kHz 0.2 Vpp）分解成 c0、c+1、c-1 三条独立曲线。 | — | — |
| RF_Control_Scale_Sensitivity | [analyze_noise_records.py](RF_Control_Scale_Sensitivity/analyze_noise_records.py) | 区分整组灵敏度偏差与少数高噪声记录拉高平均 PSD（正式模块 noise_diagnostics 的命令行薄入口）。 | — | — |
| RF_Field_Sensitivity_AW | [20261017_paper_comparison](RF_Field_Sensitivity_AW/20261017_paper_comparison/README.md) | 1013_224833（常数）与 1013_231737（优化）两次运行：零场斜率提高 59.4%，3–150 Hz 共同频带灵敏度降低 23.3% | `RF_Field_Sensitivity_AW/1013_224833_rf_sens_aw`<br>`RF_Field_Sensitivity_AW/1013_231737_rf_sens_aw` | — |
| rf_phase_response | [20261119_rf_phase_scan](rf_phase_response/20261119_rf_phase_scan/README.md) | 宽度定翻转角、拖尾主要移动 c0、X/Y 偏置线性平移 c0；Z≈−0.07 mA 有尖锐共振；最大响应点 Z=−0.068 mA、w=8.03 µs、s_f=0.575、τe=38.3 µs，斜率 2.87 mV/deg（比初始高约 38%） | `rf_phase_response/1119_*（逐条运行见 SUMMARY.md 第 1 节与各子目录 README）` | — |
| z_chain_stability | [make_zero_marginal.py](z_chain_stability/make_zero_marginal.py) | 零点的两小时时间序列 + 左侧边缘分布（不新增测量，数据取自运行目录 raw/zero_dg0.csv）。 | — | — |

<!-- END GENERATED SCRIPT INDEX -->
