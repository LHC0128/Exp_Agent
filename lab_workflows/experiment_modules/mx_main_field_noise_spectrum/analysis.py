"""Mx 主磁场控制噪声谱离线分析。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import yaml
from scipy import signal as scipy_signal

from ...analysis.noise_spectrum_separation import fit_noise_separation
from ...experiment_runtime import runtime_run_dir
from ...plotting import (
    COLOR_CYAN,
    COLOR_OPTIMAL,
    COLOR_TRAD,
    format_axis,
    new_figure,
    save_figure,
    set_plot_style,
    style_legend,
)
from .models import MxMainFieldNoiseSpectrumParams

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))


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


def _load_params(
    run_dir: Path,
) -> tuple[MxMainFieldNoiseSpectrumParams, dict[str, Any]]:
    config_path = run_dir / "experiment_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"未找到实验配置: {config_path}")
    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = MxMainFieldNoiseSpectrumParams.from_external(
        config.get("parameters", {}),
        schema_version=int(config.get("schema_version", 1)),
    )
    errors = params.validate_model()
    if errors:
        raise ValueError("；".join(errors))
    return params, config


def _load_waveforms(raw_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("waveform_I*.npz")):
        with np.load(path) as data:
            records.append(
                {
                    "path": path,
                    "point_index": int(data["point_index"]),
                    "control_frequency_hz": float(data["control_frequency_hz"]),
                    "signed_detuning_hz": float(data["signed_detuning_hz"]),
                    "resonance_frequency_hz": float(
                        data["resonance_frequency_hz"]
                    ),
                    "main_field_current_ma": float(
                        data["main_field_current_ma"]
                    ),
                    "actual_rate_sa_s": float(data["actual_rate_sa_s"]),
                    "r_v": np.asarray(data["r_v"], dtype=float).reshape(-1),
                }
            )
    if len(records) < 10:
        raise ValueError(
            f"至少需要 10 个完整主场噪声点，当前仅发现 {len(records)} 个"
        )
    records.sort(key=lambda item: int(item["point_index"]))
    return records


def _compute_psd_matrix(
    records: list[dict[str, Any]], nperseg: int
) -> tuple[np.ndarray, np.ndarray]:
    rows: list[np.ndarray] = []
    frequency_axis: np.ndarray | None = None
    for record in records:
        waveform = np.asarray(record["r_v"], dtype=float)
        waveform = waveform[np.isfinite(waveform)]
        if waveform.size < 8:
            raise ValueError(f"R 有效样本不足 8: {record['path']}")
        point_nperseg = min(int(nperseg), waveform.size)
        frequency, psd = scipy_signal.welch(
            waveform,
            fs=float(record["actual_rate_sa_s"]),
            nperseg=point_nperseg,
            noverlap=None,
            scaling="density",
        )
        if frequency_axis is None:
            frequency_axis = frequency
        elif frequency.shape != frequency_axis.shape or not np.allclose(
            frequency, frequency_axis, rtol=1e-10, atol=1e-10
        ):
            raise ValueError("各主场点的实际采样率或 Welch 频率轴不一致")
        rows.append(psd)
    assert frequency_axis is not None
    return frequency_axis, np.asarray(rows, dtype=float)


def _plot_control_axis(
    results_dir: Path,
    main_field_current_ma: np.ndarray,
    control_frequency_hz: np.ndarray,
) -> str:
    filename = "main_field_control_axis.png"
    figure, axis = new_figure()
    axis.plot(
        main_field_current_ma,
        control_frequency_hz / 1000.0,
        "o-",
        color=COLOR_OPTIMAL,
    )
    format_axis(
        axis,
        xlabel="GS200 current (mA)",
        ylabel="Control frequency (kHz)",
    )
    save_figure(figure, results_dir / filename)
    return filename


def _plot_psd_matrix(
    results_dir: Path,
    frequency_axis_hz: np.ndarray,
    control_frequency_hz: np.ndarray,
    psd_matrix: np.ndarray,
) -> str:
    filename = "noise_spectrum_2d.png"
    figure, axis = new_figure(kind="square")
    log_psd = np.log10(np.maximum(psd_matrix, np.finfo(float).tiny))
    image = axis.pcolormesh(
        frequency_axis_hz / 1000.0,
        control_frequency_hz / 1000.0,
        log_psd,
        shading="auto",
        cmap="inferno",
    )
    diagonal_max = min(
        float(frequency_axis_hz[-1]), float(control_frequency_hz[-1])
    )
    axis.plot(
        [0.0, diagonal_max / 1000.0],
        [0.0, diagonal_max / 1000.0],
        color=COLOR_CYAN,
        linestyle="--",
        label="Expected ridge",
    )
    format_axis(
        axis,
        xlabel="Demod0 R PSD frequency (kHz)",
        ylabel="Main-field control frequency (kHz)",
    )
    style_legend(axis)
    figure.colorbar(image, ax=axis, label="log10 PSD (V²/Hz)")
    save_figure(figure, results_dir / filename)
    return filename


def _plot_extracted_spectra(
    results_dir: Path,
    frequency_axis_hz: np.ndarray,
    s_beta: np.ndarray,
    n_s1: np.ndarray,
) -> str:
    filename = "noise_spectra_extracted.png"
    figure, axes = new_figure(nrows=1, ncols=2, kind="wide")
    for axis, values, color, ylabel in (
        (
            axes[0],
            s_beta,
            COLOR_OPTIMAL,
            "$S_\\beta(\\omega)$ (V²/Hz)",
        ),
        (
            axes[1],
            n_s1,
            COLOR_TRAD,
            "$N_{S_1}(\\omega)$ (V²/Hz)",
        ),
    ):
        valid = (
            np.isfinite(frequency_axis_hz)
            & np.isfinite(values)
            & (frequency_axis_hz > 0)
            & (values > 0)
        )
        axis.loglog(frequency_axis_hz[valid], values[valid], color=color)
        format_axis(axis, xlabel="Frequency (Hz)", ylabel=ylabel)
    save_figure(figure, results_dir / filename)
    return filename


def analyze(run_dir: Path) -> dict[str, Any]:
    """只读取指定运行目录的原始 R 波形并写入 ``results/``。"""
    set_plot_style("paper")
    run_dir = Path(run_dir).resolve()
    params, config = _load_params(run_dir)
    raw_dir = run_dir / "raw"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    records = _load_waveforms(raw_dir)

    point_indices = np.asarray(
        [record["point_index"] for record in records], dtype=int
    )
    saved_control_hz = np.asarray(
        [record["control_frequency_hz"] for record in records], dtype=float
    )
    saved_signed_detuning_hz = np.asarray(
        [record["signed_detuning_hz"] for record in records], dtype=float
    )
    saved_resonance_hz = np.asarray(
        [record["resonance_frequency_hz"] for record in records], dtype=float
    )
    main_field_current_ma = np.asarray(
        [record["main_field_current_ma"] for record in records], dtype=float
    )
    actual_rate_sa_s = np.asarray(
        [record["actual_rate_sa_s"] for record in records], dtype=float
    )
    calibrated_resonance_hz = (
        params.main_field_calibration_hz_per_ma * main_field_current_ma
        + params.main_field_calibration_intercept_hz
    )
    calibrated_control_hz = (
        params.hf2_reference_frequency_hz - calibrated_resonance_hz
    )
    calibrated_signed_detuning_hz = -calibrated_control_hz
    if not np.allclose(
        calibrated_resonance_hz, saved_resonance_hz, rtol=0.0, atol=1e-6
    ):
        raise ValueError("原始数据中的主场电流不满足保存的频率标定式")
    if not np.allclose(
        calibrated_control_hz, saved_control_hz, rtol=0.0, atol=1e-6
    ):
        raise ValueError("原始数据中的正控制频率不满足保存的标定式")
    if not np.allclose(
        calibrated_signed_detuning_hz,
        saved_signed_detuning_hz,
        rtol=0.0,
        atol=1e-6,
    ):
        raise ValueError("原始数据中的有符号失谐不满足保存的标定式")
    if np.any(np.diff(calibrated_control_hz) <= 0):
        raise ValueError("主场控制频率轴必须严格递增")

    frequency_axis_hz, psd_matrix = _compute_psd_matrix(
        records, params.welch_nperseg
    )
    np.savez(
        results_dir / "psd_matrix.npz",
        psd_matrix=psd_matrix,
        frequency_axis_hz=frequency_axis_hz,
        control_frequency_hz=calibrated_control_hz,
        signed_detuning_hz=calibrated_signed_detuning_hz,
        resonance_frequency_hz=calibrated_resonance_hz,
        main_field_current_ma=main_field_current_ma,
        point_index=point_indices,
        actual_rate_sa_s=actual_rate_sa_s,
        welch_nperseg=np.int64(params.welch_nperseg),
    )

    fit = fit_noise_separation(
        psd_matrix,
        frequency_axis_hz,
        calibrated_control_hz,
        peak_margin_hz=params.fit_peak_margin_hz,
        gamma_guess_hz=params.fit_gamma_guess_hz,
    )
    np.savez(
        results_dir / "popt_fit.npz",
        popt=fit.parameters,
        perr=fit.uncertainties,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
        frequency_axis_hz=frequency_axis_hz,
        param_names=np.asarray(["gamma", "Amp", "D", "dw"], dtype=str),
    )
    np.savez(
        results_dir / "noise_spectra.npz",
        S_beta=fit.s_beta,
        N_S1=fit.n_s1,
        freq_axis=frequency_axis_hz,
        omega_ctrl=calibrated_control_hz,
        signed_detuning_hz=calibrated_signed_detuning_hz,
        main_field_current_ma=main_field_current_ma,
        fit_mask=fit.fit_mask,
        interpolated_mask=fit.interpolated_mask,
    )
    np.savetxt(
        results_dir / "noise_spectra.csv",
        np.column_stack([frequency_axis_hz, fit.s_beta, fit.n_s1]),
        delimiter=",",
        header=(
            "frequency_Hz,S_beta_controllable_V2_per_Hz,"
            "N_S1_uncontrollable_V2_per_Hz"
        ),
        comments="",
    )

    files = [
        "psd_matrix.npz",
        "popt_fit.npz",
        "noise_spectra.npz",
        "noise_spectra.csv",
        _plot_control_axis(
            results_dir, main_field_current_ma, calibrated_control_hz
        ),
        _plot_psd_matrix(
            results_dir,
            frequency_axis_hz,
            calibrated_control_hz,
            psd_matrix,
        ),
        _plot_extracted_spectra(
            results_dir, frequency_axis_hz, fit.s_beta, fit.n_s1
        ),
    ]
    fitted_count = int(np.count_nonzero(fit.fit_mask))
    interpolated_count = int(np.count_nonzero(fit.interpolated_mask))
    payload = {
        "success": fitted_count > 0,
        "experiment_id": config.get(
            "experiment_id", "mx-main-field-noise-spectrum"
        ),
        "run_dir": str(run_dir),
        "acquisition_signal": "Demod0 R",
        "control_model": (
            "control_Hz = HF2_reference_Hz - "
            "(K_f_Hz_per_mA * current_mA + f_0mA_Hz)"
        ),
        "main_field_calibration": {
            "source_run": params.main_field_calibration_source_run,
            "slope_hz_per_ma": params.main_field_calibration_hz_per_ma,
            "intercept_hz": params.main_field_calibration_intercept_hz,
            "valid_current_min_ma": params.main_field_calibration_min_ma,
            "valid_current_max_ma": params.main_field_calibration_max_ma,
            "hf2_reference_frequency_hz": params.hf2_reference_frequency_hz,
        },
        "scan": {
            "completed_points": len(records),
            "target_points": params.control_frequency_points,
            "control_start_hz": float(calibrated_control_hz[0]),
            "control_stop_hz": float(calibrated_control_hz[-1]),
            "signed_detuning_start_hz": float(
                calibrated_signed_detuning_hz[0]
            ),
            "signed_detuning_stop_hz": float(
                calibrated_signed_detuning_hz[-1]
            ),
            "main_field_current_start_ma": float(main_field_current_ma[0]),
            "main_field_current_stop_ma": float(main_field_current_ma[-1]),
        },
        "welch": {
            "nperseg": params.welch_nperseg,
            "actual_rate_sa_s": float(actual_rate_sa_s[0]),
            "frequency_start_hz": float(frequency_axis_hz[0]),
            "frequency_stop_hz": float(frequency_axis_hz[-1]),
        },
        "fit": {
            "model": (
                "D + A*(gamma^2+w^2) / "
                "((gamma^2-w^2+(Omega+dw)^2)^2+4*w^2*gamma^2)"
            ),
            "fitted_frequency_points": fitted_count,
            "interpolated_frequency_points": interpolated_count,
            "total_frequency_points": int(frequency_axis_hz.size),
        },
        "files": files,
    }
    payload["files"].extend(["analysis.yaml", "analysis.json"])
    serializable = _builtin(payload)
    with (results_dir / "analysis.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(serializable, stream, allow_unicode=True, sort_keys=False)
    with (results_dir / "analysis.json").open("w", encoding="utf-8") as stream:
        json.dump(serializable, stream, ensure_ascii=False, indent=2)
    print(
        f"Mx 主场噪声谱分析完成: {len(records)} 个主场点，"
        f"{fitted_count}/{frequency_axis_hz.size} 个频率点直接拟合成功"
    )
    return serializable


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
