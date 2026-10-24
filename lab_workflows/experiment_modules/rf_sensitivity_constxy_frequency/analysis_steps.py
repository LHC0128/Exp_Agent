"""离线输入资格、参数覆盖、内容指纹与科学产物存取。"""
from dataclasses import fields
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import yaml
from ...experiment_runtime import check_cancelled
from .models import RFConstXYFrequencyParams
from . import run_format
from .stages import ANALYSIS_KEYS
from .analysis_calculation import extract_responses, fit_responses
from .analysis_algorithms import _to_jsonable
from .storage import save_npz

ARRAY_KEYS = ("freqs_daq", "amps_raw", "amps_corr", "picked_freqs", "freq_errors", "is_suspicious_arr", "freqs_dR",
    "R_vec", "R_scalar", "R_scalar_std", "X_mean", "Y_mean", "phase_vec_deg", "phase_vec_rad", "freqs_li", "li_x", "li_y",
    "li_r", "li_phase_deg", "li_phase_rad", "li_n_cycles", "li_n_samples", "li_valid", "ratio_fft_li", "ratio_hw_li", "common_freqs", "common_hw", "is_susp_daq")


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path == root or root not in path.parents:
        raise ValueError("分析输入／产物路径不能越出运行目录")
    return path


def load_config(run_dir):
    config = yaml.safe_load(output_path(run_dir, "experiment_config.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("实验配置必须为映射")
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def resolve_options(base_params, options):
    if unknown := options.keys()-set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数："+"、".join(sorted(unknown)))
    params = RFConstXYFrequencyParams.from_external(base_params.to_external() | options)
    errors = params.validate_analysis()
    for field in fields(params):
        if field.metadata["external_name"] not in ANALYSIS_KEYS:
            continue
        value = getattr(params, field.name)
        if value is None:
            continue
        for bound, compare in (("minimum", lambda v, b: v < b), ("maximum", lambda v, b: v > b)):
            if field.metadata.get(bound) is not None and compare(value, field.metadata[bound]):
                errors.append(f'{field.metadata["external_name"]} 超出参数边界')
    if errors:
        raise ValueError("；".join(errors))
    return params


def signature(path):
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def fingerprint(run_dir):
    load_config(run_dir)
    return dict(analysis_version="constxy-frequency-v1", config=signature(output_path(run_dir, "experiment_config.yaml")),
        raw={path.relative_to(run_dir).as_posix(): signature(output_path(run_dir, path.relative_to(run_dir)))
             for path in sorted(output_path(run_dir, "raw").rglob("*")) if path.is_file()})


def load_inputs(run_dir):
    config, params = load_config(run_dir)
    raw = output_path(run_dir, "raw")
    index = output_path(run_dir, "raw/frequency_index.json")
    if index.is_file():
        records = json.loads(index.read_text(encoding="utf-8"))
    else:
        if config.get("raw_format_version") is not None:
            raise ValueError("新格式缺少权威 JSON 频点索引")
        # 仅已验证的旧本地格式使用 object 索引；点波形一律禁止 pickle。
        with np.load(output_path(run_dir, "raw/frequency_index.npz"), allow_pickle=True) as values:
            records = values["records"].tolist()
    if not isinstance(records, list) or not records:
        raise ValueError("频点索引为空或不是列表")
    points, seen_indices, seen_frequencies = [], set(), set()
    for record in records:
        check_cancelled()
        name, freq, idx = record["file"], float(record["freq_Hz"]), record["idx"]
        if (not isinstance(name, str) or Path(name).name != name or not name.startswith("freq_") or not name.endswith(".npz")
                or idx in seen_indices or freq in seen_frequencies or not math.isfinite(freq) or freq < 0):
            raise ValueError("频点索引存在越界、重复或非法记录")
        seen_indices.add(idx); seen_frequencies.add(freq)
        path = output_path(run_dir, "raw/"+name)
        if path.parent != raw:
            raise ValueError("频点文件必须位于 raw 目录内")
        with np.load(path, allow_pickle=False) as source:
            data = {key: source[key].copy() for key in source.files}
        baseline = bool(record["is_baseline"])
        if float(data["freq_Hz"]) != freq or bool(data["is_baseline"]) != baseline or baseline != (freq == 0):
            raise ValueError("频点身份与索引不一致")
        if str(data["acquisition_mode"]) != params.acquisition_mode:
            raise ValueError("频点采集模式与运行配置不一致")
        if "acquisition_complete" in data and not bool(data["acquisition_complete"]):
            raise ValueError("存在故障抢救的未完整频点；原始数据已保留，不作为完整响应")
        if params.acquisition_mode in ("daq_fft_y", "both"):
            y, axis, rate = np.asarray(data["y_V"]), np.asarray(data["time_s"]), float(data["actual_rate_Sa_s"])
            if (y.ndim != 1 or len(y) < 4 or axis.shape != y.shape or not math.isfinite(rate) or rate <= 0
                    or not np.all(np.isfinite(y)) or not np.all(np.isfinite(axis)) or np.any(np.diff(axis) <= 0)):
                raise ValueError("Y 波形形状、时间轴或实际率无效")
        if not baseline and params.acquisition_mode in ("demod_r", "both"):
            for key in ("demod_r_r_vector_mean_V", "demod_r_r_mean_scalar_V", "demod_r_r_std_scalar_V", "demod_r_x_mean_V",
                        "demod_r_y_mean_V", "demod_r_phase_vector_rad", "demod_r_phase_vector_deg"):
                if not math.isfinite(float(data[key])):
                    raise ValueError("硬件 R 频点字段缺失或非有限")
        points.append(dict(idx=idx, freq_Hz=freq, is_baseline=baseline, data=data))
    return sorted(points, key=lambda point: point["idx"])


def write_product(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+".tmp")
    # 科学文件沿用原 JSON 中 NaN／Infinity 诊断；会话/API 摘要不返回这些数值。
    temporary.write_text(json.dumps(_to_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_product(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    def restore(item, key=""):
        if isinstance(item, dict):
            return {name: restore(v, name) for name, v in item.items()}
        if isinstance(item, list):
            if key in ARRAY_KEYS or key in ("fit_curve_freqs", "fit_curve_values"):
                dtype = bool if key in ("li_valid", "is_suspicious_arr", "is_susp_daq") else float
                return np.asarray(item, dtype=dtype)
            return [restore(v) for v in item]
        return item
    return restore(value)


def write_responses(run_dir, points, params):
    responses = extract_responses(points, params)
    write_product(output_path(run_dir, "results/responses.json"), responses)
    return dict(n_points=len(points), baseline_present=any(point["is_baseline"] for point in points))


def write_fits(run_dir, config, params, points):
    responses = read_product(output_path(run_dir, "results/responses.json"))
    result = fit_responses(responses, config, params, points, run_dir)
    save_npz(output_path(run_dir, "results/frequency_response.npz"), **result.pop("save_npz"))
    analysis = result["analysis"]
    write_product(output_path(run_dir, "results/analysis.json"), analysis)
    output_path(run_dir, "results/analysis.yaml").write_text(yaml.safe_dump(_to_jsonable(analysis), allow_unicode=True, sort_keys=False), encoding="utf-8")
    write_product(output_path(run_dir, "results/fit_data.json"), result)
    return dict(fit_diagnostics={key: {"success": bool(value.get("success")), "message": value.get("message", "disabled")}
        for key, value in result.items() if key.startswith(("lorentzian_", "doublelorentzian_"))})
