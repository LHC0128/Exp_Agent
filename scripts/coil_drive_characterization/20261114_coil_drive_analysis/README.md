---
title: 线圈驱动链路离线复核
conclusion: 固定延迟 δ≈−0.21 µs、电感 τ≈0.57 µs（L≈41 µH）；310 kHz 为无源并联谐振（Q≈9），更可能在功放输出网络或电缆上
data:
  - coil_drive_characterization/1114_114241_coil_drive
  - coil_drive_characterization/1114_165713_coil_drive
  - coil_drive_characterization/1114_170336_coil_drive
  - coil_drive_characterization/1114_171514_coil_drive
---

# 线圈驱动链路：2026-11-14 离线复核

2026-11-20 从运行目录 `data/coil_drive_characterization/_analysis_20261114/` 迁入；脚本只读上表四个运行，
产物只有 `figures/` 下的两张对比图（随本目录进 git），数据侧不再保留副本。

## 源运行

| 运行目录 | 参数 | 用途 |
|---|---|---|
| `1114_114241_coil_drive` | 10 Hz–500 kHz、4 点/十倍频、200 mA | 读窗居中修复后的验证轮 |
| `1114_165713_coil_drive` | 10 Hz–1 MHz、8 点/十倍频、100 mA | 幅度对照 |
| `1114_170336_coil_drive` | 10 Hz–1 MHz、8 点/十倍频、200 mA | 幅度对照、全带 |
| `1114_171514_coil_drive` | 200–600 kHz、40 点/十倍频、200 mA | 谐振区加密 |

## 脚本

| 脚本 | 回答的问题 |
|---|---|
| `coil_drive_step_fit.py` | 脉冲上升沿拟合 `I(t)=b+I_p(1−e^{−(t−δ)/τ})`，分离固定延迟 δ 与电感 τ（四个脉宽交叉核对） |
| `coil_drive_delay_split.py` | 用 |I/V| 与相位分别拟合 RL、RL+延迟、零极点+延迟三种模型，判断现有数据能否分离两部分 |
| `coil_drive_ringing.py` | 脉冲拖尾振铃的 f_ring / 衰减时间 / Q |
| `coil_drive_amplitude_compare.py` | 100 mA 与 200 mA 逐点对比，判断谐振是否无源 |
| `coil_drive_resonance_shape.py` | 200–600 kHz 加密轮的谷底、半功率宽度与 Q |
| `coil_drive_compare.py` | 把 41 点与 20 点两次扫频合并成 `figures/` 下的两张对比图 |

## 关键结论（2026-11-14）

- 固定延迟 δ = −0.21 µs（同步沿比驱动阶跃晚约 0.21 µs），电感 τ = 0.57 µs（L = τ·R_loop ≈ 41 µH），
  在 3–10 µs 四个脉宽与 100/200 mA 两次扫频下一致。
- 310 kHz 处是并联谐振：|I/V| 由 121 降到 55 mA/V（|Z| 118 → 261 Ω），相位从 −125.6° 回摆到
  −79.5°，半功率宽度约 34 kHz → Q ≈ 9，与脉冲拖尾振铃 Q ≈ 8 独立吻合。
- 谐振是无源的：100 mA 与 200 mA 逐点比值 0.990–0.997，不随幅度变。
- 若该谐振属于 72 Ω 回路，Q 应约 1，实测 Q ≈ 9，故该模式更可能在功放输出网络或电缆上；
  定案需要把功放输出同帧接入示波器（未做）。

## 复现

脚本经 `data_root()` 读取上面四个运行目录，只读数据。开发目录先设数据根：

```powershell
$env:LAB_DATA_ROOT = "D:\Code\exp_agent_run\data"
agent_exp_env\Scripts\python.exe scripts\coil_drive_characterization\20261114_coil_drive_analysis\<脚本>.py
```

`coil_drive_compare.py` 把图写到本目录 `figures/`。迁移时新旧位置各跑一遍，六个脚本的输出逐字一致。
