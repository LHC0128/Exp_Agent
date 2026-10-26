"""只读原始数据，按 DirectAW 色散中心斜率及固定目标 RF 口径分析各倍率。"""
from pathlib import Path
import csv
import json
import math
import numpy as np
import yaml
from scipy.signal import welch
from sensitivity_analysis.fitting import LARMOR_PER_NT
from ...experiment_runtime import check_cancelled
from lab_workflows.workflow_state import write_state
from ..rf_sensitivity_direct_aw.analysis_steps import fit_bandwidth, fit_response_curve, dispersive, _plain
from . import run_format
from .models import RFControlScaleParams
from ...analysis.noise_quality import inspect_noise_spectra, select_noise_records


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_inputs(directory, config, *, acquired_only=False):
    # 续测只读取完整点；正式分析仍严格要求整轮封存。
    block_reason = run_format.format_block_reason if acquired_only else run_format.analysis_block_reason
    if reason := block_reason(config):
        raise ValueError(reason)
    directory = Path(directory).resolve()
    manifest = read_json(directory / "raw/point_manifest.json")
    if manifest.get("raw_format_version") != config["raw_format_version"]:
        raise ValueError("清单与运行原始格式版本不一致")
    if config["raw_format_version"] == 2 and manifest.get("phase_calibration_scope") != run_format.PHASE_CALIBRATION_SCOPE:
        raise ValueError("v2 清单缺少逐点校相标记")
    parameters = config["parameters"]
    expected = {(Path(relative).parent.parent.name, float(scale))
                for relative in parameters["CONTROL_WAVEFORM_FILES"] for scale in parameters["CONTROL_SCALES"]}
    seen, points = set(), []
    for record in manifest["points"]:
        check_cancelled()
        if acquired_only and record["status"] not in ("acquired", "completed"):
            continue
        path = (directory / record["path"]).resolve()
        if directory not in path.parents or not path.is_dir():
            raise ValueError("点目录不存在或越出运行目录")
        key = (record["control"], float(record["scale"]))
        if key not in expected or key in seen or record["status"] not in (("acquired", "completed") if acquired_only else ("completed",)):
            raise ValueError("控制 × Scale 清单重复、未知或未完整封存")
        child = yaml.safe_load((path / "experiment_config.yaml").read_text(encoding="utf-8"))
        if reason := block_reason(child):
            raise ValueError(f"{key}：{reason}")
        if acquired_only and child.get("completion_status") not in ("acquired", "completed"):
            raise ValueError(f"{key}：清单声明完整但子运行未采集完成")
        if child.get("raw_format_version") != config["raw_format_version"] or child.get("control_scale") != key[1]:
            raise ValueError("子运行倍率/格式不一致，不能混用固定相位与逐点校相")
        if reason := run_format.point_calibration_block_reason(child):
            raise ValueError(reason)
        operational = {"RESUME_RUN_ID", "TEC_EXTERNAL_CONTROL", "RUN_TAG"}
        expected_parameters = parameters | {"ARB_WAVEFORM_FILE": child["parameters"]["ARB_WAVEFORM_FILE"],
                "CONTROL_WAVEFORM_FILES": [child["parameters"]["ARB_WAVEFORM_FILE"]]}
        child_parameters = RFControlScaleParams.from_external(child["parameters"]).acquisition_parameters()
        expected_parameters = RFControlScaleParams.from_external(expected_parameters).acquisition_parameters()
        if {k: v for k, v in child_parameters.items() if k not in operational} != {k: v for k, v in expected_parameters.items() if k not in operational}:
            raise ValueError("子运行参数与父运行快照不一致")
        if Path(child["parameters"]["ARB_WAVEFORM_FILE"]).parent.parent.name != key[0]:
            raise ValueError("控制身份与原始清单不一致")
        factor = child_parameters["CONTROL_SCALE_FACTORS"][key[0]]
        total = key[1] * factor
        for snapshot in (record, child):
            for name, expected_value in (("control_scale_factor", factor), ("effective_control_scale", total)):
                if snapshot.get(name, expected_value if child.get("schema_version", 1) < 2 else None) != expected_value:
                    raise ValueError("点清单、快照与控制补偿倍率不一致")
        manual_phase = run_format.manual_rf_phase(child) if config["raw_format_version"] == 2 else None
        required = ("response_data.npz", "bandwidth_data.npz", "xy_aw_waveforms.npz")
        if config["raw_format_version"] == 2:
            required += ("xy_aw_waveforms_initial.npz",)
            if manual_phase is None:
                required += ("z_phase_scan.npz",)
        for name in required:
            target = path / "raw" / name
            if not target.is_file() or directory not in target.resolve().parents:
                raise ValueError(f"缺少安全的原始文件：{name}")
        if config["raw_format_version"] == 2:
            selected = manual_phase
            if selected is None:
                with np.load(path / "raw/z_phase_scan.npz", allow_pickle=False) as phase_scan:
                    selected = float(phase_scan["selected_phase_deg"])
            with np.load(path / "raw/response_data.npz", allow_pickle=False) as response:
                xy_phase = float(response["xy_ctrl_phase_deg"])
                expected_phases = (selected + np.where(response["amplitudes_V"] < 0, 180, 0)) % 360
                response_phases_match = np.allclose(response["z_burst_phase_deg"], expected_phases, rtol=0, atol=1e-8)
            if (not np.isclose(selected, child["rf_phase_calibration"]["selected_phase_deg"])
                    or not np.isclose(xy_phase, child["xy_phase_calibration"]["final_phase_deg"])
                    or not response_phases_match):
                raise ValueError("当前点校相结果与实际响应采集相位不一致")
        files = sorted((path / "raw").glob("noise_D3_*.npz"))
        if len(files) != (parameters["NOISE_N_AVG"] if parameters["INCLUDE_NOISE"] else 0):
            raise ValueError("各倍率的噪声记录数与采集快照不一致")
        points.append((record, path, child))
        seen.add(key)
    if seen != expected and not acquired_only:
        raise ValueError("缺少控制 × Scale 数据，不把部分采集当作完整结果")
    return points


