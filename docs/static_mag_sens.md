# 静磁场灵敏度测量 — 实验类型说明

供 `/expcodegen` 技能在生成该类型实验的 Notebook 时参考。

## 实验概述

测量静磁场的色散线形，根据色散斜率（dY/dB）和 Y 信号的功率谱密度（PSD）计算磁场灵敏度。

## 数据采集方式

Z 磁场使用 DG4000 的 **RAMP 波形**（三角波）连续扫场，HF2 锁相放大器通过 DAQ 模块采集一个完整周期的解调信号（R, X, Y）。

### RAMP 波形参数

| 参数 | 典型值 | 说明 |
|------|--------|------|
| `RAMP_LOW` | -0.5 V | 三角波最小值 |
| `RAMP_HIGH` | +0.5 V | 三角波最大值 |
| `RAMP_FREQ` | 1.0 Hz | 三角波重复频率 |
| `RAMP_SYMMETRY` | 20% | 上升段占周期比例 |

### 上升段 vs 下降段

```
对称性 20%:
  周期起点 (t=0) = 三角波最低点 V=RAMP_LOW
  ┌──────────────────────────────────────────────┐
  │  0 ~ 20% 周期 (t_norm < 0.2): 上升段          │
  │    电压 RAMP_LOW → RAMP_HIGH (单调递增)        │
  │    占周期 20%                                  │
  ├──────────────────────────────────────────────┤
  │  20% ~ 100% 周期 (t_norm ≥ 0.2): 下降段       │
  │    电压 RAMP_HIGH → RAMP_LOW (单调递减)        │
  │    占周期 80%                                  │
  └──────────────────────────────────────────────┘
```

**关键结论**：对称性 20% 时，下降段 = 周期的 80%。所谓"中间 80% 区域"就是下降段。
必须只取其中一个单调段（上升或下降），否则同一电压对应两个不同时间点的信号值，画图会折叠。

## 下降段掩码计算

```python
s = RAMP_SYMMETRY / 100.0      # 0.2
t_norm = time / T_ramp          # 归一化时间 [0, 1)
t_cycle = t_norm % 1.0          # 折叠到单个周期

# 下降段 = 电压单调递减区间
falling_mask = t_cycle >= s

# 如要裁剪转折区（推荐），取下降段中间 80%：
fall_start = s + (1.0 - s) * 0.1   # 0.28
fall_end   = s + (1.0 - s) * 0.9   # 0.92
fall_mask  = (t_cycle >= fall_start) & (t_cycle < fall_end)
```

## 色散曲线绘制

- 纵轴：锁相 X, Y, R 信号
- 横轴：Z 磁场值 `B = Z_voltage * V_TO_FT`
- **必须使用下降段掩码**，否则数据折叠
- 下降段电压单调递减，直接按时序画图即可，无需 `argsort`

## 色散拟合

Y 信号使用色散线形拟合：

```python
def dispersive(x, A, gamma, x0, C):
    return A * (x - x0) / ((x - x0)**2 + gamma**2) + C
```

拟合数据同样使用下降段掩码。

## 零交叉点（噪声测量位置）

从下降段（或上升段）找到 Y 信号过零点对应的 Z 电压，将 Z 场固定在该电压处测量噪声。

## 灵敏度计算

```
δB(f) = sqrt(PSD_Y(f)) / |dY/dB|
```

- `dY/dB`：色散拟合得到的零交叉点斜率（V/fT）
- `PSD_Y`：Y 信号功率谱密度（V²/Hz），使用 Welch 法估计

## 代码生成注意事项

1. **数据解析 cell**：计算 `t_norm`、`falling_mask`、`fall_mask`（含裁剪）并存到 `recorded_data` 字典
2. **绘图 cell**：使用 `fall_mask` 而非 `effective_mask`，标题注明 "falling edge"
3. **拟合 cell**：与绘图一致使用 `fall_mask`
4. 上升段（`rising_mask`）只在找零交叉点时使用，不用于画图

## 参考 Notebook

`experiments/Static_Magnetic_Field_Sensitivity_YYYYMMDD.ipynb`
