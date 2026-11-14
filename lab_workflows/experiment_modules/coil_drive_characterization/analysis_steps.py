"""离线输入资格、帧读取与两段分析；不连接仪器，不改写 raw。"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from . import processing, run_format
from .stages import ANALYSIS_KEYS

FRAME_NAME = re.compile(r"(zero_off|zero_on)_\d{2}\.npz|gain_check\.npz|(sine|pulse)_\d{2}\.npz")


class AnalysisQualityError(ValueError):
    """输入或拟合不满足分析条件；诊断仍保存。"""


def output_path(run_dir, name):
    root = Path(run_dir).resolve()
    path = (root / name).resolve()
    if path != root and root not in path.parents:
        raise ValueError("输入或产物路径越出运行目录")
    return path


def load_config(run_dir):
    config = run_format.read_config(output_path(run_dir, "experiment_config.yaml"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config, run_format.parameters(config)


def resolve_options(base, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖值：" + "、".join(sorted(unknown)))
    value = options.get("AREA_TOLERANCE", base.area_tolerance)
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 0.5:
        raise ValueError("AREA_TOLERANCE 必须在 0～0.5 之间")
    return type(base).from_external(base.to_external() | options)


def signature(path):
    if not path.is_file():
        return None
    return [path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()]


def fingerprint(run_dir):
    root = Path(run_dir)
    paths = [output_path(root, "experiment_config.yaml"), *sorted(output_path(root, "raw").glob("*.npz"))]
    return {p.relative_to(root).as_posix(): signature(p) for p in paths}


def load_frames(run_dir, kinds):
    """按文件名排序读取指定类型的帧；字段缺失、长度不符或非有限值即拒绝。"""
    frames = []
    for path in sorted(output_path(run_dir, "raw").glob("*.npz")):
        if not FRAME_NAME.fullmatch(path.name):
            continue
        check_cancelled()
        with np.load(path, allow_pickle=False) as archive:
            meta = json.loads(str(archive["meta"]))
            if meta.get("kind") not in kinds:
                continue
            t, v = np.asarray(archive["time"], float), np.asarray(archive["sense_v"], float)
        if t.ndim != 1 or t.shape != v.shape or len(t) < 10 or not (np.isfinite(t).all() and np.isfinite(v).all()):
            raise AnalysisQualityError(f"{path.name} 时间轴或电压无效")
        frames.append((path.name, meta, t, v))
    return frames


def transfer(run_dir, params):
    """B01：零偏、增益核对与正弦传递函数（扫频帧可缺省）。"""
    r = params.sense_resistor_ohm
    zero = {kind: [float(np.mean(v)) for _, _, _, v in load_frames(run_dir, {kind})] for kind in ("zero_off", "zero_on")}
    if not zero["zero_off"] or not zero["zero_on"]:
        raise AnalysisQualityError("缺少零偏帧（功放关/开两态），请先完成 A03")
    noise = [float(np.std(v)) for _, _, _, v in load_frames(run_dir, {"zero_on"})]
    baseline = float(np.mean(zero["zero_off"]))
    offset = (float(np.mean(zero["zero_on"])) - baseline) / r
    result = {"zero_offset": dict(scope_baseline_v=baseline, offset_current_a=offset,
                                  offset_current_frame_std_a=float(np.std(zero["zero_on"]) / r),
                                  noise_rms_a=float(np.mean(noise) / r))}
    gain = load_frames(run_dir, {"gain_check"})
    if gain:
        _, meta, _, v = gain[0]
        measured = ((float(np.mean(v)) - baseline) / r - offset) / meta["drive_v"]
        result["gain_check"] = dict(drive_v=meta["drive_v"], measured_amps_per_volt=measured,
                                    predicted_amps_per_volt=params.amps_per_drive_volt,
                                    ratio=measured / params.amps_per_drive_volt)
    points, traces = [], []
    for _, meta, t, v in load_frames(run_dir, {"sine"}):
        current = v / r
        fit = processing.fit_sine(t, current, meta["frequency_hz"])
        points.append(processing.point_from_fit(fit, meta["frequency_hz"], meta["drive_peak_v"]))
        traces.append(processing.SineTrace(meta["frequency_hz"], meta["drive_peak_v"], t, current, fit))
    result["transfer"] = [asdict(point) for point in points]
    result["bandwidth_3db_hz"] = processing.bandwidth_3db(points) if len(points) > 1 else None
    output_path(run_dir, "results/transfer.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                             encoding="utf-8")
    if traces:
        processing.plot_sine_frames(traces, output_path(run_dir, "results/coil_drive_sine.png"))
    return result


def pulses(run_dir, params, b01):
    """B02：脉冲指标、最短可用脉宽、汇总图与 summary.json。

    记录窗未包含衰减尾部的帧整帧跳过并记入 summary["pulse_skipped"]，不因此中止分析；
    只有全部脉冲帧都不可用时才判失败。
    """
    traces, metrics, skipped = [], [], []
    for name, meta, t, v in load_frames(run_dir, {"pulse"}):
        current = v / params.sense_resistor_ohm
        try:
            metric = processing.step_metrics(t, current, meta["width_s"])
        except processing.TruncatedPulse as exc:
            skipped.append({"frame": name, "width_s": meta["width_s"], "reason": str(exc)})
            continue
        except ValueError as exc:
            raise AnalysisQualityError(f"{name}: {exc}") from exc
        metrics.append(metric)
        traces.append((meta["width_s"], t, current))
    if not metrics:
        raise AnalysisQualityError("所有脉冲帧的记录窗都不完整，无法给出脉冲指标；"
                                  "请加大 PULSE_WINDOW_MULTIPLE 或 PULSE_MIN_WINDOW_US 重采")
    rows = processing.pulse_area_table(metrics)
    summary = dict(b01, pulse=rows, pulse_skipped=skipped, area_tolerance_fraction=params.area_tolerance,
                   shortest_usable_pulse_width_s=processing.shortest_usable_width(rows, params.area_tolerance),
                   rise_10_90_s=max(metrics, key=lambda m: m.width_s).rise_10_90_s)
    # 汇总拆成单张图：幅频、相频、脉冲响应各一张，缺数据的那张不生成。
    processing.plot_summary(summary, traces, output_path(run_dir, "results"))
    output_path(run_dir, "results/summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
    return summary