def fit_dispersive_slope(field, response):
    """复用 DirectAW 四参数色散拟合；用完整协方差传播中心斜率误差。"""
    field, response = np.asarray(field, dtype=float), np.asarray(response, dtype=float)
    if (field.ndim != 1 or response.shape != field.shape or len(field) < 5
            or not np.isfinite(field).all() or not np.isfinite(response).all()
            or np.any(np.diff(field) <= 0) or not field.min() < 0 < field.max()
            or np.ptp(response) <= 0):
        raise ValueError("色散拟合需要至少五个有限、有序且跨越零 RF 的非恒定响应点")
    fit = fit_response_curve(field, response, component="X")
    if not fit["success"] or fit["method"] != "dispersive_curve_fit":
        raise ValueError("色散拟合失败，不使用数值梯度回退：" + str(fit["failure_reason"]))
    parameters, covariance = np.asarray(fit["popt"]), np.asarray(fit["pcov"])
    if (parameters.shape != (4,) or covariance.shape != (4, 4)
            or not np.isfinite(parameters).all() or not np.isfinite(covariance).all()
            or np.any(np.diag(covariance) < 0) or parameters[1] == 0):
        raise ValueError("色散拟合参数或协方差无效")
    amplitude, gamma, center, offset = parameters
    slope = float(fit["slope_V_per_nT"])
    gradient = np.array([np.sign(amplitude) / gamma**2, -2 * abs(amplitude) / gamma**3, 0., 0.])
    slope_variance = float(gradient @ covariance @ gradient)
    residual = response - dispersive(field, *parameters)
    hwhm = abs(float(gamma))
    hwhm_error = float(np.sqrt(covariance[1, 1]))
    row = dict(slope_V_per_nT=slope,
        slope_se_V_per_nT=float(np.sqrt(max(0., slope_variance))),
        signed_slope_V_per_nT=float(amplitude / gamma**2),
        zero_field_slope_V_per_nT=float(amplitude * (gamma**2 - center**2) / (center**2 + gamma**2)**2),
        fit_method=fit["method"], response_fit_r_squared=float(fit["r_squared"]),
        dispersive_amplitude_V_nT=float(amplitude), dispersive_center_nT=float(center),
        dispersive_offset_V=float(offset), dispersive_hwhm_nT=hwhm,
        dispersive_hwhm_Hz=hwhm * LARMOR_PER_NT,
        dispersive_hwhm_se_nT=hwhm_error, dispersive_hwhm_se_Hz=hwhm_error * LARMOR_PER_NT,
        fit_min_nT=float(field.min()), fit_max_nT=float(field.max()), n_fit=len(field),
        residual_rms_V=float(np.sqrt(np.mean(residual**2))))
    return row, dict(parameters=parameters, covariance=covariance, residual=residual,
        fitted=dispersive(field, *parameters), fit_field_nT=fit["fit_x_nT"], fit_x_V=fit["fit_y_V"])


