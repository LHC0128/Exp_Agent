"""XY 平衡方格扫描的采集驱动：1 Vpp 射频、相位步进 5°、25 个偏置点 + 3 个参考点。

用法（开发目录，先设数据根）：
    export LAB_DATA_ROOT="D:/Code/exp_agent_run/data"
    agent_exp_env/Scripts/python.exe scripts/rf_phase_response/20261122_xy_balance_scan/scripts/run_balance_scan.py

行为：逐点显式传 X_BIAS_V / Y_BIAS_V 与冻结参数（含 Y_RF_VPP=1.0、相位 0–360/5°），每参数配 --allow-param；
校验退出码、任务状态与 raw 行数（360/5 + 1 = 73）；逐点做谐波分解（c0、±1、±2、±3 次）；
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lab_workflows.common import data_root  # noqa: E402

import harmonics  # noqa: E402

DATA_TYPE = "rf_phase_response"
STUDY = "20261122_xy_balance_scan"
PROCESS_DIR = data_root() / DATA_TYPE / "_studies" / STUDY
RESULT_DIR = HERE.parent
REPO = HERE.parents[3]  # HERE=<root>/scripts/<data_type>/<研究>/scripts
PY = str(REPO / "agent_exp_env" / "Scripts" / "python.exe")
LOG_DIR = PROCESS_DIR / "logs"
EXPERIMENT = "rf-phase-response"
EXPECTED_ROWS = 73  # 72 个相位 + 表头

FROZEN = {
    "MAIN_FIELD_CURRENT_MA": -0.0655, "Z_PULSE_WIDTH_US": 8.6, "Z_TAIL_S_F": 0.5,
    "Z_TAIL_TAU_E_US": 38.3, "Y_RF_VPP": 1.0, "RF_DIRECTION": "Y",
    "Y_RF_PHASE_START_DEG": 0.0, "Y_RF_PHASE_STOP_DEG": 360.0, "Y_RF_PHASE_STEP_DEG": 5.0,
    "N_REPEAT": 1,
}


def log(handle, text):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def load_list(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(rows, key=lambda row: int(row["order"]))


def bounds(rows):
    xs = [float(row["x"]) for row in rows]
    ys = [float(row["y"]) for row in rows]
    out = {}
    for name, value in FROZEN.items():
        out[name] = "|".join([str(value)]) if isinstance(value, str) else f"{value:g}:{value:g}"
    out["X_BIAS_V"] = f"{min(xs):g}:{max(xs):g}"
    out["Y_BIAS_V"] = f"{min(ys):g}:{max(ys):g}"
    out["RUN_TAG"] = "|".join(row["tag"] for row in rows)
    return out


def command(row, limits):
    values = dict(FROZEN, X_BIAS_V=float(row["x"]), Y_BIAS_V=float(row["y"]), RUN_TAG=row["tag"])
    cmd = [PY, "-m", "tools.lab_client", "run", EXPERIMENT, "--allow", EXPERIMENT]
    for name in values:
        cmd += ["--allow-param", f"{name}={limits[name]}"]
    for name in values:
        cmd += ["--param", f"{name}={values[name]}"]
    return cmd + ["--timeout", "900"]


def dry_run(handle, cmd):
    probe = cmd[:cmd.index("--timeout")] + ["--dry-run"]
    proc = subprocess.run(probe, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        log(handle, f"预检失败退出码 {proc.returncode}: {(proc.stdout or proc.stderr)[-600:]}")
        return False
    log(handle, "预检通过")
    return True


def rows_of(run_dir):
    path = Path(run_dir) / "raw" / "phase_response.csv"
    return sum(1 for _ in path.open(encoding="utf-8")) if path.is_file() else 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-order", type=int, default=1)
    parser.add_argument("--runlist", default="balance_scan")
    args = parser.parse_args(argv)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_list(RESULT_DIR / "runlists" / f"{args.runlist}.csv")
    limits = bounds(rows)
    jsonl = PROCESS_DIR / f"{args.runlist}_runs.jsonl"
    with (LOG_DIR / f"{args.runlist}.log").open("a", encoding="utf-8") as handle:
        log(handle, f"===== XY 平衡方格扫描开始，共 {len(rows)} 点，从 order={args.start_order}")
        pending = [row for row in rows if int(row["order"]) >= args.start_order]
        if not dry_run(handle, command(pending[0], limits)):
            return 2

        for row in pending:
            started = datetime.now()
            log(handle, f"--- order={row['order']} {row['tag']} x={row['x']} y={row['y']}")
            proc = subprocess.run(command(row, limits), cwd=REPO, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            try:
                report = json.loads(proc.stdout)
            except ValueError:
                report = {}
            if proc.returncode != 0:
                log(handle, f"FAILED {row['tag']} 退出码 {proc.returncode}: {(proc.stdout or proc.stderr)[-500:]}")
                return 1
            run_dir = report.get("run_dir")
            if not run_dir:
                log(handle, f"FAILED {row['tag']} 报告缺 run_dir")
                return 1
            count = rows_of(run_dir)
            if count < EXPECTED_ROWS:
                log(handle, f"FAILED {row['tag']} raw 只有 {count} 行（应 {EXPECTED_ROWS}）")
                return 1
            try:
                record, _, _ = harmonics.summarise(run_dir)
            except Exception as exc:
                log(handle, f"FAILED {row['tag']} 谐波分解 {type(exc).__name__}: {exc}")
                return 1
            record.update(order=int(row["order"]), tag=row["tag"], role=row["role"],
                          x=float(row["x"]), y=float(row["y"]), run_dir=str(run_dir), raw_rows=count,
                          status=report.get("status"),
                          safety_shutdown=(report.get("safety_shutdown") or {}).get("completed"),
                          started_at=started.isoformat(timespec="seconds"),
                          finished_at=datetime.now().isoformat(timespec="seconds"))
            with jsonl.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            log(handle, f"OK {row['tag']} |c0|={record['c0_mV']:.1f} mV |c1|={record['c1_mV']:.1f} "
                        f"c2/c1={record['even_ratio']:.3f} c3/c1={record['third_ratio']:.3f} "
                        f"R峰={record['r_peak_mV']:.0f} mV 用时 {int((datetime.now() - started).total_seconds())} s")
            time.sleep(2)
        log(handle, f"===== XY 平衡方格扫描完成 {len(pending)} 点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
