---
title: 论文用常数控制与优化控制对比
conclusion: 1013_224833（常数）与 1013_231737（优化）两次运行：零场斜率提高 59.4%，3–150 Hz 共同频带灵敏度降低 23.3%
data:
  - RF_Field_Sensitivity_AW/1013_224833_rf_sens_aw
  - RF_Field_Sensitivity_AW/1013_231737_rf_sens_aw
---

# 论文用常数控制与优化控制对比

独立入口 `RF_Field_Sensitivity_AW_compare.py` 用于同一设置下的两次运行，顺序为常数控制、
优化控制。它读取各自的 `experiment_config.yaml` 和 `raw/`，保留原色散模型与 R 扫频线宽拟合方法，
重算 X 噪声 Welch PSD，不依赖原 `results/` 缓存，不改写采集数据、分析状态或 GUI 默认参数。
这是独立的离线工具，分析、绘图、格式校验和测试都在本目录；不导入 GUI 或正式实验工作流。
唯一的项目依赖是公共绘图样式 `lab_workflows.plotting`，请继续使用仓库已安装的虚拟环境。

下例的两次运行由实验者确认使用 RMS 场强标定：`B_rf,rms = Vpp × 542.89 nT/V`。
沿用运行内保存的标定系数，不额外乘除 `sqrt(2)`；负号表示相位相反的有符号 RMS 幅度。
程序面向这一标定约定，其他幅值定义的数据使用前须核对。

```powershell
$D = "D:\Code\exp_agent_run\data\RF_Field_Sensitivity_AW"
agent_exp_env\Scripts\python.exe scripts\RF_Field_Sensitivity_AW\20261017_paper_comparison\RF_Field_Sensitivity_AW_compare.py `
  "$D\1013_224833_rf_sens_aw" `
  "$D\1013_231737_rf_sens_aw" `
  --output .cache\paper_compare\manual_compare_01
```

输出目录必须是新目录，且不能与任一源运行目录重叠；可用 `--band LOW_HZ HIGH_HZ` 指定共同
统计频带，默认 3–150 Hz。程序核对运行资格、X 标定/X 噪声来源、零 RF 输出快照、
控制包络类型和其他实验参数的一致性；不对不一致的噪声频率网格自动插值。

论文比较使用零 RF 噪声工作点处的局部斜率
`g0 = |a (gamma² - B0²) / (gamma² + B0²)²|`，同时保留原分析器的拟合中心斜率
`|a|/gamma²` 作为诊断。两者在拟合中心偏离零场时略有区别。斜率误差为无权色散拟合
完整协方差传播的 1 SE，包含参数相关性，以拟合残差估计方差；不把相关时序样点的标准差
当成独立重复的 SEM。图中响应点的误差条是单点时序 SD，通常小于标记。

每条噪声使用实际采样率、周期 Hann 窗、配置段长、50% 重叠和常数去趋势；先等权平均
PSD，再开方，除以 `g0`，最后沿用 `sqrt(1+(f_a/HWHM)²)` 线宽补偿。两次运行在同一
频带逐频点取中位数，频带必须低于两次实测 HWHM。95% 噪声统计区间通过各自整条记录
独立重采样 2000 次获得，不把频率点当作独立重复；固定斜率和线宽，因此不包括场强标定、
模型或长期漂移系统误差。每种控制只有一次完整运行，40 条噪声记录不能替代运行间重复性。
R 扫频线宽给出单极点近似补偿，不代表完整 X 幅度调制传递已经独立标定。

输出包含四组 PDF/PNG：`rf_control_response`（图 c）、`rf_control_sensitivity`（图 d）、
`rf_control_comparison`（并排预览）和 `rf_control_diagnostics`（拟合残差、输出 ASD、
未补偿场等效 ASD 和补偿因子）。谱线不平滑，灰色区域标记共同统计频带。
`comparison_source_data.npz` 保存完整数值数组，`comparison_metrics.csv` 保存核心指标，
`comparison.json` 保存参数、协方差、噪声统计区间、处理方法及输入/代码 SHA-256。

## 文件与验证

- `RF_Field_Sensitivity_AW_compare.py`：命令行入口。
- `paper_comparison.py`：读取原始记录、PSD、公共频带统计及输出。
- `paper_comparison_core.py`：固定本次分析的格式校验、色散及 R 扫频拟合。
- `paper_comparison_plot.py`：公共 paper 样式的论文面板与诊断图。
- `test_paper_comparison.py`：数值、原始记录只读、命令行和异常输入回归。

在开发目录执行验证：

```powershell
agent_exp_env\Scripts\python.exe -m pytest scripts\RF_Field_Sensitivity_AW\20261017_paper_comparison\test_paper_comparison.py -q
```

原已发布的图和数值仍保存在 `D:\Code\exp_agent\results\RF_Field_Sensitivity_AW_comparison\20261017_paper`。
再次运行只生成新结果，不自动修改论文，也不覆盖原始数据。迁移前的分析记录保留原样。

2026-11-20 从运行目录 `data/RF_Field_Sensitivity_AW/` 根目录迁入，代码字节未改。迁移核验：测试 14 项通过；
用上例两次运行重算，`comparison_metrics.csv`、`comparison.json` 的比较量与 20261017_paper 已发布结果在浮点末位以内一致
（相对差约 1e-15）。`comparison.json` 的 `code_sha256` 键是代码绝对路径，迁移后路径改变，哈希值不变。

本工具产出的数据已用于论文，按 AGENTS.md「探索档与正式档」属于升正式档的触发条件，待决定是否升档。
