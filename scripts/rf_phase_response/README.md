# 射频相位响应（rf_phase_response）研究记录

`data_type = rf_phase_response` 下的分析与对比记录，属探索档：不进注册表、不接 GUI。每个目录是一个独立问题，README 内嵌本目录全部图，脚本、结果表与运行清单同目录。原始运行目录仍在 `data/rf_phase_response/`。

## 按问题分六个目录

| 目录 | 回答的问题 | 一句话结论 |
|---|---|---|
| [20261119_xy_field_scan](20261119_xy_field_scan/README.md) | X/Y 偏置怎么影响响应 | 只平移中心 c₀（X +31.5 mV/V、Y −143.4 mV/V），\|c±1\| 与最佳相位不动 |
| [20261119_z_field_scan](20261119_z_field_scan/README.md) | Z 主磁场在哪里共振、多宽 | −0.07 mA 附近尖锐共振，峰顶斜率约 2.0 mV/deg（零场 0.274 的 7 倍），峰宽约 0.04 mA |
| [20261119_pulse_params](20261119_pulse_params/README.md) | 零场下脉冲参数各自管什么 | 宽度定翻转角（8.03 µs ≈ π），拖尾 s_f 主要抬 \|c₀\|，τe 与沿宽是弱参数 |
| [20261119_pulse_params_at_z06](20261119_pulse_params_at_z06/README.md) | 到 Z 峰附近这些结论还成立吗 | 宽度结论不变；拖尾作用反转（主要压 \|c₀\|）；2D 网格峰在 (0.65, 45)，2.078 mV/deg |
| [20261119_response_optimization](20261119_response_optimization/README.md) | 怎么把斜率一步步抬高、停在哪 | 2.078 → 2.869±0.005 mV/deg（Z=−0.068、8.03、0.575/38.3）；X/Y 归零未验证 |
| [20261120_coupling_check](20261120_coupling_check/README.md) | 最优 Z 随参数动吗、是峰还是脊、谁最优 | 不耦合；是等转角脊；网格最优 B(8.60/0.50) 比 A 高 3.1%±0.5%，已设为默认参数 |

## 跨批次的三条主线

1. **翻转角由上面积（总转角）决定。** 宽度、拖尾 $s_f$、$\tau_e$ 换了无数组合，响应始终主要随"脉冲总转角相对干净 π 的比值"变化；11-20 的网格把这条推到极限——等转角方向上响应几乎平坦。
2. **Z 共振放大约 7 倍，但峰位不随脉冲参数移动。** 11-19 只在一个波形上验过（阶段 D），11-20 用 6 个波形位置系统验证，偏移 ≤0.37 µA。
3. **$c_0$ 与斜率是两个不同的优化目标。** 斜率在 (0.65,45) 最高，$c_0$ 在 (0.80,60) 最小；X/Y 偏置只能平移 $c_0$（±50 mV 约 ±7 mV 量级），归零能力有限。

## 公共约定与已知情况

- 所有批次 `N_REPEAT=1`（优化最终点除外，用 3 次），没有逐点标准误，`fit_quality` 不具判别力。
- TEC（COM13）从 11-19 起一直缺失，运行中 `temp_c` 为空；11-19 还有一次 MFLI DAQ 卡死（14:03–14:41），受影响点已重跑，失败目录保留。
- 批次间漂移 0.1–0.3%/min，跨批次比较必须靠参考点归一（11-20 的耦合检查插了 14 次参考点）。
- 复现统一前置：`$env:LAB_DATA_ROOT = "D:\Code\exp_agent_run\data"`，然后按各目录 README 的命令跑；采集脚本要单独授权。

## 已知问题

- `20261119_xy_field_scan/scripts/compare_field_scan.py` 按标签匹配全部 x/y/z 运行，重跑会并入 11-19 之后的 Z 扫描（原 18 行不变，新增约 70 行）；各目录保留当时生成的图。
- `20261119_pulse_params/scripts/compare_per_param.py` 在当前数据上报 `KeyError: 'components'`：19 个后续运行的摘要缺该字段（原脚本代码相同，同样失败）；重跑前需按标签白名单筛选运行。
- `20261119_response_optimization/scripts/run_z2_fine_resume.sh` 是采集命令记录，会启动真实实验，不作为分析脚本重跑。
- `20261120_coupling_check/scripts/model_area.py` 需要参数，例如 `8.03 0.575 38.3`。
- 逐点过程文件（JSONL、采集日志）按约定在运行目录 `data/rf_phase_response/_studies/<研究目录>/`，不在本仓库。
