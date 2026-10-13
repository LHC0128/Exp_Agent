# 科研绘图统一规范

项目共享绘图模块为 `lab_workflows.plotting`。默认配置为 `paper`；`nature`、`aps`
是投稿排版起点，只有明确制作 A0 海报时才选择 `a0_poster`。全部配置复用同一套
绘图与分析接口，期刊选择不得改变数据、拟合、统计方法或单位。

## 配置

| 配置 | 标准尺寸 | 宽图尺寸 | 基础字号 | 主要用途 |
|---|---:|---:|---:|---|
| `paper`（默认） | 3.4 × 2.5 in | 7.0 × 3.2 in | 8 pt | 论文单栏、双栏及实验结果图 |
| `nature` | 宽 89 mm | 宽 183 mm | 7 pt | Nature 排版起点，高度随内容指定 |
| `aps` | 宽 86 mm | 宽 178 mm | 9 pt | APS 排版起点，最终检查最小字形高度 |
| `a0_poster` | 8.0 × 4.5 in | 11.0 × 5.5 in | 18 pt | 按最终印刷尺寸排版的 A0 海报面板 |

尺寸均指最终排版尺寸。若排版软件再次缩放图片，文字与线宽也会同步缩放，应在
导出前按最终放置比例检查可读性。

`width_mm` / `height_mm` 可分别覆盖宽高；未覆盖的维度沿用配置，宽度覆盖不会
自动缩放字号或高度。它们与英寸 `figsize` 互斥。非海报配置下，`new_figure()` 在
未指定 `figsize` 或 `height_mm` 时，将多行图高度增加至至少 `2.2 × nrows` 英寸。
这只是诊断图的可读性下限；投稿组合图应按选定面板显式设置高度。
样式由 `plot_style_context()` 或 `set_plot_style()`
设置，`new_figure(profile=...)` 负责尺寸；两处使用同一个配置。

这些数值不是期刊合规认证。2026-09-20 核对的官方参考：

