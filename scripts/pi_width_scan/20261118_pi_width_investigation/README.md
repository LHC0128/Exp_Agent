---
title: π 宽度原子域定标异常研究
conclusion: 原子处 Z 脉冲场约 54% 瞬时到达、约 46% 以 τe≈47 µs 延迟到达，工作 π 宽度由电学 5.12 µs 移到 6.5–6.7 µs（190 mA）；慢拖尾与静态横场产生的 c0 使相位扫描椭圆偏离原点
data:
  - pi_width_scan/1117_175856_pi_width_scan
  - pi_width_scan/1118_*（逐条运行见 01_acquisition/runs_index.csv）
  - pi_width_scan/_phase_background_diagnostic
  - scope_px_snapshot/1118_135547
  - bperp_null/1118_191524_bperp_null
---

# π 宽度原子域定标：异常现象研究（2026-11-17/18）

本目录集中存放 2026-11-18 对 `pi-width-scan` 异常现象的研究：上机记录、分析代码、拟合结果和图。原始数据仍在各运行目录的 `raw/`，本目录不改动任何运行目录。

- 结论摘要：[报告.md](报告.md)
- 逐步过程与原理：[记录.md](记录.md)

两份文件写于开发目录的 `.cache/pi_width_investigation/`，原样复制到这里。文中的旧路径按下方"旧路径对照"找到对应文件。

2026-11-20 从运行目录 `data/pi_width_scan/_pi_width_investigation_20261118/` 迁入（不含 PDF，数据侧目录已删除，备份在开发目录
`.cache/agent-tasks/research-scripts-tier/data_backup/`）。迁移时的改动：读运行目录的路径改经 `data_root()`；去掉
`sys.path.insert(0, "D:/Code/exp_agent")`（解释器已可编辑安装）；第 10 阶段原读开发目录 `.cache/tail_comp_scan/` 的 lab_client
记录收进 `10_tail_compensation/lab_client_out/`，当时的上机批处理收进 `10_tail_compensation/runners/`（含当时授权，仅作记录，
不直接重跑）；`10_tail_compensation/scope_capture.py` 连接真实仪器，只能在运行目录执行。
复现前设 `$env:LAB_DATA_ROOT = "D:\Code\exp_agent_run\data"`。

## 结论速览

| 问题 | 结论 | 依据 |
| --- | --- | --- |
| 相位扫描为什么不对称 | 与射频无关的 c0（S）把 c±1 构成的椭圆推离原点 | 03、04 |
| S 从哪里来 | 静态横场 × Z 场慢拖尾，加上慢拖尾自带的横向分量 | 04、06 |
| π 宽度 | 190 mA 下 c−1 峰值在 6.6–6.7 µs，回零点（2π）在约 12 µs；电学值是 5.12 µs | 03 |
| 为什么不是电学值 | 原子处的 Z 场约 54% 瞬时出现，约 46% 以 τe ≈ 47 µs 延迟出现，电流本身没有拖尾 | 05、06 |
| 单相位找 R 峰行不行 | 不行：4 个相位下峰位分布在 3–14 µs | 03 |
| 怎样补平静态 Bx、By | 打 π 脉冲时把 10 kHz（2f）复振幅调到零；主要误差来自拖尾横向分量（模型） | 08 |

## 目录分类

| 目录 | 内容 | 主要文件 |
| --- | --- | --- |
| `01_acquisition/` | 上机批处理与运行清单（2026-11-18 00:33–01:48，经 GUI 的 lab_client） | `run_batch.ps1`、`run_batch2.ps1`、`runs/*.json`（每次运行的 lab_client 摘要）、`runs_index.csv` |
| `02_offline_1117/` | 11-17 已有数据的复盘：相位扫描傅里叶分解、10°/190° 奇偶分离 | `harmonics.py` → `harmonics.txt`；`odd_even.py` → `odd_even.txt`、`odd_even_10_190.png` |
| `03_phase_grid/` | 4 相位 × 25 脉宽网格分解，三种 X 偏置 | `grid_analysis.py` → `grid_bias{0,m20,p20}.{txt,png,npz}`；`locate_pi.py`（c−1 峰值与回零点） |
| `04_x_bias_response/` | c0 对 X 偏置的线性响应 L 与对称性检验 | `bias_response.py` → `bias_response.{txt,png}` |
| `05_time_domain/` | 示波器时域 P_x 折叠、奇偶分解与衰减拟合 | `scope_fold.py` → `px_gated_rf_*_fold.*`；`px_odd_even.py` → `px_time_domain_odd_even.{png,pdf}`、`px_odd_even.json` |
| `06_model/` | 周期稳态 Bloch 模型、拟合、样本外检验与预言 | `blochsim.py`、`explore.py`（第一轮模拟）；`fitmodel.py` → `fits/`；`plot_fit.py`；`predict_L.py` → `figures/predict_*`；`predict.py` → `predictions/`；`model_time_trace.py` → `model_time_trace.json` |
| `07_summary_figures/` | 工作总结用图 | `make_summary_figures.py` → `figA_phase_grid_decomposition.*`、`figB_model_evidence.*` |
| `08_even_2f_criterion/` | 纯模型：10 kHz 偶部 c2 = 0 作为补平 Bx、By 判据的验证（不含上机数据） | `harmmodel.py`（P_x 与任意谐波）、`verify.py` → `outputs/`、`figures/`；`make_figC.py` → `figC_even_2f_criterion.*`；结论见 `结论.md` |

不在本目录的相关内容：
- 交接与 11-17 诊断：运行目录 `<数据根>/pi_width_scan/HANDOVER.md`、`<数据根>/pi_width_scan/_phase_background_diagnostic/`（其分析脚本在 `scripts/pi_width_scan/phase_background_three_component_split.py`）。

