"""Z 线圈实际电流频率响应离线分析：对数扫频复响应拟合。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ...current_feedback import (
    AW_CURRENT_RESPONSE_PHASE_REFERENCE, sense_voltage_to_current,
)
from ...plotting import format_axis, new_figure, save_figure, set_plot_style
from ..z_coil_inductance_frequency_response.analysis import (
    _as_scalar, _circular_std_deg, _load_capture, falling_edge_time, fit_sine,
)
from ..z_aw_waveform_scope_check.analysis import _saturation_diagnostic


EXPERIMENT_ID = "z-coil-current-frequency-response"
FORMAT_VERSION = 4
OUTPUT_PROTOCOL = "coherent_swept_sine_current_response"
COMMAND_VOLTAGE_REFERENCE = "50_ohm"
# 驱动参考取自同一帧的实测电压时，分母是实测相量；缺少参考通道时退化为设定值。
PHASE_REFERENCE_MEASURED = AW_CURRENT_RESPONSE_PHASE_REFERENCE
PHASE_REFERENCE_COMMANDED = "commanded_amplitude"
# 沿用既有质量基准：无饱和、正弦拟合相关性下限、重复相位标准差上限。
FIT_CORRELATION_MIN = 0.98
PHASE_STD_MAX_DEG = 15.0


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise TypeError(f"YAML 顶层必须是映射: {path}")
    return payload


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


def select_post_settle_cycles(
    time_s: np.ndarray, frequency_hz: float, *,
    discard_cycles: int, retained_cycles: int,
) -> np.ndarray:
    """选择相干启动后丢弃指定周期数的完整周期采样掩码。"""
    period_s = 1.0 / float(frequency_hz)
    dt = float(np.median(np.diff(time_s)))
    start = discard_cycles * period_s
    stop = (discard_cycles + retained_cycles) * period_s
    if float(np.max(time_s)) < stop - 0.5 * dt:
        raise ValueError(
            f"{frequency_hz:.6g} Hz 标定帧的触发后窗口不足："
            f"需要到 {stop:.6g} s，实际到 {float(np.max(time_s)):.6g} s"
        )
    mask = (time_s >= start) & (time_s < stop - 0.5 * dt)
    if int(mask.sum()) < 4:
        raise ValueError(
            f"{frequency_hz:.6g} Hz 标定帧内完整周期采样不足"
            f"（丢弃 {discard_cycles} 周期后仅 {int(mask.sum())} 点）"
        )
    return mask


def _phasor(fit: dict[str, float]) -> complex:
    """把 sin/cos 拟合结果转成 cos 约定的复相量。"""
    return float(fit["amplitude_v"]) * np.exp(1j * np.deg2rad(fit["phase_deg"]))


def _fit_swept_capture(capture: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """从单帧扫频采集恢复复数传递函数 H = I/V。

    驱动参考与电流取自同一帧、同一时间轴：以参考通道实测正弦作分母，
    相干启动时延、DG 内部延迟与通道共模误差同时被消掉，剩下的才是真实
    I/V 传递。缺少参考通道时退化为按命令设定值归一，相位会保留一个随
    频率线性增长的固定时延，只能用于幅度判读。
    """
    frequency = float(_as_scalar(capture, "frequency_hz"))
    drive_amplitude_vpp = float(
        _as_scalar(capture, "drive_amplitude_vpp", config["DRIVE_AMPLITUDE_VPP"]))
    drive_offset_v = float(_as_scalar(capture, "drive_offset_v", 0.0))
    edge_time = falling_edge_time(
        capture["trigger_time_s"], capture["trigger_voltage_v"],
        float(config["SCOPE_TRIGGER_LEVEL_V"]),
    )
    time_relative_s = np.asarray(capture["time_s"], dtype=float) - edge_time
    selected = select_post_settle_cycles(
        time_relative_s, frequency,
        discard_cycles=int(config["RESPONSE_SETTLE_CYCLES"]),
        retained_cycles=int(config["SCOPE_CYCLES"]),
    )
    measured_voltage = np.asarray(capture["measured_voltage_v"], dtype=float)
    current_a = sense_voltage_to_current(
        measured_voltage[selected], float(config["SENSE_RESISTOR_OHM"]))
    current_fit = fit_sine(
        time_relative_s[selected], current_a, frequency,
        drive_amplitude_vpp, drive_offset_v,
    )
    reference_fit: dict[str, float] | None = None
    drive_amplitude_measured_v = float("nan")
    if "reference_voltage_v" in capture:
        reference_voltage = np.asarray(capture["reference_voltage_v"], dtype=float)
        if reference_voltage.size != measured_voltage.size:
            raise ValueError("驱动参考通道点数与 CH3 不一致")
        reference_fit = fit_sine(
            time_relative_s[selected], reference_voltage[selected], frequency,
            drive_amplitude_vpp, drive_offset_v,
        )
        drive_phasor = _phasor(reference_fit)
        drive_amplitude_measured_v = float(reference_fit["amplitude_v"])
        phase_reference = PHASE_REFERENCE_MEASURED
    else:
        drive_phasor = 0.5 * drive_amplitude_vpp + 0.0j
        phase_reference = PHASE_REFERENCE_COMMANDED
    if abs(drive_phasor) <= 0.0:
        raise ValueError(f"{frequency:.6g} Hz 的驱动参考幅度为零，无法归一化")
    transfer = _phasor(current_fit) / drive_phasor
    saturation = _saturation_diagnostic(
        measured_voltage,
        scale_v_div=_as_scalar(capture, "scale_used_v_div", np.nan),
        offset_v=_as_scalar(capture, "offset_used_v", 0.0),
    )
    return {
        "frequency_hz": frequency,
        "transfer": complex(transfer),
        "phase_reference": phase_reference,
        "drive_amplitude_measured_v": drive_amplitude_measured_v,
        "drive_fit_correlation": (
            float(reference_fit["correlation"]) if reference_fit is not None
            else float("nan")),
        "current_amplitude_a": float(current_fit["amplitude_v"]),
        "current_offset_a": float(current_fit["offset_v"]),
        "fit_correlation": float(current_fit["correlation"]),
        "fit_residual_rms_a": float(current_fit["residual_rms_v"]),
        "sample_count": int(selected.sum()),
        **saturation,
    }


def _aggregate_points(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一频率的重复帧汇总为一点，并给出相位重复性判据。"""
    frequency_axis = np.asarray(
        sorted({r["frequency_hz"] for r in records}), dtype=float)
    points: list[dict[str, Any]] = []
    for frequency in frequency_axis:
        frames = [r for r in records if np.isclose(r["frequency_hz"], frequency)]
        transfers = np.asarray([r["transfer"] for r in frames], dtype=complex)
        phases_deg = np.rad2deg(np.angle(transfers))
        saturation = any(bool(r["saturation_detected"]) for r in frames)
        correlation = float(np.mean([r["fit_correlation"] for r in frames]))
        phase_std = float(_circular_std_deg(phases_deg))
        mean_transfer = complex(np.mean(transfers))
        reliable = bool(
            not saturation
            and np.all(np.isfinite(transfers))
            and np.all(np.abs(transfers) > 0.0)
            and np.isfinite(correlation)
            and correlation > FIT_CORRELATION_MIN
            and phase_std < PHASE_STD_MAX_DEG
        )
        points.append({
            "frequency_hz": float(frequency),
            "transfer_real_a_per_v": mean_transfer.real,
            "transfer_imag_a_per_v": mean_transfer.imag,
            "transfer_magnitude_a_per_v": abs(mean_transfer),
            "transfer_phase_deg": float(np.rad2deg(np.angle(mean_transfer))),
            "reliable": reliable,
            "frame_count": len(frames),
            "saturation_detected": saturation,
            "fit_correlation_mean": correlation,
            "phase_std_deg": phase_std,
            "current_amplitude_a_mean": float(
                np.mean([r["current_amplitude_a"] for r in frames])),
            "drive_amplitude_measured_v_mean": float(
                np.mean([r["drive_amplitude_measured_v"] for r in frames])),
        })
    return points