def response_fit(directory, config, *, component="X"):
    rows, arrays = [], {}
    for index, (record, path, child) in enumerate(load_inputs(directory, config)):
        key = f"p{index:04d}"
        factor = child["parameters"].get("CONTROL_SCALE_FACTORS", {}).get(record["control"], 1.0)
        row = dict(record, key=key, control_scale_factor=factor,
            effective_control_scale=record["scale"] * factor,
            valid=False, dispersive_fit_valid=False, lorentzian_fit_valid=False,
            rf_phase_mode="nominal_control" if child["raw_format_version"] == 1 else "scan" if child["parameters"].get("Z_PHASE_SCAN_ENABLED", True) else "manual",
            rf_phase_deg=child.get("rf_phase_calibration", {}).get("selected_phase_deg"))
        rejections = []
        with np.load(path / "raw/response_data.npz", allow_pickle=False) as data:
            field, values = np.asarray(data["amplitudes_nT"]), np.asarray(data["demod3_x_V"])
            if not np.allclose(field, data["amplitudes_V"] * child["parameters"]["Z_V_TO_NT"]):
                raise ValueError("RF 场标定与原始幅度不一致")
            if float(data["z_rf_freq_Hz"]) != child["parameters"]["Z_RF_FREQ"]:
                raise ValueError("响应采集频率不是固定目标 RF")
            arrays.update({key + "_field_nT": field, key + "_x_V": values,
                           key + "_y_V": data["demod3_y_V"], key + "_r_V": data["demod3_r_V"]})
        try:
            fitted, source = fit_dispersive_slope(field, values)
            row.update(fitted, dispersive_fit_valid=True)
            arrays.update({key + "_" + name: value for name, value in source.items()})
        except ValueError as exc:
            rejections.append(str(exc))
        try:
            bandwidth = fit_bandwidth(path, child)
            row.update(center_Hz=bandwidth["center_Hz"], hwhm_Hz=bandwidth["hwhm_Hz"],
                       lorentzian_hwhm_Hz=bandwidth["hwhm_Hz"],
                       frequency_shift_Hz=bandwidth["center_Hz"] - child["parameters"]["Z_RF_FREQ"],
                       fractional_frequency_shift=(bandwidth["center_Hz"] - child["parameters"]["Z_RF_FREQ"]) / child["parameters"]["Z_RF_FREQ"],
                       peak_V=bandwidth["amplitude_V"], frequency_fit_r_squared=bandwidth["r_squared"], lorentzian_fit_valid=True)
            arrays.update({key + "_frequency_fit_Hz": bandwidth["fit_freq_Hz"], key + "_frequency_fit_r_V": bandwidth["fit_r_V"]})
        except ValueError as exc:
            rejections.append(str(exc))
        row["valid"] = row["dispersive_fit_valid"] and row["lorentzian_fit_valid"]
        if rejections:
            row["rejection_reason"] = "；".join(rejections)
        with np.load(path / "raw/bandwidth_data.npz", allow_pickle=False) as data:
            arrays.update({key + "_frequency_Hz": data["frequency_Hz"], key + "_frequency_r_V": data["demod3_r_V"]})
        rows.append(row)
    write_state(Path(directory) / "results/response_fits.json", _plain({"points": rows}))
    np.savez(Path(directory) / "results/fit_source_data.npz", **arrays)
    return dict(points=len(rows), accepted=sum(row["valid"] for row in rows),
                slope_definition="abs(A)/gamma**2 at the fitted dispersive center; same as RF DirectAW",
                linewidth_definition="abs(gamma) in nT; equivalent Hz uses the RF DirectAW LARMOR_PER_NT")


