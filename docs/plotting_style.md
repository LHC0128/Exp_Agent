# 科研绘图统一规范

项目共享绘图模块为 `lab_workflows.plotting`，提供 `paper` 和 `a0_poster`
两套配置。默认配置是 `paper`，适用于论文单栏或双栏图；只有明确制作 A0 海报时
才选择 `a0_poster`。

## 配置

| 配置 | 标准尺寸 | 宽图尺寸 | 基础字号 | 主要用途 |
|---|---:|---:|---:|---|
| `paper`（默认） | 3.4 × 1.7 in | 7.0 × 3.2 in | 8 pt | 论文单栏、双栏及实验结果图 |
| `a0_poster` | 8.0 × 4.5 in | 11.0 × 5.5 in | 18 pt | 按最终印刷尺寸排版的 A0 海报面板 |

尺寸均指最终排版尺寸。若排版软件再次缩放图片，文字与线宽也会同步缩放，应在
导出前按最终放置比例检查可读性。

## 使用方式

```python
from lab_workflows.plotting import (
    COLOR_OPTIMAL,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
)

set_plot_style()  # 默认 paper
fig, ax = new_figure()
ax.plot(x, y, color=COLOR_OPTIMAL, label="Data")
format_axis(ax, xlabel="Frequency (Hz)", ylabel="ASD (V/√Hz)")
save_figure(fig, results_dir / "noise_psd.png")
```

A0 海报图使用 `set_plot_style("a0_poster")`，并通过
`new_figure(profile="a0_poster")` 创建对应尺寸。需要避免影响其他绘图代码时，使用
`plot_style_context()` 临时应用配置。

## 科研表达约定

- 坐标、图例和注释使用英文，并在坐标标签中明确单位。
- 默认不在单个面板内重复写标题；图意由论文图注或海报版面标题说明。
- 数据、拟合、参考线分别使用标记、实线或虚线以及不同色盲友好颜色，不能只依赖颜色区分。
- 对数坐标只用于严格为正的数据，并保留主、次刻度。
- 参考线必须在图例或分析结果中明确标为 `Reference`，不能替代实际统计量。
- 实际统计量、拟合参数和不确定度保存在结果文件中，绘图不得修改物理分析结果。
- PNG 默认 300 dpi；论文最终排版优先同时导出 PDF，以保留矢量线条和可编辑文字。