def _plot_transfer(results_dir: Path, frequency_hz: np.ndarray, transfer: np.ndarray,
                   reliable: np.ndarray) -> str:
    set_plot_style("paper")
    fig, axes = new_figure(nrows=2, ncols=1, kind="wide", constrained_layout=True)
    axes[0].plot(frequency_hz[reliable], np.abs(transfer[reliable]), "o-", ms=3, lw=1.0,
                 label="Reliable point")
    axes[0].plot(frequency_hz[~reliable], np.abs(transfer[~reliable]), "x", ms=5,
                 label="Excluded point")
    axes[0].set_xscale("log")
    format_axis(axes[0], xlabel="Drive frequency (Hz)", ylabel="|I/V| (A/V)")
    axes[0].grid(True, alpha=0.25, which="both")
    axes[0].legend(loc="best")
    axes[1].plot(frequency_hz[reliable], np.rad2deg(np.angle(transfer[reliable])), "o-",
                 ms=3, lw=1.0, label="Reliable point")
    axes[1].plot(frequency_hz[~reliable], np.rad2deg(np.angle(transfer[~reliable])), "x",
                 ms=5, label="Excluded point")
    axes[1].set_xscale("log")
    format_axis(axes[1], xlabel="Drive frequency (Hz)", ylabel="Phase (deg)")
    axes[1].grid(True, alpha=0.25, which="both")
    axes[1].legend(loc="best")
    filename = "transfer_function.png"
    save_figure(fig, results_dir / filename)
    return filename


