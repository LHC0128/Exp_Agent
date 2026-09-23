"""历史 HF2 投影噪声运行目录的离线兼容分析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml
from scipy import signal as scipy_signal

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lab_workflows.plotting import new_figure, save_figure, set_plot_style


def _builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _phase_psd(waveforms: np.ndarray, rate: float, nperseg: int):
    spectra = []
    frequency = None
    for waveform in np.asarray(waveforms, dtype=float):
        if not np.all(np.isfinite(waveform)):
            continue
        frequency, psd = scipy_signal.welch(
            waveform,
            fs=rate,
            window="hann",
            nperseg=min(nperseg, waveform.size),
            noverlap=min(nperseg, waveform.size) // 2,
            detrend="constant",
            scaling="density",
        )
        spectra.append(psd)
    if frequency is None or not spectra:
        raise ValueError("历史 HF2 数据中没有有效波形")
    matrix = np.asarray(spectra, dtype=float)
    return frequency, np.mean(matrix, axis=0), np.std(matrix, axis=0)


def analyze_legacy(run_dir: Path) -> dict[str, Any]:
    """读取旧版两阶段 HF2 X/Y NPZ，并生成兼容 PSD 结果。"""
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)
    config_path = run_dir / "experiment_config.yaml"
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    psd_params = config.get("psd_params", {})
    nperseg = int(psd_params.get("nperseg", 5000))
    with np.load(raw_dir / "waveforms_phase1_light.npz") as off:
        off_x = np.asarray(off["x_waveforms"], dtype=float)
        off_y = np.asarray(off["y_waveforms"], dtype=float)
        off_rate = float(off["actual_rate"])
    with np.load(raw_dir / "waveforms_thermal.npz") as on:
        on_x = np.asarray(on["x_waveforms"], dtype=float)
        on_y = np.asarray(on["y_waveforms"], dtype=float)
        on_rate = float(on["actual_rate"])
    if not np.isclose(off_rate, on_rate, rtol=1e-9, atol=0.0):
        raise ValueError("历史 HF2 两阶段实际采样率不一致")
    frequency, off_x_psd, off_x_std = _phase_psd(off_x, off_rate, nperseg)
    frequency_on, on_x_psd, on_x_std = _phase_psd(on_x, on_rate, nperseg)
    _, off_y_psd, off_y_std = _phase_psd(off_y, off_rate, nperseg)
    _, on_y_psd, on_y_std = _phase_psd(on_y, on_rate, nperseg)
    if not np.allclose(frequency, frequency_on, rtol=1e-12, atol=1e-12):
        raise ValueError("历史 HF2 两阶段 PSD 频率轴不一致")
    delta_x = on_x_psd - off_x_psd
    delta_y = on_y_psd - off_y_psd
    np.savez(
        results_dir / "legacy_psd_spectra.npz",
        frequency_hz=frequency,
        field_off_x_psd_v2_hz=off_x_psd,
        field_on_x_psd_v2_hz=on_x_psd,
        delta_x_psd_v2_hz=delta_x,
        field_off_y_psd_v2_hz=off_y_psd,
        field_on_y_psd_v2_hz=on_y_psd,
        delta_y_psd_v2_hz=delta_y,
        field_off_x_std_v2_hz=off_x_std,
        field_on_x_std_v2_hz=on_x_std,
        field_off_y_std_v2_hz=off_y_std,
        field_on_y_std_v2_hz=on_y_std,
        actual_rate_sa_s=np.float64(off_rate),
        welch_nperseg=np.int64(nperseg),
    )
    fmin = float(psd_params.get("integ_fmin", 0.5))
    fmax = min(float(psd_params.get("integ_fmax", frequency[-1])), frequency[-1])
    mask = (frequency >= fmin) & (frequency <= fmax)
    set_plot_style("paper")
    fig, axes = new_figure(nrows=2, kind="wide", height_mm=110, sharex=True)
    axes[0].semilogy(frequency[mask], off_x_psd[mask], linestyle="--", label="Field-offset background")
    axes[0].semilogy(frequency[mask], on_x_psd[mask], label="Thermal state")
    axes[0].set_ylabel("PSD (V²/Hz)")
    axes[0].set_title("Legacy HF2 Projection-Noise PSD")
    axes[0].legend()
    axes[0].grid(False)
    axes[1].plot(frequency[mask], delta_x[mask])
    axes[1].axhline(0.0, color="k", lw=0.8)
    axes[1].set_xlabel("Baseband frequency (Hz)")
    axes[1].set_ylabel("PSD difference (V²/Hz)")
    axes[1].grid(False)
    fig.set_layout_engine("constrained")
    figure_name = "legacy_projection_noise_psd.png"
    save_figure(fig, results_dir / figure_name, close=False)
    plt.close(fig)

    payload = {
        "success": True,
        "acquisition_backend": "legacy_hf2_demod_xy",
        "run_dir": str(run_dir),
        "compatibility_note": "历史 HF2 数据兼容分析；未套用新的 SDS RF 洛伦兹模型。",
        "welch": {
            "actual_rate_sa_s": off_rate,
            "nperseg": nperseg,
            "frequency_min_hz": fmin,
            "frequency_max_hz": fmax,
        },
        "files": ["legacy_psd_spectra.npz", figure_name],
    }
    payload["files"].extend(["legacy_analysis.yaml", "legacy_analysis.json"])
    with (results_dir / "legacy_analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(_builtin(payload), stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "legacy_analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(_builtin(payload), stream, ensure_ascii=False, indent=2)
    return payload