- [Nature 制图指南](https://research-figure-guide.nature.com/figures/building-and-exporting-figure-panels/)：89 / 183 mm，整图最高 170 mm。
- [Nature 初投稿指南](https://www.nature.com/nature/for-authors/initial-submission)：另有约 90 / 180 mm 的表述，最终尺寸下字号 5–7 pt。本项目采用制图指南的精确宽度与 7 pt。
- [APS 排版指南](https://publish.aps.org/files/styleguide-pr.pdf)：约 86 / 178 mm。
  [APS Style Basics](https://journals.aps.org/authors/style-basics) 要求最终最小大写字母/数字足够高；9 pt 是项目起点，pt 字号不等于实际字形高度，仍需检查。
- Science 官方页面本次未能读取，暂不提供已核实的专用预设；按具体稿件指南通过毫米图幅与局部 `matplotlib.rc_context()` 调整。

## 使用方式

```python
from lab_workflows.plotting import (
    COLOR_OPTIMAL,
    format_axis,
    new_figure,
    save_figure,
    plot_style_context,
)

with plot_style_context("paper"):
    fig, ax = new_figure(height_mm=65)
    ax.plot(x, y, color=COLOR_OPTIMAL, label="Data")
    format_axis(ax, xlabel="Frequency (Hz)", ylabel="ASD (V/√Hz)")
    save_figure(fig, results_dir / "noise_asd.png")  # 同时生成 noise_asd.pdf
```

A0 海报图使用 `set_plot_style("a0_poster")`，并通过
`new_figure(profile="a0_poster")` 创建对应尺寸。需要避免影响其他绘图代码时，使用
`plot_style_context()` 临时应用配置。

## 视觉与排版

- 白底、Arial 优先，默认不显示网格；需要读数时按图显式开启淡网格。
- 默认曲线 1.3 pt、标记 3.5 pt、坐标轴 0.8 pt；密集频谱允许减细曲线，不平滑替代原始结果。
- optimal 使用蓝色，traditional 使用朱红色；同一物理对象跨图保持同色。
- 数据与拟合可以同色，分别用点和实线；对照同时使用线型/标记，不能只靠颜色。
- 连续大小使用 `viridis` 等顺序色图，正负残差使用以零为中心的发散色图；相互比较的热图共享色标范围，独立归一化必须明确说明。
- 变量在数学模式中默认斜体，单位用 `\mathrm{}` 正体；图内全部标注为英文。
- 组合图直接复用接收 `ax` 的绘图函数，对齐绘图区、字号与编号；编号格式按目标期刊调整。

## 科研表达约定

- 坐标、图例和注释使用英文，并在坐标标签中明确单位。
- 默认不在单个面板内重复写标题；图意由论文图注或海报版面标题说明。
- 对数坐标只用于严格为正的数据，并保留主、次刻度。
- 参考线必须在图例或分析结果中明确标为 `Reference`，不能替代实际统计量。
- 实际统计量、拟合参数和不确定度保存在结果文件中，绘图不得修改物理分析结果。
- PSD 与 ASD 严格区分：电压 PSD 为 V²/Hz、ASD 为 V/√Hz；磁场等效 ASD 为 T/√Hz 或 fT/√Hz。名称、标签和结果字段保持一致。
- 明确幅值采用 RMS、peak 或 peak-to-peak；响应标定与噪声换算使用一致定义。
- 线宽明确为 FWHM 或 HWHM，频率明确为 Hz 或 rad/s。
- 误差条写明 SD、SEM 或置信区间及计算方法；记录独立重复次数，不把同一条记录的相关频点当作独立重复。
- 频谱记录实际采样率、窗函数、分段长度、重叠率、单/双边谱、去趋势、平均方式；平均 PSD 再开方和平均 ASD 不可混称。
- 缺失/无效数据保留掩码或 NaN，不自动补零；平滑、删点、插值、归一化记录依据与参数。

## 导出与复现

`save_figure()` 对 `.png` / `.pdf` 默认同时生成同名 PDF 和 PNG；无扩展名时返回
PDF 路径。返回值仍为调用方请求的路径，已有 GUI PNG 链接继续有效。`paired=False`
只保存请求文件，其他扩展名保持单文件输出。不要在成对导出时传入 `format=`。

PNG 默认 300 dpi；PDF 保留矢量曲线、嵌入 TrueType 字体。密集热图按需设置
`rasterized=True`，其 DPI 影响栅格区域，不影响矢量文字和线条。保存两种格式都成功
后才关闭 Figure；导出失败会抛出异常，可能留下已经完成的一种文件，应检查后重试。

默认保持最终画布尺寸，即使外部 rcParams 设置了自动裁边。显式 `bbox_inches="tight"`
仍兼容旧诊断图，但裁切后的宽高不能作为投稿图幅保证；投稿图使用固定画布内的
constrained layout，并检查标签和图例未越界。这里的双格式保存会写入同名目标文件，
请将人工编辑版本另存命名，避免重新分析覆盖。

分析输入和参数优先记录在现有 `experiment_config.yaml` 与分析结果文件中。
投稿图需能定位到源数据、独立重复、处理参数、拟合结果与代码版本；额外记录绘图
配置、依赖版本和必要的源文件哈希。调整外观不得重新选择统计口径。

当前正式 Python 分析程序和独立绘图工具已接入公共创建、保存接口，重新分析后
获得新默认样式和双格式输出。已有历史图片不会被批量重绘；Notebook 本轮仅盘点，
未修改。覆盖范围、已修复的科学表达问题和待审定项见
[绘图检查报告](plotting_audit_20260920.md)，旧入口使用方式见
[历史分析绘图入口](legacy_analysis_plotting.md)。

全扫描、多频率和多相位概览保留全部诊断面板，可能高于期刊主图尺寸限制。
投稿时应根据科学问题选择面板或分页，将完整诊断结果保留在补充材料中；不要直接
缩小整张长图，也不要为适配版面静默删点。`paper` 配置统一外观和导出，不能替代
针对最终图的单位、统计方法、图注和目标期刊尺寸检查。

## 四类可复现样图

仓库根目录运行（仅模拟数据，不连接设备）：

```powershell
agent_exp_env\Scripts\python.exe -m examples.publication_plot_gallery
agent_exp_env\Scripts\python.exe -m examples.publication_plot_gallery --profile nature --output results/publication_plot_gallery_nature
```

样例源码为 [examples/publication_plot_gallery.py](../examples/publication_plot_gallery.py)。默认输出
`results/publication_plot_gallery/`：

1. `01_noise_spectrum.pdf/png`：磁场 ASD，双条件对比，明确频段。
2. `02_response_fit.pdf/png`：模拟重复测量、SEM、加权洛伦兹拟合及残差。
3. `03_response_heatmap.pdf/png`：绝对响应色标，热图局部栅格化。
4. `04_multipanel.pdf/png`：统一编号的组合图，附独立重复与均值 ± SD。

`source_data.npz` 保存模拟输入和分析数组，`manifest.json` 记录随机种子、频谱参数、
拟合参数/不确定度、软件版本及源码哈希，`captions.md` 提供图注。固定随机种子复现
数值；PDF 文件时间戳等不保证逐字节相同。图内的 Synthetic 标记用于防止模拟样例
被误认为实测结果，正式论文标题与解释通常移至图注。

验收时核对最终物理宽高、100% 尺寸可读性、标签遮挡、单位和误差含义、灰度区分，
并确认 PDF 中的字体已嵌入、曲线未整体栅格化。