def _analyze_swept_captures(run_dir: Path, config: dict[str, Any],
                            records: list[dict[str, Any]]) -> dict[str, Any]:
    """对数扫频分析：逐频率汇总重复帧，保留可靠性掩码与可靠覆盖区间。"""
    phase_references = {str(r["phase_reference"]) for r in records}
    if len(phase_references) != 1:
        raise ValueError("同一运行内混用了不同的相位参考方式")
    phase_reference = phase_references.pop()
    point_results = _aggregate_points(records)
    frequency_hz = np.asarray([p["frequency_hz"] for p in point_results], dtype=float)
    transfer = np.asarray(
        [p["transfer_real_a_per_v"] + 1j * p["transfer_imag_a_per_v"]
         for p in point_results], dtype=complex)
    reliable = np.asarray([p["reliable"] for p in point_results], dtype=bool)
    if not np.any(reliable):
        raise ValueError("扫频没有产生任何可靠频点，请检查驱动幅度、接线与重复次数")
    reliable_frequency = frequency_hz[reliable]
    results_dir = run_dir / "results"
    np.savez(
        results_dir / "frequency_response.npz",
        format_version=np.int64(FORMAT_VERSION),
        output_protocol=np.asarray(OUTPUT_PROTOCOL),
        command_voltage_reference=np.asarray(COMMAND_VOLTAGE_REFERENCE),
        phase_reference=np.asarray(phase_reference),
        frequency_hz=frequency_hz,
        transfer_real_a_per_v=transfer.real,
        transfer_imag_a_per_v=transfer.imag,
        reliable=reliable,
        frame_count=np.asarray([p["frame_count"] for p in point_results], dtype=int),
        fit_correlation_mean=np.asarray(
            [p["fit_correlation_mean"] for p in point_results], dtype=float),
        phase_std_deg=np.asarray(
            [p["phase_std_deg"] for p in point_results], dtype=float),
        saturation_detected=np.asarray(
            [p["saturation_detected"] for p in point_results], dtype=np.uint8),
        sense_resistor_ohm=np.float64(config["SENSE_RESISTOR_OHM"]),
        reliable_frequency_min_hz=np.float64(float(np.min(reliable_frequency))),
        reliable_frequency_max_hz=np.float64(float(np.max(reliable_frequency))),
    )
    plot_transfer = _plot_transfer(results_dir, frequency_hz, transfer, reliable)
    excluded = frequency_hz[~reliable]
    return {
        "experiment_id": EXPERIMENT_ID,
        "run_dir": str(run_dir),
        "analysis_format_version": FORMAT_VERSION,
        "capture_count": len(records),
        "frequency_count": int(frequency_hz.size),
        "output_protocol": OUTPUT_PROTOCOL,
        "command_voltage_reference": COMMAND_VOLTAGE_REFERENCE,
        "phase_reference": phase_reference,
        "sense_resistor_ohm": float(config["SENSE_RESISTOR_OHM"]),
        "reliable_point_count": int(np.count_nonzero(reliable)),
        "reliable_band_hz": {
            "minimum": float(np.min(reliable_frequency)),
            "maximum": float(np.max(reliable_frequency)),
        },
        "excluded_frequency_hz": [float(value) for value in excluded],
        "points": point_results,
        "quality_limits": {
            "fit_correlation_min": FIT_CORRELATION_MIN,
            "phase_std_max_deg": PHASE_STD_MAX_DEG,
        },
        "files": [plot_transfer, "frequency_response.npz"],
    }


def analyze(run_dir: Path) -> dict[str, Any]:
    """分析对数扫频电流频响运行目录。"""
    run_dir = Path(run_dir).resolve()
    config = _load_yaml(run_dir / "experiment_config.yaml")
    parameters = config.get("parameters", {})
    analysis_config = {
        "SENSE_RESISTOR_OHM": float(parameters.get("SENSE_RESISTOR_OHM", 0.0)),
        "SCOPE_TRIGGER_LEVEL_V": float(parameters.get("SCOPE_TRIGGER_LEVEL_V", 2.5)),
        "RESPONSE_SETTLE_CYCLES": int(parameters.get("RESPONSE_SETTLE_CYCLES", 0)),
        "SCOPE_CYCLES": int(parameters.get("SCOPE_CYCLES", 3)),
        "DRIVE_AMPLITUDE_VPP": float(parameters.get("DRIVE_AMPLITUDE_VPP", 1.0)),
    }
    if analysis_config["SENSE_RESISTOR_OHM"] <= 0.0:
        raise ValueError("分析配置缺少正的 SENSE_RESISTOR_OHM")
    capture_paths = sorted((run_dir / "raw").glob("scope_f*_r*.npz"))
    if not capture_paths:
        raise FileNotFoundError(f"未找到 SDS 扫频采集文件: {run_dir / 'raw'}")
    records = []
    for path in capture_paths:
        capture = _load_capture(path)
        if "frequency_hz" not in capture:
            raise ValueError(f"{path.name} 缺少 frequency_hz，不是扫频采集帧")
        record = _fit_swept_capture(capture, analysis_config)
        record["file"] = str(path.relative_to(run_dir))
        records.append(record)
    (run_dir / "results").mkdir(exist_ok=True)
    report = _analyze_swept_captures(run_dir, analysis_config, records)
    (run_dir / "results" / "frequency_response.yaml").write_text(
        yaml.safe_dump(_builtin(report), allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    (run_dir / "results" / "frequency_response.json").write_text(
        json.dumps(_builtin(report), ensure_ascii=False, indent=2),
        encoding="utf-8")
    return _builtin(report)


def main() -> int:
    from ...experiment_runtime import runtime_run_dir
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
