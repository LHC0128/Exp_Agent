"""离线输入资格、原 Welch/ASD 算法与加权线性拟合；不连接仪器。"""
from dataclasses import fields
from pathlib import Path
import hashlib
import json
import math
import warnings
import numpy as np
import yaml
from scipy import signal
from scipy.optimize import curve_fit
from ...experiment_runtime import check_cancelled
from lab_workflows.workflow_state import write_state, read_state
from .storage import save_npz
from .stages import ANALYSIS_KEYS
from . import run_format


class AnalysisQualityError(ValueError):
    """输入或拟合不合格；保留可定位的诊断。"""


def output_path(run_dir, relative):
    path = Path(run_dir) / relative
    if not path.resolve().is_relative_to(Path(run_dir).resolve()) or path.resolve() != path.absolute():
        raise AnalysisQualityError(f"路径越出运行目录或经过目录联接：{path}")
    return path


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def load_config(run_dir):
    path = output_path(run_dir, "experiment_config.yaml")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise AnalysisQualityError(reason)
    return config, run_format.parameters(config)


def resolve_options(params, values):
    if unknown := values.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
    resolved = type(params).from_external(params.to_external() | values)
    for field in fields(resolved):
        if field.metadata["external_name"] not in ANALYSIS_KEYS:
            continue
        value, metadata = getattr(resolved, field.name), field.metadata
        if ((metadata["minimum"] is not None and value < metadata["minimum"])
                or (metadata["maximum"] is not None and value > metadata["maximum"])):
            raise ValueError(f'{metadata["external_name"]} 超出范围')
    return resolved


def fingerprint(run_dir):
    config, _ = load_config(run_dir)
    config = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    raw = output_path(run_dir, "raw")
    return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        raw_files={p.relative_to(run_dir).as_posix(): signature(output_path(run_dir, p.relative_to(run_dir)))
                   for p in sorted(raw.glob("*.npz")) if p.is_file()}, analysis_version="photon-shot-noise-v1")


