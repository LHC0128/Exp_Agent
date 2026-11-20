---
title: 射频相位响应参数与场扫描汇总
conclusion: 宽度定翻转角、拖尾主要移动 c0、X/Y 偏置线性平移 c0；Z≈−0.07 mA 有尖锐共振；最大响应点 Z=−0.068 mA、w=8.03 µs、s_f=0.575、τe=38.3 µs，斜率 2.87 mV/deg（比初始高约 38%）
data:
  - rf_phase_response/1119_*（逐条运行见 SUMMARY.md 第 1 节与各子目录 README）
---

# 射频相位响应：参数与场扫描汇总目录

本目录合并了 2026-11-19 的射频相位响应参数扫描与 X/Y/Z 场扫描产物，原始数据仍保存在 `data/rf_phase_response/<运行目录>/raw/`。

2026-11-20 从运行目录 `data/rf_phase_response/_rf_phase_scan_20261119/` 迁入（不含 PDF）。迁移时的改动：
脚本输入经 `data_root()`；原先写到开发目录 `.cache/compare_out/` 的产物改为写回所在子目录的 `figures/`；
`opt_response/data/` 改名为 `opt_response/tables/`（`data/` 被根 `.gitignore` 忽略）；
原在 `.cache/` 的 `analyze_rf_vpp.py`、`make_stage_phasors.py` 收入 `opt_response/scripts/`。

## 结构

```
20261119_rf_phase_scan/
  SUMMARY.md                 汇总：数据来源、固定参数、扫描条件、结果表、结论、异常
  figures/
    summary_overview.png     四类扫描总览（宽度、s_f、X/Y、Z 细扫）
  param_scan/                宽度 / s_f / τe / 沿宽（14 条件）
    README.md
    figures/  scripts/
  field_scan/                X/Y 偏置与 Z 粗扫
    README.md
    figures/  scripts/
  z06_scan/                  Z=−0.06 mA 的脉冲参数复扫（14 条件）
  z06_grid/                  Z=−0.06 mA 的 s_f×τe 二维扫描（25 条件）
  z_fine_scan/               Z 主磁场细扫（−0.20…+0.20 mA，步进 0.02）
    README.md
    figures/  scripts/
  z06_scan/                  Z=−0.06 mA 固定时的脉冲参数复扫（14 条件）
    README.md
    figures/  scripts/
  scripts/                   汇总图脚本
    make_summary_figure.py
```

## 快速查看

- 先读 [SUMMARY.md](SUMMARY.md)；
- 总览图 `figures/summary_overview.png`；
- 各批次的运行清单与图说明见对应子目录的 `README.md`。

## 关键结论

- 宽度决定翻转角，`|c−1|` 在 8.03 µs（≈π）附近最大；
- 拖尾幅度主要移动中心 `|c0|`，对 `|c±1|` 影响小；
- X/Y 偏置近似线性平移 `|c0|`，不改变 `|c±1|` 与最佳相位；
- Z 主磁场在 −0.07 mA 附近有尖锐共振，`|c0|`、`|c±1|` 与切线斜率同时放大。

## 数据质量

- `N_REPEAT=1`，无逐点标准误；
- TEC（COM13）缺失，`temp_c` 为空；
- MFLI 在 14:03–14:41 出现 DAQ 卡死，受影响点已重跑，失败目录保留未删除；
- 详细说明与运行目录见 `SUMMARY.md` 第 6 节。

## 复现与已知问题

```powershell
$env:LAB_DATA_ROOT = "D:\Code\exp_agent_run\data"
agent_exp_env\Scripts\python.exe scripts\rf_phase_response\20261119_rf_phase_scan\<子目录>\scripts\<脚本>.py
```

迁移核验（2026-11-20，新位置重跑）：21 份 CSV/JSON 与原件逐字一致。三处例外：

- `field_scan/scripts/compare_field_scan.py` 按标签匹配全部 x/y/z 运行，重跑会并入 11-19 之后的 Z 扫描（原 18 行不变，新增约 70 行）；本目录保留原版图与 CSV。
- `param_scan/scripts/compare_per_param.py` 在当前数据上报 `KeyError: 'components'`：19 个后续运行的摘要缺该字段，原脚本代码相同，同样失败。待查：按标签白名单筛选运行。
- `coupling_check/scripts/model_area.py` 需要参数，例如 `8.03 0.575 38.3`。
- `opt_response/scripts/run_z2_fine_resume.sh` 是当时经 `lab_client` 续跑采集的命令记录，会启动真实实验，不作为分析脚本重跑。

- `field_scan/scripts/compare_field_xy_array.py`（2026-11-20 从 `.cache/` 收入）：X/Y 偏置扫描 2×6 相量阵列，生成 `field_scan/figures/field_xy_array.png`；重跑会并入之后的同标签运行，本目录保留原图。
