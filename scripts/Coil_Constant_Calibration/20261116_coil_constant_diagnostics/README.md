---
title: Z 线圈常数标定离线诊断
conclusion: K=653.73±2.44 Hz/mA（93.39 nT/mA，I0=−2.03 mA），三轮复现差 0.02%；Y RF 幅度只加宽不移动线心；驱动链电流上限约 220 mA，K 只在 |I|≤200 mA 内有效
data:
  - Coil_Constant_Calibration/1116_161937_coil_const_coarse3
  - Coil_Constant_Calibration/1116_165757_coil_amp050
  - Coil_Constant_Calibration/1116_170645_coil_amp500b
  - Coil_Constant_Calibration/1116_173127_coil_const_fine
  - Coil_Constant_Calibration/1116_181213_coil_const_fine
  - Coil_Constant_Calibration/1116_185249_coil_const_300
---

# Z 线圈常数标定：离线诊断（2026-11-16）

完整记录与图见 `工作总结/2026-11-18/Z线圈常数标定_详细记录.md`（主文 `工作总结/2026-11-18/main.md` 第三节）。
本目录只收现存数据上还能重跑的脚本。

## 脚本

| 脚本 | 回答的问题 | 产物 |
|---|---|---|
| `coil_field_state_audit.py` | 各运行全部电流点的 R(f) 找峰，反推当时的零电流中心 f0 | 打印 |
| `coil_curve_centers.py` | `1116_161937_coil_const_coarse3` 逐曲线中心，便于贴表 | 打印 |
| `coil_fine_final_report.py` | 细扫最终轮 `1116_173127_coil_const_fine` 的逐曲线结果与整体标定 | 打印 |
| `coil_gamma_floor_test.py` | `1116_181213_coil_const_fine` 两条低场曲线上，有/无 γ 下限的拟合对比 | 打印 |
| `diag_amp_compare.py` | 三档 Y RF 幅度在 −100 mA 附近的线型对比 | `1116_165757_coil_amp050/results/diag_amp_compare.png` |
| `diag_amp_compare_complete.py` | 同上，含各自洛伦兹拟合 | `1116_170645_coil_amp500b/results/diag_amp_compare_complete.png` |
| `diag_drive_current_saturation.py` | ±300 mA 轮的驱动电压—实测电流曲线与高电流残差 | `1116_185249_coil_const_300/results/diag_drive_current_saturation.png` |

`diag_*.py` 原放在各运行的 `results/` 下，按 `__file__` 推导运行目录；迁入时改为 `data_root()` 下的固定运行路径，产物仍写回原处。

## 迁移说明（2026-11-20）

- 来源：开发目录 `.cache/` 下 15 个 `coil_*.py`，以及上表三个运行 `results/` 里的 `diag_*.py`。
- 新旧位置各跑一遍，stdout 逐字一致（输出路径行除外）。
- 未收入：`coil_coarse_dump/fit/inspect/plot.py`、`coil_amp_state_check.py`、`coil_frame_timing.py`
  （读 `1116_145659_coil_const_coarse`、`1116_154948_coil_const_coarse2`）、`coil_fine_dump.py`、`coil_fine_lineshape.py`
  （读 `1116_163244_coil_const_fine`）、`coil_verify_inspect.py`（读 `1116_153524_coil_const_verify`）——这些源运行已删，无法重跑；
  `coil_amp_compare.py`、`coil_amp_compare2.py` 是 `diag_amp_compare*.py` 的前身；`plot_saturation.py` 是
  `diag_drive_current_saturation.py` 的前身。原件在开发目录 `.cache/agent-tasks/research-scripts-tier/` 备份里。
- 数据损失：迁移核验时在原位置重跑了 `diag_amp_compare*.py`，脚本跳过已删源运行的曲线（1.5 Vpp，前者还有 0.5 Vpp），
  覆盖了 11-16 生成的两张原图；现存两张图不含这些曲线，原图未保留。1.5 Vpp 线型的数值见详细记录第四节表格。