def load_inputs(run_dir, params, *, compute=False):
    """归档前检查全部输入；只缓存谱统计量，不把整个扫描波形载入内存。"""
    with np.load(output_path(run_dir, "raw/scan_meta.npz"), allow_pickle=False) as meta:
        powers = np.asarray(meta["probe_powers"], dtype=float)
        reference_rate = float(meta["actual_rate"])
        duration, repeats = float(meta["acq_duration"]), int(meta["acq_repeats"])
    if (powers.shape != (params.probe_power_points,) or not np.all(np.isfinite(powers))
            or not np.allclose(powers, np.linspace(params.probe_power_start, params.probe_power_stop, params.probe_power_points))
            or np.any(np.diff(powers) <= 0) or repeats != params.acq_repeats
            or not math.isfinite(reference_rate) or reference_rate <= 0
            or not np.isclose(duration, params.acq_duration) or repeats < 1):
        raise AnalysisQualityError("扫描摘要与历史参数、Probe 轴或实际采样率不一致")
    flat_limit = reference_rate * params.flat_freq_fraction
    all_asd_x, all_asd_y, all_psd_x, all_psd_y, all_rms, valid = [], [], [], [], [], []
    freqs_saved = None
    for index, power in enumerate(powers):
        check_cancelled()
        with np.load(output_path(run_dir, f"raw/waveforms_P{index:03d}.npz"), allow_pickle=False) as wave:
            xw, yw = np.asarray(wave["x_waveforms"], float), np.asarray(wave["y_waveforms"], float)
            rates = np.asarray(wave["actual_rates_Sa_s"], float) if "actual_rates_Sa_s" in wave else np.full(
                xw.shape[0], float(wave["actual_rate"]) if "actual_rate" in wave else reference_rate)
            if (xw.ndim != 2 or yw.shape != xw.shape or xw.shape[0] != repeats or xw.shape[1] < 8
                    or rates.shape != (repeats,) or not np.all(np.isfinite(rates)) or np.any(rates <= 0)
                    or not np.isclose(float(wave["probe_power"]), power)
                    or int(wave["probe_power_index"]) != index
                    or np.any(np.abs(xw.shape[1] / rates - duration) > 2 / rates)):
                raise AnalysisQualityError(f"Probe {index} 波形形状、索引、时长或实际率不一致")
            if "time_s" in wave:
                axis = np.asarray(wave["time_s"], float)
                if (axis.shape != xw.shape or not np.all(np.isfinite(axis)) or np.any(np.diff(axis, axis=1) <= 0)
                        or not np.allclose(np.diff(axis, axis=1), 1 / rates[:, None], rtol=1e-4, atol=1e-12)):
                    raise AnalysisQualityError(f"Probe {index} 时间轴与实际率不一致")
        nperseg = min(params.psd_nperseg, xw.shape[1] // 2)
        if nperseg < 4:
            raise AnalysisQualityError("Welch 分段点数不足")
        ax, ay, px, py, rms, mask = [], [], [], [], [], []
        for repeat, (x, y, rate) in enumerate(zip(xw, yw, rates)):
            check_cancelled()
            freqs = np.fft.rfftfreq(nperseg, d=1 / rate)
            if freqs_saved is None:
                freqs_saved = freqs
            elif freqs_saved.shape != freqs.shape or not np.allclose(freqs_saved, freqs):
                raise AnalysisQualityError("记录实际采样率或长度不一致，不能合并 PSD")
            band = (freqs > 0) & (freqs <= flat_limit)
            if not np.any(band):
                raise AnalysisQualityError("平坦频段不含有效 Welch 频率点")
            good = bool(np.all(np.isfinite(x)) and np.all(np.isfinite(y)))
            mask.append(good)
            if not compute:
                continue
            rms.append(float(np.std(x)))
            if not good:
                ax.append(np.nan); ay.append(np.nan)
                continue
            frequencies, psd_x = signal.welch(x, fs=rate, nperseg=nperseg, scaling="density", detrend="constant")
            _, psd_y = signal.welch(y, fs=rate, nperseg=nperseg, scaling="density", detrend="constant")
            ax.append(np.median(np.sqrt(np.maximum(psd_x[band], 0))))
            ay.append(np.median(np.sqrt(np.maximum(psd_y[band], 0))))
            px.append(psd_x); py.append(psd_y)
        valid.append(mask)
        if compute:
            all_asd_x.append(ax); all_asd_y.append(ay); all_rms.append(rms)
            all_psd_x.append(np.mean(px, axis=0) if px else np.full_like(freqs_saved, np.nan))
            all_psd_y.append(np.mean(py, axis=0) if py else np.full_like(freqs_saved, np.nan))
    if not compute:
        return dict(completed_points=len(powers), valid_records=int(np.count_nonzero(valid)))
    data = dict(probe_powers=powers, actual_rate=np.asarray(reference_rate), flat_freq_limit=np.asarray(flat_limit),
        frequencies=freqs_saved, all_asd_x=np.asarray(all_asd_x), all_asd_y=np.asarray(all_asd_y),
        psd_xm=np.asarray(all_psd_x), psd_ym=np.asarray(all_psd_y), all_rms=np.asarray(all_rms),
        valid_records=np.asarray(valid))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for channel in ("x", "y"):
            values = data["all_asd_" + channel]
            data["mean_asd_" + channel] = np.nanmean(values, axis=1)
            data["std_asd_" + channel] = np.nanstd(values, axis=1)
        data["mean_rms_x"] = np.nanmean(data["all_rms"], axis=1)
        data["std_rms_x"] = np.nanstd(data["all_rms"], axis=1)
    return data


def write_statistics(run_dir, data):
    save_npz(output_path(run_dir, "results/noise_statistics.npz"), **data)
    return dict(completed_points=len(data["probe_powers"]),
        valid_records=int(np.count_nonzero(data["valid_records"])))


def statistics(run_dir):
    with np.load(output_path(run_dir, "results/noise_statistics.npz"), allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def linear_model(power, alpha, beta):
    return alpha * power + beta


def fit(run_dir, params):
    data = statistics(run_dir)
    powers = data["probe_powers"]
    report = dict(method="Welch PSD->ASD flat median->ASD^2 fit", fit={},
        params=dict(nperseg=params.psd_nperseg, flat_freq=float(data["flat_freq_limit"]), fit_start=params.fit_start_idx))
    errors, diagnostics = [], {}
    for channel in ("x", "y"):
        mean, sd = data["mean_asd_" + channel], data["std_asd_" + channel]
        sigma = 2 * mean * sd
        valid = (np.arange(len(powers)) >= params.fit_start_idx) & np.isfinite(mean) & np.isfinite(sigma) & (sigma > 0)
        diagnostics[channel] = dict(valid_points=int(valid.sum()), excluded_indices=np.flatnonzero(~valid).tolist())
        try:
            if valid.sum() < 3 or len(np.unique(powers[valid])) < 3:
                raise AnalysisQualityError(f"{channel.upper()} 拟合至少需要三个有限、权重为正的不同 Probe 点")
            popt, covariance = curve_fit(linear_model, powers[valid], mean[valid] ** 2,
                                        sigma=sigma[valid], absolute_sigma=True)
            uncertainty = np.sqrt(np.diag(covariance))
            if not np.all(np.isfinite(popt)) or not np.all(np.isfinite(uncertainty)):
                raise AnalysisQualityError(f"{channel.upper()} 拟合参数或不确定度非有限")
            alpha, beta = popt
            report["fit"].update({f"alpha_{channel}": float(alpha), f"alpha_{channel}_err": float(uncertainty[0]),
                f"beta_{channel}": float(beta), f"beta_{channel}_err": float(uncertainty[1])})
        except (ValueError, RuntimeError) as exc:
            errors.append(str(exc))
    if not errors:
        ax, bx = report["fit"]["alpha_x"], report["fit"]["beta_x"]
        report["fit"]["beta_x_ASD"] = math.sqrt(max(bx, 0))
        ratio = ax * powers[-1] / bx if bx > 0 else None
        report["fit"]["snr_db"] = float(10 * np.log10(ratio)) if ratio is not None and ratio > 0 else None
    payload = dict(status="quality_failed" if errors else "completed", errors=errors,
                   diagnostics=diagnostics, report=report)
    write_state(output_path(run_dir, "results/fit_summary.json"), payload)
    if errors:
        raise AnalysisQualityError("；".join(errors))
    return dict(fit=report["fit"], diagnostics=diagnostics)


def write_report(run_dir):
    fit_result = read_state(output_path(run_dir, "results/fit_summary.json"))
    if fit_result.get("status") != "completed":
        raise AnalysisQualityError("拟合质量未通过")
    output_path(run_dir, "results/analysis.yaml").write_text(
        yaml.safe_dump(fit_result["report"], allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dict(fit=fit_result["report"]["fit"])