def load_noise_spectra(directory, parameters, nperseg):
    """主分析与独立诊断共用真实采样率、Welch 和原始记录校验。"""
    spectra, rates, files, frequency = [], [], [], None
    for file in sorted((Path(directory) / "raw").glob("noise_D3_*.npz")):
        check_cancelled()
        with np.load(file, allow_pickle=False) as data:
            values = np.asarray(data["noise_x_V"], dtype=float)
            rate = float(data["actual_rate_Sa_s"])
            if (values.ndim != 1 or values.size < 2 or not np.isfinite(values).all()
                    or not math.isfinite(rate) or rate <= 0):
                raise ValueError("噪声时序或实际采样率无效")
            if int(data["demod_idx"]) != parameters["DEMOD3_IDX"]:
                raise ValueError("噪声解调器与采集快照不一致")
            f, psd = welch(values, fs=rate, nperseg=min(nperseg, len(values)),
                           window="hann", detrend="constant", scaling="density")
        if frequency is not None and (f.shape != frequency.shape or not np.allclose(f, frequency)):
            raise ValueError("同一倍率的噪声频率轴不一致，不能直接平均")
        frequency = f
        spectra.append(psd)
        rates.append(rate)
        files.append(file.name)
    if not spectra:
        raise ValueError("缺少噪声记录")
    return frequency, np.asarray(spectra), rates, files


