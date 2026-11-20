# 射频相位响应：宽度、拖尾与沿宽参数扫描（2026-11-19）

本目录存放 2026-11-19 参数扫描的分析产物与脚本，不修改各运行目录的 `raw/`。

## 运行清单

| 标签 | 条件 | 运行目录 |
| --- | --- | --- |
| baseline | w=8.03 µs，s_f=0.544，τe=46.9 µs，edge=1.0 µs | `1119_092825_rf_phase_response` |
| w4 | w=4 µs | `1119_103941_w4` |
| w6 | w=6 µs | `1119_104134_w6` |
| w10 | w=10 µs | `1119_104327_w10` |
| w12 | w=12 µs | `1119_104537_w12` |
| tailoff | 关闭拖尾补偿 | `1119_104734_tailoff` |
| sf34 | s_f=0.34 | `1119_104933_sf34` |
| sf44 | s_f=0.44 | `1119_105143_sf44` |
| sf64 | s_f=0.64 | `1119_105354_sf64` |
| sf74 | s_f=0.74 | `1119_105605_sf74` |
| te369 | τe=36.9 µs | `1119_105819_te369` |
| te569 | τe=56.9 µs | `1119_110030_te569` |
| edge05 | edge=0.5 µs | `1119_110248_edge05` |
| edge15 | edge=1.5 µs | `1119_110459_edge15` |

除注明的参数外，所有运行相同：5 kHz、50 mVpp、190 mA、相位 0–360°/10°（36 点）、`N_REPEAT=1`、单点 0.2 s、稳定 0.1 s、温控分组 2、组末等待 2.0 s。

## 图

- `figures/param_width.png`：宽度 4/6/8.03/10/12 µs。左为相量椭圆，右上为 `|c0|`、`|c+1|`、`|c−1|` 与切线斜率（右轴，mV/deg），右下为最佳相位。
- `figures/param_sf.png`：s_f = off/0.34/0.44/0.544/0.64/0.74。
- `figures/param_te.png`：τe = 36.9/46.9/56.9 µs。
- `figures/param_edge.png`：edge = 0.5/1.0/1.5 µs。
- `figures/phasors_overlay.png`：宽度与拖尾/沿宽两组相量叠加。
- `figures/components_vs_condition.png`：全部条件的三个分量与最佳相位。
- `figures/components.csv`：全部条件的数值汇总。

基线在各图的折线中作为一个点参与连线，并额外用黑色空心标记与竖向点线标出。

## 关键关系

椭圆半长轴 `A = |c+1| + |c−1|`；最大切线斜率

```
max |dZ/dφ| = A × π/180   （mV/deg）
```

最快点在短轴端点，而非长轴端点。

## 复现

每个运行目录的摘要与单次图由实验自带分析生成：

```powershell
$env:LAB_RUN_DIR = "D:\Code\exp_agent_run\data\rf_phase_response\<run>"
agent_exp_env\Scripts\python.exe experiments\RfPhaseResponse_plot.py
```

四张参数图与汇总由 `scripts/compare_per_param.py` 生成（经 `data_root()` 读 `rf_phase_response/` 下各运行），输出到本子目录 `figures/`；`scripts/compare_phase_runs.py` 生成叠加图与 `components.csv`。

## 注意

- `N_REPEAT=1`，没有逐点标准误，`fit_quality` 不具判别力。
- TEC（COM13）缺失，运行中 `temp_c` 为空。
- w10 的 `|c0|=10.9 mV` 高于 w6（4.9）与 w12（3.7），且残差最大（4.3×10⁻⁴ V）；运行内前后半段稳定，建议复跑 w10/w6 验证可重复性。