## 运行清单（详见 `01_acquisition/runs_index.csv`）

固定条件：Y_RF_VPP 0.1、5 kHz、190 mA、沿 1 µs、脉宽 2–14 µs 步进 0.5、N_REPEAT 2、步骤 A01,A02,A04（A05 自动收尾）。

| X 偏置 | 10° | 100° | 190° | 280° | 10° 复测 |
| --- | --- | --- | --- | --- | --- |
| 0 | 1118_003307 | 1118_003802 | 1118_004300 | 1118_004755 | 1118_005253 |
| −0.02 V | 1118_005812 | 1118_010309 | 1118_010809 | 1118_011645（1118_011306 失败） | 1118_012150 |
| +0.02 V | 1118_012648 | 1118_013208 | 1118_013703 | 1118_014305（1118_014159 失败） | 1118_014800 失败 |

三次失败分别是：A04 中途 VISA 超时（已存 2–8 µs 数据）、A02 时 MFLI 不可见、A02 时 VISA 超时。前两次 A05 收尾为 completed；第三次在连接阶段就失败了，还没写入任何输出命令。

## 复现（在 `D:/Code/exp_agent` 下用 `agent_exp_env\Scripts\python.exe`）

设 `A=./`，`R=<数据根>/pi_width_scan`：

```
python $A/03_phase_grid/grid_analysis.py $A/03_phase_grid bias0 $R/1118_003307_pi_width_scan:10 $R/1118_003802_pi_width_scan:100 $R/1118_004300_pi_width_scan:190 $R/1118_004755_pi_width_scan:280 $R/1118_005253_pi_width_scan:10
python $A/03_phase_grid/locate_pi.py $A/03_phase_grid/grid_bias0.npz
python $A/04_x_bias_response/bias_response.py $A/03_phase_grid $A/04_x_bias_response
python $A/05_time_domain/px_odd_even.py
python $A/06_model/fitmodel.py $A/03_phase_grid/grid_bias0.npz $A/06_model/fits/rf_slow rfonly          # 约 20 分钟
python $A/06_model/predict_L.py $A/06_model/fits/rf_slow.json $A/03_phase_grid $A/06_model/figures slow
python $A/06_model/model_time_trace.py
python $A/07_summary_figures/make_summary_figures.py
python $A/08_even_2f_criterion/verify.py        # 约 1 分钟
python $A/08_even_2f_criterion/make_figC.py
```

另外两组网格（−0.02 V、+0.02 V）的运行目录按上表替换。2026-11-18 10:22 从本目录重跑 `bias_response.py`、`predict_L.py`，结果与原值一致：L 残差 0.42 mV，S 分解残差 1.61 mV。

## 旧路径对照（记录.md、报告.md 中的路径）

| 记录中的路径（`.cache/pi_width_investigation/` 下） | 本目录 |
| --- | --- |
| `run_batch*.ps1`、`runs/` | `01_acquisition/` |
| `scripts/harmonics.py`、`scripts/odd_even.py`、`outputs/harmonics.txt`、`outputs/odd_even.*`、`figures/odd_even_10_190.png` | `02_offline_1117/` |
| `scripts/grid_analysis.py`、`scripts/locate_pi.py`、`figures/grid_*` | `03_phase_grid/` |
| `scripts/bias_response.py`、`figures/bias_response.*` | `04_x_bias_response/` |
| `scripts/scope_fold.py`、`figures/px_gated_rf_*_fold.*` | `05_time_domain/` |
| `scripts/{blochsim,explore,fitmodel,plot_fit,predict,predict_L}.py`、`fits/`、`predictions/`、`figures/fit_*`、`figures/predict_*`、`outputs/explore_sim.txt`、`outputs/predict_L_slow.txt` | `06_model/` |


## 迁移核验（2026-11-20）

在开发目录新位置逐个重跑（单个限时 5 分钟）：151 份 JSON/CSV/TXT/MD 产物中 149 份与迁移前逐字一致，
`10_tail_compensation/outputs/j1_map.txt` 只差计时行，`README.md` 为本次迁移说明。未覆盖的脚本：

- 需命令行参数、未重跑：`02_offline_1117/odd_even.py`、`03_phase_grid/grid_analysis.py`、`03_phase_grid/locate_pi.py`、
  `04_x_bias_response/bias_response.py`、`05_time_domain/scope_fold.py`、`06_model/explore.py`、`06_model/fitmodel.py`、
  `06_model/plot_fit.py`、`06_model/predict.py`、`06_model/predict_L.py`、`10_tail_compensation/scope_analysis.py`
  （调用方式见 记录.md）。
- 超过 5 分钟未跑完：`10_tail_compensation/robust_j1.py`、`10_tail_compensation/scan_fit.py`。
- 连接真实仪器、未运行：`10_tail_compensation/scope_capture.py`。

## 后续收入（2026-11-20）

- `scope_px_snapshot/`：示波器 Px 时域快照 `<数据根>/scope_px_snapshot/1118_135547` 的采集与分析（原在该运行的 `scripts/`）。
  `capture.py` 连接真实仪器；`fit_model.py`、`estimate_null_bias.py`、`predict_zero_field.py` 读写该运行的 `results/`，
  模型取本研究 `08_even_2f_criterion/`。迁移核验：`fit_summary.json`、`null_bias_estimate.json` 与原件逐字一致，核验后原件已恢复。
- `07_summary_figures/make_concept_figure.py`：c0 / c+1 / c−1 三分量示意图，输出到 `工作总结/2026-11-18/fig_three_component_concept.png`（原在开发目录 `.cache/`）。
