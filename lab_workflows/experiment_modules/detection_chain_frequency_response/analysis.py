"""探测链路频率响应标定离线分析。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit

from ...plotting import new_figure, save_figure, set_plot_style

if TYPE_CHECKING:
    from .models import DetectionChainFrequencyResponseParams


def analyze(
    params: DetectionChainFrequencyResponseParams,
    run_dir: Path,
) -> None:
    """分析探测链路频率响应数据。
    
    读取 raw/ 中保存的频率响应数据，
    绘制响应曲线，可选拟合 BPD 理论模型。
    """
    set_plot_style("paper")
    
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # 加载数据
    data = np.load(raw_dir / "frequency_response.npz")
    am_freqs = data["am_frequencies_hz"]
    peak_amps = data["peak_amplitudes"]
    
    # 历史数据也排除零基带点：Welch 默认去均值，原有谱峰不是直流响应。
    zero_baseband = np.isclose(am_freqs, params.hf2_osc_freq_hz, rtol=0, atol=1e-9)
    peak_amps = np.where(zero_baseband, np.nan, peak_amps)
    valid = np.isfinite(peak_amps) & (peak_amps > 0)
    if not np.any(valid):
        print("无有效数据点")
        return
    
    # 两侧各取最近的有效点，避免用已经失效的零基带点归一化。
    reference_indices = []
    for side in (am_freqs < params.hf2_osc_freq_hz, am_freqs > params.hf2_osc_freq_hz):
        candidates = np.flatnonzero(valid & side)
        if candidates.size:
            reference_indices.append(candidates[np.argmin(abs(am_freqs[candidates] - params.hf2_osc_freq_hz))])
    norm_factor = float(np.mean(peak_amps[reference_indices]))
    peak_amps_norm = peak_amps / norm_factor
    
    print(f"\n有效数据点: {np.sum(valid)}/{len(am_freqs)}")
    print(f"归一化参考: {norm_factor:.3e} V/√Hz，AM 频率 {am_freqs[reference_indices].tolist()} Hz")
    
    # 计算基带频率对应的响应下降
    # 15-25 kHz 基带对应的 AM 频率
    baseband_15k = [75000, 105000]  # 下边带、上边带
    baseband_25k = [65000, 115000]
    
    def get_response_at_freq(freq):
        idx = np.argmin(np.abs(am_freqs - freq))
        if valid[idx]:
            return peak_amps_norm[idx]
        return np.nan
    
    resp_75k = get_response_at_freq(75000)
    resp_105k = get_response_at_freq(105000)
    resp_65k = get_response_at_freq(65000)
    resp_115k = get_response_at_freq(115000)
    
    if not np.isnan(resp_105k) and not np.isnan(resp_115k):
        drop_upper = (resp_115k - resp_105k) / resp_105k * 100
        slope_upper = drop_upper / 10  # 10 kHz span
        print(f"\n上边带 (105-115 kHz): {drop_upper:+.4f}% 总下降")
        print(f"  斜率: {slope_upper:+.4f}% / kHz (基带)")
    
    if not np.isnan(resp_75k) and not np.isnan(resp_65k):
        drop_lower = (resp_65k - resp_75k) / resp_75k * 100
        slope_lower = drop_lower / 10
        print(f"\n下边带 (75-65 kHz): {drop_lower:+.4f}% 总下降")
        print(f"  斜率: {slope_lower:+.4f}% / kHz (基带)")
    
    if all(not np.isnan(x) for x in [resp_75k, resp_105k, resp_65k, resp_115k]):
        avg_15k = (resp_75k + resp_105k) / 2
        avg_25k = (resp_65k + resp_115k) / 2
        drop_dsb = (avg_25k - avg_15k) / avg_15k * 100
        slope_dsb = drop_dsb / 10
        print(f"\n双边带平均 (15-25 kHz 基带): {drop_dsb:+.4f}% 总下降")
        print(f"  斜率: {slope_dsb:+.4f}% / kHz")
        print("\n与噪声谱实验对比:")
        print(f"  噪声谱 N_S1 下降: -0.010% / kHz")
        print(f"  本次标定测量: {slope_dsb:+.4f}% / kHz")
        print("  两种斜率仅作对照，不按比例推算噪声贡献。")
    
    # 绘图
    fig, axes = new_figure(nrows=2, ncols=2, kind="wide", height_mm=120)
    
    # (a) 归一化响应 vs AM 频率
    ax = axes[0, 0]
    ax.plot(am_freqs / 1000, peak_amps_norm, 'bo-', markersize=4, linewidth=1.5)
    ax.axvline(params.hf2_osc_freq_hz / 1000, color='gray', linestyle='--', alpha=0.5, label='Zero baseband (excluded)')
    ax.axvline(75, color='r', linestyle='--', alpha=0.5)
    ax.axvline(105, color='r', linestyle='--', alpha=0.5, label='±15 kHz sidebands')
    ax.axvline(65, color='orange', linestyle='--', alpha=0.5)
    ax.axvline(115, color='orange', linestyle='--', alpha=0.5, label='±25 kHz sidebands')
    ax.set_xlabel('AM Modulation Frequency (kHz)')
    ax.set_ylabel('Normalized Response')
    ax.set_title('(a) Detection Chain Frequency Response')
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # (b) 绝对响应幅度
    ax = axes[0, 1]
    ax.plot(am_freqs / 1000, peak_amps, 'go-', markersize=4, linewidth=1.5)
    ax.axvline(params.hf2_osc_freq_hz / 1000, color='gray', linestyle='--', alpha=0.5)
    ax.set_xlabel('AM Modulation Frequency (kHz)')
    ax.set_ylabel('Peak ASD (V/sqrt(Hz))')
    ax.set_title('(b) Absolute Response ASD')
    ax.grid(True, alpha=0.3)
    ax.set_yscale('log')
    
    # (c) 基带频率表示
    ax = axes[1, 0]
    baseband_freqs = np.abs(am_freqs - params.hf2_osc_freq_hz)
    ax.plot(baseband_freqs / 1000, peak_amps_norm, 'mo-', markersize=4, linewidth=1.5)
    ax.axvline(15, color='r', linestyle='--', alpha=0.5, label='15 kHz')
    ax.axvline(25, color='orange', linestyle='--', alpha=0.5, label='25 kHz')
    ax.set_xlabel('Baseband Frequency (kHz)')
    ax.set_ylabel('Normalized Response')
    ax.set_title('(c) Response vs Baseband Frequency')
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # (d) 相对变化百分比
    ax = axes[1, 1]
    rel_change = (peak_amps_norm - 1) * 100
    ax.plot(baseband_freqs / 1000, rel_change, 'ko-', markersize=4, linewidth=1.5)
    ax.axhline(0, color='gray', linestyle=':', alpha=0.5)
    ax.axvline(15, color='r', linestyle='--', alpha=0.5)
    ax.axvline(25, color='orange', linestyle='--', alpha=0.5)
    ax.set_xlabel('Baseband Frequency (kHz)')
    ax.set_ylabel('Relative Change (%)')
    ax.set_title('(d) Response Change Relative to Nearest Valid Points')
    ax.grid(True, alpha=0.3)
    
    fig.set_layout_engine("constrained")
    save_figure(fig, results_dir / "frequency_response.png", close=True)
    
    # 保存数值结果
    np.savez(
        results_dir / "analysis_results.npz",
        am_frequencies_hz=am_freqs,
        peak_amplitudes=peak_amps,
        peak_amplitudes_normalized=peak_amps_norm,
        baseband_frequencies_hz=baseband_freqs,
        norm_factor=norm_factor,
        normalization_reference_am_frequencies_hz=am_freqs[reference_indices],
        excluded_zero_baseband=zero_baseband,
    )
    
    print(f"\n结果已保存至: {results_dir}")


def main() -> int:
    from ...experiment_runtime import runtime_run_dir
    from .models import DetectionChainFrequencyResponseParams

    run_dir = runtime_run_dir()
    config = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8")) or {}
    params = DetectionChainFrequencyResponseParams.from_external(config["parameters"])
    analyze(params, run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
