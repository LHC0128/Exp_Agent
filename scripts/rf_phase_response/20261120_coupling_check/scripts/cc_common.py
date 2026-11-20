"""耦合验证（coupling_check）阶段数据的公共读取、时间戳解析与漂移归一。

逐点记录与日志由采集驱动 `scripts/run_stage.py` 写到过程目录
`data/rf_phase_response/_studies/20261119_rf_phase_scan/coupling_check/<stage>_runs.jsonl`，
一行一个成功运行；结果表、图与清单写到本目录（tables、figures、runlists）。本模块不做仪器访问。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from lab_workflows.common import data_root

DATA_TYPE = "rf_phase_response"
# 过程文件目录沿用 11-19 的研究目录名（早于本目录 2026-11-20 改名为 20261120_coupling_check）。
STUDY = "20261119_rf_phase_scan"
# 过程文件（逐点 JSONL、采集日志）：运行目录 _studies 布局，不进 git。
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY / "coupling_check"
# 结果输出（tables、figures、runlists）：本文件所在的研究目录，进 git。
RESULT_DIR = Path(__file__).resolve().parents[1]
RUN_ROOT = data_root() / DATA_TYPE
STAMP = re.compile(r"^(\d{2})(\d{2})_(\d{6})_")

# 阶段 1 拟合用的固定峰形（取自 11-19 阶段 A：峰高 2.349、本底 0.418）。
PEAK_G_MA = 0.01037
PEAK_BETA = 0.151
# 判据：Z0 相对 P0 的允许偏移。
COUPLING_TOLERANCE_MA = 0.001
# 相邻参考点漂移超过这个比例在报告里标出。
DRIFT_FLAG = 0.05


def run_time(run_dir):
    """运行目录名 `MMDD_HHMMSS_<tag>` 的时间戳。"""
    name = Path(run_dir).name
    match = STAMP.match(name)
    if not match:
        raise ValueError(f"运行目录名缺少时间戳：{name}")
    month, day, clock = int(match.group(1)), int(match.group(2)), match.group(3)
    now = datetime.now()
    year = now.year if month <= now.month else now.year - 1
    return datetime(year, month, day, int(clock[0:2]), int(clock[2:4]), int(clock[4:6]))


def load(stage):
    """读某阶段的 JSONL 记录，按 order 排序。"""
    path = PROCESS_DIR / f"{stage}_runs.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        row["time"] = run_time(row["run_dir"])
    return sorted(rows, key=lambda row: row["order"])


def _interp(refs, moment):
    """参考点之间按时间线性插值；区间外沿用最近一段的斜率外推。"""
    stamps = [item["time"] for item in refs]
    if len(stamps) == 1:
        return refs[0]["S_mV_per_deg"], 0.0
    if moment <= stamps[0]:
        low = 0
    elif moment >= stamps[-1]:
        low = len(stamps) - 2
    else:
        low = max(index for index in range(len(stamps) - 1) if stamps[index] <= moment)
    span = (stamps[low + 1] - stamps[low]).total_seconds()
    if span <= 0:
        return refs[low]["S_mV_per_deg"], 0.0
    fraction = (moment - stamps[low]).total_seconds() / span
    slope = (refs[low + 1]["S_mV_per_deg"] - refs[low]["S_mV_per_deg"]) / span
    value = refs[low]["S_mV_per_deg"] + (refs[low + 1]["S_mV_per_deg"] - refs[low]["S_mV_per_deg"]) * fraction
    return value, slope


def drift_normalise(rows):
    """给每条记录加 S_ref(t) 与 r=S/S_ref，并返回参考点漂移摘要。

    参考点自身按“其所在区间内其它参考点的插值”归一，使 r 与扫描点同一口径；
    只有单个参考点时 r≡1。
    """
    refs = sorted((row for row in rows if row["role"] == "ref"), key=lambda row: row["time"])
    if not refs:
        raise ValueError("该阶段缺少参考点，无法做漂移归一")
    for row in rows:
        if row["role"] == "ref":
            # 参考点自身就是刻度，r≡1；用邻近参考点插值只会得到端点外推的假比值。
            row["S_ref"] = row["S_mV_per_deg"]
            row["r"] = 1.0
            continue
        value, _ = _interp(refs, row["time"])
        row["S_ref"] = value
        row["r"] = row["S_mV_per_deg"] / value if value else float("nan")
    gaps = []
    for left, right in zip(refs, refs[1:]):
        ratio = right["S_mV_per_deg"] / left["S_mV_per_deg"] - 1.0
        gaps.append({"from": left["tag"], "to": right["tag"], "change": ratio,
                     "flagged": abs(ratio) > DRIFT_FLAG})
    summary = {"refs": refs, "gaps": gaps,
               "max_abs_change": max((abs(item["change"]) for item in gaps), default=0.0),
               "first_time": refs[0]["time"], "last_time": refs[-1]["time"]}
    return summary


def peak_shape(z, z0):
    """固定形状洛伦兹：只放开峰高与峰位。"""
    return PEAK_BETA + (1.0 - PEAK_BETA) / (1.0 + ((z - z0) / PEAK_G_MA) ** 2)


def offset_minutes(rows):
    """相对该阶段第一个参考点的时间（分钟），用于画漂移图。"""
    refs = [row for row in rows if row["role"] == "ref"]
    base = min(item["time"] for item in refs)
    for row in rows:
        row["minutes"] = (row["time"] - base).total_seconds() / 60.0
    return base


__all__ = ["PROCESS_DIR", "RESULT_DIR", "RUN_ROOT", "PEAK_G_MA", "PEAK_BETA", "COUPLING_TOLERANCE_MA",
           "DRIFT_FLAG", "load", "drift_normalise", "peak_shape", "offset_minutes", "run_time"]
