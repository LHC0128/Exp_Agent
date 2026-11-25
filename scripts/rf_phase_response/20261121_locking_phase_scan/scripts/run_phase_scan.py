"""锁相解调相位扫描的采集驱动：每个解调相位跑一次完整 36 点相位响应。

用法（开发目录，先设数据根）：
    export LAB_DATA_ROOT="D:/Code/exp_agent_run/data"
    agent_exp_env/Scripts/python.exe scripts/rf_phase_response/20261121_locking_phase_scan/scripts/run_phase_scan.py [--runlist phase_scan]

行为：逐点显式传 DEMOD0_PHASE_DEG 与冻结的射频/脉冲/偏置参数，每个参数配 --allow-param；
校验退出码、任务状态、raw/phase_response.csv 行数（36 点 + 表头 = 37）；
逐点做离线分析，记录 S、c0 复数、c±1、最佳读相位、R 峰与残差；
任一点失败立即停、不重试；记录与日志写运行目录 _studies。不直接连接仪器。
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lab_workflows.common import data_root  # noqa: E402

DATA_TYPE = "rf_phase_response"
STUDY = "20261121_locking_phase_scan"
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY
RESULT_DIR = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
PY = str(REPO / "agent_exp_env" / "Scripts" / "python.exe")
LOG_DIR = PROCESS_DIR / "logs"
EXPERIMENT = "rf-phase-response"

# 冻结参数：当前工作点（2026-11-20 定下的最优 Z 脉冲与偏置），逐点显式传。
FROZEN = {"MAIN_FIELD_CURRENT_MA": -0.0655, "Z_PULSE_WIDTH_US": 8.6,
          "Z_TAIL_S_F": 0.5, "Z_TAIL_TAU_E_US": 38.3, "N_REPEAT": 1,
          "X_BIAS_V": 0.16, "Y_BIAS_V": 0.08, "Y_RF_VPP": 0.05}


def log(handle, text):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def load_list(list_path):
    with list_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(rows, key=lambda row: int(row["order"]))


def bounds(rows):
    degrees = [float(row["demod_deg"]) for row in rows]
    out = {name: f"{value:g}:{value:g}" for name, value in FROZEN.items()}
    out["DEMOD0_PHASE_DEG"] = f"{min(degrees):g}:{max(degrees):g}"
    out["RF_DIRECTION"] = "|".join(sorted({str(row.get("direction") or "Y").upper() for row in rows}))
    out["RUN_TAG"] = "|".join(row["tag"] for row in rows)
    return out


def command(row, limits):
    values = dict(FROZEN, DEMOD0_PHASE_DEG=float(row["demod_deg"]),
                  RF_DIRECTION=str(row.get("direction") or "Y").upper(), RUN_TAG=row["tag"])
    cmd = [PY, "-m", "tools.lab_client", "run", EXPERIMENT, "--allow", EXPERIMENT]
    for name in values:
        cmd += ["--allow-param", f"{name}={limits[name]}"]
    for name in values:
        cmd += ["--param", f"{name}={values[name]}"]
    return cmd + ["--timeout", "600"]


def dry_run(handle, cmd):
    probe = cmd[:cmd.index("--timeout")] + ["--dry-run"]
    proc = subprocess.run(probe, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        log(handle, f"预检失败退出码 {proc.returncode}: {(proc.stdout or proc.stderr)[-500:]}")
        return False
    log(handle, "预检通过")
    return True


def analyse(run_dir, handle):
    from lab_workflows.experiment_modules.rf_phase_response.analysis import analyze

    summary = analyze(Path(run_dir))
    components = summary["components"]
    c0, cp, cm = components["c0"], components["c_plus1"], components["c_minus1"]
    record = {
        "S_mV_per_deg": float(summary["sensitivity_max_v_per_deg"]) * 1e3,
        "c0_re_mV": c0["re_v"] * 1e3, "c0_im_mV": c0["im_v"] * 1e3, "c0_abs_mV": c0["abs_v"] * 1e3,
        "c0_arg_deg": c0["arg_deg"],
        "c1_mV": cp["abs_v"] * 1e3, "c1_arg_deg": cp["arg_deg"],
        "cm1_mV": cm["abs_v"] * 1e3, "cm1_arg_deg": cm["arg_deg"],
        "best_phase_deg": summary.get("best_phase_deg"),
        "fit_quality": summary.get("fit_quality"),
        "r_peak_v": max(point["R_mean_v"] for point in summary["points"]),
        "residual_mV": float(summary["residual_rms_v"]) * 1e3,
    }
    log(handle, f"分析完成 S={record['S_mV_per_deg']:.4f} |c0|={record['c0_abs_mV']:.2f} mV "
                f"arg(c0)={record['c0_arg_deg']:.1f}° 最佳相位={record['best_phase_deg']} "
                f"R峰={record['r_peak_v']:.3f} V 残差={record['residual_mV']:.1f} mV")
    return record


def rows_of(run_dir):
    path = Path(run_dir) / "raw" / "phase_response.csv"
    return sum(1 for _ in path.open(encoding="utf-8")) if path.is_file() else 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-order", type=int, default=1)
    parser.add_argument("--runlist", default="phase_scan", help="runlists/<名字>.csv，默认 phase_scan")
    args = parser.parse_args(argv)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    list_path = RESULT_DIR / "runlists" / f"{args.runlist}.csv"
    jsonl = PROCESS_DIR / f"{args.runlist}_runs.jsonl"
    rows = load_list(list_path)
    limits = bounds(rows)
    with (LOG_DIR / f"{args.runlist}.log").open("a", encoding="utf-8") as handle:
        log(handle, f"===== 锁相相位扫描 {args.runlist} 开始，共 {len(rows)} 点，从 order={args.start_order}")
        pending = [row for row in rows if int(row["order"]) >= args.start_order]
        if not dry_run(handle, command(pending[0], limits)):
            return 2

        for row in pending:
            started = datetime.now()
            log(handle, f"--- order={row['order']} {row['tag']} demod={row['demod_deg']}°")
            proc = subprocess.run(command(row, limits), cwd=REPO, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            try:
                report = json.loads(proc.stdout)
            except ValueError:
                report = {}
            if proc.returncode != 0:
                log(handle, f"FAILED {row['tag']} 退出码 {proc.returncode}: {(proc.stdout or proc.stderr)[-400:]}")
                return 1
            run_dir = report.get("run_dir")
            if not run_dir:
                log(handle, f"FAILED {row['tag']} 报告缺 run_dir")
                return 1
            count = rows_of(run_dir)
            if count < 37:
                log(handle, f"FAILED {row['tag']} raw 只有 {count} 行")
                return 1
            try:
                result = analyse(run_dir, handle)
            except Exception as exc:
                log(handle, f"FAILED {row['tag']} 离线分析 {type(exc).__name__}: {exc}")
                return 1
            record = dict(result, order=int(row["order"]), tag=row["tag"], role=row["role"],
                          demod_deg=float(row["demod_deg"]), rf_direction=row.get("direction") or "Y",
                          run_dir=str(run_dir), raw_rows=count, status=report.get("status"),
                          safety_shutdown=(report.get("safety_shutdown") or {}).get("completed"),
                          started_at=started.isoformat(timespec="seconds"),
                          finished_at=datetime.now().isoformat(timespec="seconds"))
            with jsonl.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            log(handle, f"OK {row['tag']} 用时 {int((datetime.now() - started).total_seconds())} s")
            time.sleep(2)
        log(handle, f"===== 锁相相位扫描 {args.runlist} 完成 {len(pending)} 点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
