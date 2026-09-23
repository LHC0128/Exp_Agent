"""项目统一的论文图与 A0 海报实验绘图样式。

样式迁移自 ``D:/Code/exp/optimal_control/poster_style.py``。本模块不创建固定
输出目录，调用方必须显式传入结果路径，以符合单次实验 ``results/`` 目录契约。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal

import matplotlib as mpl
import numpy as np


# 色盲友好配色（Wong 2011 / Nature 风格）。
COLOR_OPTIMAL = "#0072B2"
COLOR_TRAD = "#D55E00"
COLOR_GREEN = "#009E73"
COLOR_ORANGE = "#E69F00"
COLOR_PURPLE = "#CC79A7"
COLOR_GRAY = "#999999"
COLOR_CYAN = "#56B4E9"
COLOR_BROWN = "#8C564B"


# 两套尺寸均表示最终排版尺寸，单位为英寸。
PlotProfile = Literal["paper", "nature", "aps", "a0_poster"]
FigureKind = Literal["standard", "wide", "square"]
DEFAULT_PROFILE: PlotProfile = "paper"

PAPER_FIG_HEIGHT = 2.5
PAPER_STANDARD = (3.4, PAPER_FIG_HEIGHT)
PAPER_WIDE = (7.0, 3.2)
PAPER_SQUARE = (3.3, 3.3)

A0_POSTER_FIG_HEIGHT = 4.5
A0_POSTER_STANDARD = (8.0, A0_POSTER_FIG_HEIGHT)
A0_POSTER_WIDE = (11.0, 5.5)
A0_POSTER_SQUARE = (7.0, 7.0)

# 默认别名始终指向论文图配置。
FIG_HEIGHT = PAPER_FIG_HEIGHT
STANDARD = PAPER_STANDARD
WIDE = PAPER_WIDE
SQUARE = PAPER_SQUARE

# 历史别名，便于从原始 poster_style.py 迁移。
SINGLE_WIDE = STANDARD
SINGLE_TALL = (3.3, PAPER_FIG_HEIGHT)
DOUBLE_WIDE = PAPER_WIDE
SMALL = (3.3, FIG_HEIGHT)


PAPER_RCPARAMS: dict[str, Any] = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 8,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "axes.titlesize": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 120,
    "savefig.dpi": 300,
    "savefig.bbox": None,
    "savefig.pad_inches": 0.08,
    "savefig.format": "pdf",
    "axes.linewidth": 0.8,
    "axes.grid": False,
    "axes.prop_cycle": mpl.cycler(color=[
        COLOR_OPTIMAL, COLOR_TRAD, COLOR_GREEN, COLOR_PURPLE,
        COLOR_ORANGE, COLOR_CYAN, COLOR_GRAY,
    ]),
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
    "grid.linewidth": 0.5,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.size": 3.5,
    "ytick.major.size": 3.5,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.minor.size": 2.0,
    "ytick.minor.size": 2.0,
    "xtick.minor.width": 0.6,
    "ytick.minor.width": 0.6,
    "xtick.minor.visible": True,
    "ytick.minor.visible": True,
    "lines.linewidth": 1.3,
    "lines.markersize": 3.5,
    "lines.markeredgewidth": 0.8,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "mathtext.fontset": "dejavusans",
    "mathtext.default": "it",
    "axes.formatter.use_mathtext": True,
    "axes.unicode_minus": True,
    "figure.constrained_layout.use": True,
}


A0_POSTER_RCPARAMS: dict[str, Any] = {
    **PAPER_RCPARAMS,
    "font.size": 18,
    "axes.titlesize": 20,
    "axes.labelsize": 20,
    "xtick.labelsize": 16,
    "ytick.labelsize": 16,
    "legend.fontsize": 16,
    "axes.linewidth": 1.5,
    "grid.linewidth": 0.9,
    "xtick.major.size": 7,
    "ytick.major.size": 7,
    "xtick.major.width": 1.4,
    "ytick.major.width": 1.4,
    "xtick.minor.size": 4,
    "ytick.minor.size": 4,
    "xtick.minor.width": 1.0,
    "ytick.minor.width": 1.0,
    "lines.linewidth": 2.5,
    "lines.markersize": 8,
    "lines.markeredgewidth": 1.2,
    "savefig.pad_inches": 0.12,
}

PROFILE_RCPARAMS: dict[PlotProfile, dict[str, Any]] = {
    "paper": PAPER_RCPARAMS,
    # 投稿起点，不代替具体期刊、稿件类型和最终缩放检查。
    "nature": {**PAPER_RCPARAMS, **dict.fromkeys((
        "font.size", "axes.titlesize", "axes.labelsize",
        "xtick.labelsize", "ytick.labelsize", "legend.fontsize",
    ), 7)},
    "aps": {**PAPER_RCPARAMS, **dict.fromkeys((
        "font.size", "axes.titlesize", "axes.labelsize",
        "xtick.labelsize", "ytick.labelsize", "legend.fontsize",
    ), 9)},
    "a0_poster": A0_POSTER_RCPARAMS,
}

# 兼容原始模块名称；新代码优先使用 PAPER_RCPARAMS / A0_POSTER_RCPARAMS。
POSTER_RCPARAMS = A0_POSTER_RCPARAMS


def _validate_profile(profile: PlotProfile) -> PlotProfile:
    if profile not in PROFILE_RCPARAMS:
        raise ValueError(f"未知绘图配置: {profile}")
    return profile


def figure_size(
    kind: FigureKind = "standard",
    *,
    profile: PlotProfile = DEFAULT_PROFILE,
    width_mm: float | None = None,
    height_mm: float | None = None,
) -> tuple[float, float]:
    """返回指定配置的最终排版尺寸。"""
    _validate_profile(profile)
    sizes = {
        "paper": {
            "standard": PAPER_STANDARD,
            "wide": PAPER_WIDE,
            "square": PAPER_SQUARE,
        },
        "a0_poster": {
            "standard": A0_POSTER_STANDARD,
            "wide": A0_POSTER_WIDE,
            "square": A0_POSTER_SQUARE,
        },
    }
    base_profile = "paper" if profile in {"nature", "aps"} else profile
    if kind not in sizes[base_profile]:
        raise ValueError(f"未知图尺寸类型: {kind}")
    width, height = sizes[base_profile][kind]
    if profile in {"nature", "aps"}:
        columns = {"nature": (89.0, 183.0), "aps": (86.0, 178.0)}
        new_width = columns[profile][kind == "wide"] / 25.4
        height *= new_width / width
        width = new_width
    if width_mm is not None:
        width = float(width_mm) / 25.4
    if height_mm is not None:
        height = float(height_mm) / 25.4
    if not all(np.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("图幅宽高必须是有限正数")
    return width, height


def set_plot_style(profile: PlotProfile = DEFAULT_PROFILE) -> None:
    """将指定科研绘图配置应用到后续 Matplotlib 绘图。"""
    mpl.rcParams.update(PROFILE_RCPARAMS[_validate_profile(profile)])


def plot_style_context(
    profile: PlotProfile = DEFAULT_PROFILE,
) -> AbstractContextManager[None]:
    """返回临时科研绘图配置上下文，退出后恢复调用方设置。"""
    return mpl.rc_context(PROFILE_RCPARAMS[_validate_profile(profile)])


def set_poster_style() -> None:
    """兼容原始接口：将 A0 海报样式应用到后续绘图。"""
    set_plot_style("a0_poster")


def poster_style_context() -> AbstractContextManager[None]:
    """兼容原始接口：返回临时 A0 海报样式上下文。"""
    return plot_style_context("a0_poster")


def blues_gradient(count: int) -> np.ndarray:
    """返回由浅到深的蓝色渐变。"""
    return mpl.colormaps["Blues"](np.linspace(0.35, 0.9, count))


def reds_gradient(count: int) -> np.ndarray:
    """返回由浅到深的红色渐变。"""
    return mpl.colormaps["Reds"](np.linspace(0.35, 0.9, count))


def plasma_gradient(count: int) -> np.ndarray:
    """返回 plasma 渐变。"""
    return mpl.colormaps["plasma"](np.linspace(0.05, 0.95, count))


def viridis_gradient(count: int) -> np.ndarray:
    """返回 viridis 渐变。"""
    return mpl.colormaps["viridis"](np.linspace(0.05, 0.95, count))


def new_figure(
    figsize: tuple[float, float] | None = None,
    nrows: int = 1,
    ncols: int = 1,
    *,
    profile: PlotProfile = DEFAULT_PROFILE,
    kind: FigureKind = "standard",
    width_mm: float | None = None,
    height_mm: float | None = None,
    **kwargs: Any,
) -> tuple[Any, Any]:
    """用统一尺寸创建 Figure 和 Axes。"""
    import matplotlib.pyplot as plt

    if figsize is not None and (width_mm is not None or height_mm is not None):
        raise ValueError("figsize 与毫米图幅不能同时指定")
    if figsize is None:
        figsize = figure_size(kind, profile=profile, width_mm=width_mm, height_mm=height_mm)
        if height_mm is None and profile != "a0_poster" and nrows > 1:
            # 多行诊断图为每行保留文字、图例和坐标空间；显式尺寸仍由调用方决定。
            figsize = (figsize[0], max(figsize[1], 2.2 * nrows))
    return plt.subplots(nrows, ncols, figsize=figsize, **kwargs)


def save_figure(
    fig: Any,
    path: str | Path,
    *,
    dpi: int = 300,
    close: bool = True,
    paired: bool = True,
    **kwargs: Any,
) -> Path:
    """PNG/PDF 默认成对保存，返回原请求路径；其他格式保持单文件行为。

    默认保持画布尺寸，不受外部 savefig.bbox 设置影响。显式 tight 仅用于诊断图。
    paired=False 可只保存请求格式。两次保存均成功后才关闭图像。
    """
    import matplotlib.pyplot as plt

    output_path = Path(path)
    if not output_path.suffix:
        output_path = output_path.with_suffix(".pdf")
    paths = [output_path]
    if paired and output_path.suffix.lower() in {".png", ".pdf"}:
        if "format" in kwargs:
            raise ValueError("成对导出由扩展名决定格式，请移除 format 或设置 paired=False")
        paths.append(output_path.with_suffix(".pdf" if output_path.suffix.lower() == ".png" else ".png"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with mpl.rc_context({"savefig.bbox": None}):
        for destination in paths:
            fig.savefig(destination, dpi=dpi, **kwargs)
    if close:
        plt.close(fig)
    return output_path


def style_legend(
    ax: Any,
    *,
    loc: str = "best",
    fontsize: float | None = None,
    ncol: int = 1,
    **kwargs: Any,
) -> Any:
    """应用统一图例样式。"""
    if fontsize is None:
        fontsize = float(mpl.rcParams["legend.fontsize"])
    legend = ax.legend(loc=loc, fontsize=fontsize, ncol=ncol, **kwargs)
    if legend and legend.get_frame_on():
        legend.get_frame().set_linewidth(0.6)
    return legend


def format_axis(ax: Any, *, xlabel: str = "", ylabel: str = "") -> Any:
    """应用统一坐标标签样式；科研图面板默认不设置标题。"""
    if xlabel:
        ax.set_xlabel(xlabel, fontweight="normal")
    if ylabel:
        ax.set_ylabel(ylabel, fontweight="normal")
    return ax
