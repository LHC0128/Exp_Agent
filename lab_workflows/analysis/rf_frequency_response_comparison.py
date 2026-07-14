"""对比多个 RF 频率扫描运行的幅频响应。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml


@dataclass(frozen=True)
class FrequencyResponseSeries:
    """单次运行中用于对比的幅频响应。"""

    run_dir: Path
    label: str
    frequency_hz: np.ndarray
    response_v: np.ndarray


def load_frequency_response_series(run_dir: str | Path) -> FrequencyResponseSeries:
    """读取一次运行已生成的 R 中位数幅频响应。"""

    run_dir = Path(run_dir).resolve()
    analysis_path = run_dir / "results" / "analysis.json"
    config_path = run_dir / "experiment_config.yaml"

    if not analysis_path.is_file():
        raise FileNotFoundError(f"未找到分析结果: {analysis_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")

    with analysis_path.open(encoding="utf-8") as file:
        analysis = json.load(file)
    with config_path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    response = analysis.get("freq_response", {})
    try:
        frequency_hz = np.asarray(response["freq_Hz"], dtype=float)
        response_v = np.asarray(response["R_V_median"], dtype=float)
    except KeyError as exc:
        raise KeyError(f"{analysis_path} 缺少 freq_response.{exc.args[0]}") from exc

    if frequency_hz.ndim != 1 or response_v.ndim != 1:
        raise ValueError(f"{analysis_path} 中的频率和响应必须是一维数组")
    if frequency_hz.size != response_v.size:
        raise ValueError(
            f"{analysis_path} 中的频率点数 ({frequency_hz.size}) 与响应点数 "
            f"({response_v.size}) 不一致"
        )

    valid = np.isfinite(frequency_hz) & np.isfinite(response_v)
    if not np.any(valid):
        raise ValueError(f"{analysis_path} 中没有有效的幅频响应数据")

    waveform_file = config.get("xy_direct_aw", {}).get("ARB_WAVEFORM_FILE")
    label = run_dir.name
    if waveform_file:
        label = f"{label} ({waveform_file})"

    return FrequencyResponseSeries(
        run_dir=run_dir,
        label=label,
        frequency_hz=frequency_hz[valid],
        response_v=response_v[valid],
    )


def compare_frequency_response_amplitude(
    run_dirs: Sequence[str | Path],
    output_dir: str | Path,
    *,
    output_name: str = "freq_response_amplitude_comparison.png",
    show_figure: bool = False,
) -> Path:
    """叠加多个运行的 R 中位数幅频响应并保存图片。"""

    if len(run_dirs) < 2:
        raise ValueError("幅频响应对比至少需要两个运行目录")

    series_list = [load_frequency_response_series(run_dir) for run_dir in run_dirs]
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    with plt.rc_context(
        {
            "figure.dpi": 120,
            "font.size": 11,
            "axes.labelsize": 12,
        }
    ):
        fig, ax = plt.subplots(figsize=(11, 6.2))
        for series in series_list:
            ax.plot(
                series.frequency_hz,
                series.response_v,
                ".-",
                linewidth=1.4,
                markersize=4,
                label=series.label,
            )

        ax.set_xlabel("Z RF Frequency (Hz)")
        ax.set_ylabel("Demod 3 response (V)")
        ax.set_title("RF Field Frequency Response Comparison - Median Amplitude")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path, dpi=150, bbox_inches="tight")

        if show_figure:
            plt.show()
        else:
            plt.close(fig)

    return output_path