def noise_psd(directory, config, nperseg_config):
    index, arrays, quality_rows = [], {}, []
    low, high = (config["analysis_options"][name] for name in ("band_min_hz", "band_max_hz"))
    policy = config["analysis_options"]["noise_outlier_policy"]
    fits = {row["key"]: row for row in read_json(Path(directory) / "results/response_fits.json")["points"]}
    for i, (record, path, child) in enumerate(load_inputs(directory, config)):
        check_cancelled()
        if not child["parameters"]["INCLUDE_NOISE"]:
            continue
        frequency, matrix, rates, files = load_noise_spectra(path, child["parameters"], nperseg_config)
        key = f"p{i:04d}"
        band = (frequency >= low) & (frequency <= high)
        keep, scores, unfiltered, filtered, quality = inspect_noise_spectra(frequency, matrix, low, high, policy)
        slope = fits[key].get("slope_V_per_nT")
        scale_factor = 1e6 / slope if slope else None
        rejected = [name for name, accepted in zip(files, keep) if not accepted]
        for number, (name, score, accepted) in enumerate(zip(files, scores, keep)):
            quality_rows.append(dict(control=record["control"], scale=record["scale"],
                record_index=number, record_number=number + 1, filename=name, accepted=bool(accepted),
                band_median_asd_V_per_sqrtHz=float(score),
                band_median_sensitivity_fT_per_sqrtHz=float(score * scale_factor) if scale_factor else None,
                threshold_asd_V_per_sqrtHz=quality["threshold_asd_V_per_sqrtHz"],
                reason="" if accepted else "显著高值异常：同时超过组内中位数3倍与6个MAD稳健标准差"))
        arrays.update({key + "_frequency_Hz": frequency, key + "_psd_V2_per_Hz": filtered,
                       key + "_unfiltered_psd_V2_per_Hz": unfiltered,
                       key + "_psd_records_V2_per_Hz": matrix, key + "_included_record_mask": keep,
                       key + "_record_band_median_asd_V_per_sqrtHz": scores})
        index.append(dict(key=key, control=record["control"], scale=record["scale"], actual_rates_Sa_s=rates,
            n_records=len(matrix), n_accepted=int(keep.sum()), n_rejected=len(rejected), rejected_records=rejected,
            before_sensitivity_fT_per_sqrtHz=float(np.median(np.sqrt(unfiltered[band])) * scale_factor) if scale_factor else None,
            after_sensitivity_fT_per_sqrtHz=float(np.median(np.sqrt(filtered[band])) * scale_factor) if scale_factor else None,
            **quality))
    np.savez(Path(directory) / "results/psd_data.npz", **arrays)
    quality_summary = {"points": index, "enabled": config["parameters"]["INCLUDE_NOISE"],
        "comparison_band_Hz": [low, high], "policy": policy,
        "total_records": sum(row["n_records"] for row in index),
        "rejected_records": sum(row["n_rejected"] for row in index),
        "affected_groups": sum(row["n_rejected"] > 0 for row in index)}
    write_state(Path(directory) / "results/psd_index.json", quality_summary)
    write_state(Path(directory) / "results/noise_rejection_summary.json", quality_summary)
    fields = ["control", "scale", "record_index", "record_number", "filename", "accepted",
              "band_median_asd_V_per_sqrtHz", "band_median_sensitivity_fT_per_sqrtHz",
              "threshold_asd_V_per_sqrtHz", "reason"]
    with (Path(directory) / "results/noise_record_quality.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(quality_rows)
    return dict(enabled=config["parameters"]["INCLUDE_NOISE"], points=len(index),
                total_records=quality_summary["total_records"], rejected_records=quality_summary["rejected_records"])


def sensitivity_and_plots(directory, config, nperseg_config, *, identity=run_format):
    from .plotting import FULL_ANALYSIS_PANELS, plot_results
    directory = Path(directory)
    rows = read_json(directory / "results/response_fits.json")["points"]
    low, high = (config["analysis_options"][name] for name in ("band_min_hz", "band_max_hz"))
    valid = [row for row in rows if row["valid"]]
    # HWHM 不是硬截止，不能让一个点屏蔽整组；各点只检查设定频带的实际 PSD 覆盖。
    band_valid = config["parameters"]["INCLUDE_NOISE"] and bool(valid)
    arrays = {}
    quality_summary = read_json(directory / "results/psd_index.json")
    noise_quality = {record["key"]: record for record in quality_summary["points"]}
    for row in rows:
        if quality := noise_quality.get(row["key"]):
            row.update(noise_records_total=quality["n_records"], noise_records_accepted=quality["n_accepted"],
                noise_records_rejected=quality["n_rejected"],
                excluded_noise_records=";".join(quality["rejected_records"]),
                sensitivity_before_rejection_fT_per_sqrtHz=quality["before_sensitivity_fT_per_sqrtHz"])
    with np.load(directory / "results/psd_data.npz", allow_pickle=False) as spectra:
        for row in rows:
            key = row["key"]
            if not row["valid"] or key + "_frequency_Hz" not in spectra:
                continue
            frequency, psd = spectra[key + "_frequency_Hz"], spectra[key + "_psd_V2_per_Hz"]
            raw = np.sqrt(psd) / (abs(row["slope_V_per_nT"]) / 1e6)
            estimated = raw * np.sqrt(1 + (frequency / row["hwhm_Hz"])**2)
            unfiltered = np.sqrt(spectra[key + "_unfiltered_psd_V2_per_Hz"]) / (abs(row["slope_V_per_nT"]) / 1e6)
            mask = (frequency >= low) & (frequency <= high)
            covered = bool(frequency.size and frequency[-1] >= high and mask.sum() >= 2)
            row.update(sensitivity_fT_per_sqrtHz=float(np.median(raw[mask])) if covered else None,
                       model_sensitivity_fT_per_sqrtHz=float(np.median(estimated[mask])) if covered else None,
                       sensitivity_band_valid=covered,
                       band_exceeds_lorentzian_hwhm=bool(high > row["hwhm_Hz"]),
                       sensitivity_rejection_reason="" if covered else "设定频带超出实际 PSD 频率范围或有效频点不足")
            arrays.update({key + "_frequency_Hz": frequency, key + "_raw_fT_per_sqrtHz": raw,
                           key + "_single_pole_estimate_fT_per_sqrtHz": estimated,
                           key + "_unfiltered_fT_per_sqrtHz": unfiltered})
    controls = {}
    for name in dict.fromkeys(row["control"] for row in rows):
        group = [row for row in rows if row["control"] == name]
        reference = next((row for row in group if row["scale"] == 1), None)
        normalized = bool(reference and reference["valid"] and reference["slope_V_per_nT"] > 0)
        if normalized:
            g0 = reference["slope_V_per_nT"]
            for row in group:
                if row["valid"]:
                    row["normalized_slope"] = row["slope_V_per_nT"] / g0
                    row["normalized_slope_se"] = 0.0 if row is reference else float(np.hypot(
                        row["slope_se_V_per_nT"] / g0,
                        row["slope_V_per_nT"] * reference["slope_se_V_per_nT"] / g0**2))
                    if reference.get("sensitivity_fT_per_sqrtHz") and row.get("sensitivity_fT_per_sqrtHz"):
                        row["relative_sensitivity"] = row["sensitivity_fT_per_sqrtHz"] / reference["sensitivity_fT_per_sqrtHz"]
        controls[name] = {"points": group, "normalization_scale": 1.0 if normalized else None,
            "normalization_reason": "" if normalized else "未采集扫描倍率 1" if reference is None else "扫描倍率 1 的拟合无效"}
    plot_labels = {name: name for name in controls}
    scope = run_format.calibration_scope(config)
    phase_description = ("各控制仅在标称倍率校相，其他倍率固定标称相位" if scope == "nominal_control" else
        "所有控制 × Scale 共用手动 RF 相位，其余校相逐点独立执行" if not config["parameters"].get("Z_PHASE_SCAN_ENABLED", True) else
        "每个控制 × Scale 独立完成四级校相，再用该点相位进行完整测量")
    summary = _plain(dict(controls=controls, points=rows, plot_labels=plot_labels, comparison_band_Hz=[low, high],
        raw_format_version=config["raw_format_version"], phase_calibration_scope=scope,
        rf_phase_mode="nominal_control" if scope == "nominal_control" else "scan" if config["parameters"].get("Z_PHASE_SCAN_ENABLED", True) else "manual",
        rf_manual_phase_deg=None if config["parameters"].get("Z_PHASE_SCAN_ENABLED", True) else config["parameters"]["Z_BURST_PHASE_INIT"] % 360,
        common_band_valid=band_valid and all(row.get("sensitivity_band_valid", False) for row in valid),
        sensitivity_statistic="median of the slope-referred ASD over the configured common baseband frequency band",
        noise_enabled=config["parameters"]["INCLUDE_NOISE"],
        noise_rejection=dict(policy=quality_summary["policy"], total_records=quality_summary["total_records"],
            rejected_records=quality_summary["rejected_records"], affected_groups=quality_summary["affected_groups"],
            summary_file="noise_rejection_summary.json", record_file="noise_record_quality.csv"),
        slope_definition="abs(A)/gamma**2 at the fitted dispersive center; same as RF DirectAW",
        dispersive_linewidth_definition="HWHM = abs(gamma); equivalent Hz = abs(gamma_nT) * LARMOR_PER_NT",
        gyromagnetic_ratio_Hz_per_nT=LARMOR_PER_NT,
        full_analysis_panels=[key for key, _ in FULL_ANALYSIS_PANELS],
        sensitivity_assumption=f"固定目标 RF；{phase_description}，沿用 DirectAW，以各倍率色散拟合中心斜率 |A|/γ² 换算场等效噪声。噪声仍在零 RF 采集，中心斜率与零场增益的等同性需结合保存的色散中心和零场导数判断；未独立标定 X 动态频响。噪声可按统一的保守高值规则剔除个别异常记录后平均 PSD，原始各条 PSD、未剔除结果和清单均保留；剔除后指标是去除突发异常的稳态估计，不代表采集全过程没有噪声恶化。各组按原设定公共频带取灵敏度谱中位数，不用 HWHM 作为硬截止。超出 HWHM 单独标记，不表示噪声异常；单极点补偿继续使用洛伦兹扫频线宽且仅为模型估计，色散等效线宽只用于独立比较。",
        uncertainty="斜率形式标准误由色散四参数完整协方差传播，线宽取 gamma 标准误；不含标定、相关残差及跨运行重复性。同控制的归一化点共用参考分母。"))
    write_state(directory / "results/analysis.json", summary)
    (directory / "results/analysis.yaml").write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with (directory / "results/metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)
    np.savez(directory / "results/sensitivity.npz", **arrays)
    plot_results(directory, rows, arrays, comparison_band_hz=(low, high))
    return summary
