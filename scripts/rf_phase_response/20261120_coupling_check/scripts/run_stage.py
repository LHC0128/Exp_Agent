"""耦合验证（coupling_check）阶段采集驱动：按 runlist 逐点跑 rf-phase-response，逐点校验并离线分析。

用法（开发目录，先设数据根）：
    export LAB_DATA_ROOT="D:/Code/exp_agent_run/data"
    agent_exp_env/Scripts/python.exe scripts/rf_phase_response/20261119_rf_phase_scan/coupling_check/scripts/run_stage.py stage3

路径：逐点记录与日志写运行目录 `_studies/20261119_rf_phase_scan/coupling_check/`，
运行清单从本目录上一级的 `runlists/` 读；不直接连接仪器，经运行目录 GUI 的 `tools.lab_client` 启停实验。

行为：
- 读 runlists/<stage>.csv，按 order 顺序执行，不重排。
- 每点显式传 MAIN_FIELD_CURRENT_MA / Z_PULSE_WIDTH_US / Z_TAIL_S_F / Z_TAIL_TAU_E_US / RUN_TAG / N_REPEAT，
  每个参数都配一个 --allow-param（范围或候选清单）。
- 校验退出码、任务状态、raw/phase_response.csv 行数（36 点 + 表头 = 37）。
- 逐点做离线分析（与 GUI B01 同一代码），写 results/ 并取 S、|c0|。
- 任一点失败立即停，不重试；日志与 JSONL 记录保留现场。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cc_common as cc

REPO = Path(__file__).resolve().parents[5]
DEV = REPO
LOG_DIR = cc.PROCESS_DIR / "logs"
DATA_DIR = cc.PROCESS_DIR
RESULT_DIR = cc.RESULT_DIR
EXPERIMENT = "rf-phase-response"
DATA_TYPE = "rf_phase_response"
PY = str(REPO / "agent_exp_env" / "Scripts" / "python.exe")

PARAMS = ("MAIN_FIELD_CURRENT_MA", "Z_PULSE_WIDTH_US", "Z_TAIL_S_F", "Z_TAIL_TAU_E_US", "RUN_TAG", "N_REPEAT")


def log(handle, text):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def load_list(name):
    path = RESULT_DIR / "runlists" / f"{name}.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda row: int(row["order"]))
    return rows


def allow_bounds(rows):
    """按清单实际取值生成授权范围：数值取 min:max，RUN_TAG 取候选清单。"""
    out = {}
    for name in ("MAIN_FIELD_CURRENT_MA", "Z_PULSE_WIDTH_US", "Z_TAIL_S_F", "Z_TAIL_TAU_E_US"):
        vals = [float(row[col]) for row in rows for col in [{"Z_PULSE_WIDTH_US": "w", "Z_TAIL_S_F": "sf",
                                                             "Z_TAIL_TAU_E_US": "te",
                                                             "MAIN_FIELD_CURRENT_MA": "z"}[name]]]
        out[name] = f"{min(vals):g}:{max(vals):g}"
    out["N_REPEAT"] = "1"
    out["RUN_TAG"] = "|".join(row["tag"] for row in rows)
    return out


def build_command(row, bounds):
    values = {
        "MAIN_FIELD_CURRENT_MA": float(row["z"]),
        "Z_PULSE_WIDTH_US": float(row["w"]),
        "Z_TAIL_S_F": float(row["sf"]),
        "Z_TAIL_TAU_E_US": float(row["te"]),
        "RUN_TAG": row["tag"],
        "N_REPEAT": 1,
    }
    cmd = [PY, "-m", "tools.lab_client", "run", EXPERIMENT, "--allow", EXPERIMENT]
    for name in PARAMS:
        cmd += ["--allow-param", f"{name}={bounds[name]}"]
    for name in PARAMS:
        cmd += ["--param", f"{name}={values[name]}"]
    cmd += ["--timeout", "600"]
    return cmd


def dry_run(handle, command):
    cmd = command[:command.index("--timeout")] + ["--dry-run"]
    log(handle, "预检 dry-run：" + " ".join(cmd[3:]))
    proc = subprocess.run(cmd, cwd=DEV, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (LOG_DIR / "dry_run.json").write_text(proc.stdout or proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        log(handle, f"预检失败退出码 {proc.returncode}：{(proc.stdout or proc.stderr)[-800:]}")
        return False
    log(handle, "预检通过")
    return True


def analyse(run_dir, handle):
    """离线分析（B01），返回 (S_mV_per_deg, |c0|_mV)。"""
    from lab_workflows.experiment_modules.rf_phase_response.analysis import analyze

    summary = analyze(Path(run_dir))
    sens = float(summary["sensitivity_max_v_per_deg"]) * 1e3
    c0 = float(summary["components"]["c0"]["abs_v"]) * 1e3
    log(handle, f"分析完成 S={sens:.4f} mV/deg |c0|={c0:.1f} mV 拟合={summary.get('fit_quality')}")
    return sens, c0


def rows_of(run_dir):
    path = Path(run_dir) / "raw" / "phase_response.csv"
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["stage1", "stage2", "stage3"])
    parser.add_argument("--list", dest="list_name", default=None,
                        help="runlists/ 下的清单名，默认与 stage 同名；阶段 2 用 stage2_filled")
    parser.add_argument("--start-order", type=int, default=1)
    args = parser.parse_args(argv)
    list_name = args.list_name or args.stage

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_list(list_name)
    bounds = allow_bounds(rows)
    jsonl = DATA_DIR / f"{args.stage}_runs.jsonl"

    with (LOG_DIR / f"{args.stage}.log").open("a", encoding="utf-8") as handle:
        log(handle, f"===== {args.stage} 开始，共 {len(rows)} 点，从 order={args.start_order} 起")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

        first = next(row for row in rows if int(row["order"]) >= args.start_order)
        if not dry_run(handle, build_command(first, bounds)):
            return 2

        failures = []
        for row in rows:
            order = int(row["order"])
            if order < args.start_order:
                continue
            tag = row["tag"]
            cmd = build_command(row, bounds)
            command_started = datetime.now()
            log(handle, f"--- order={order} {tag} z={row['z']} w={row['w']} sf={row['sf']} te={row['te']}")
            proc = subprocess.run(cmd, cwd=DEV, capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                report = json.loads(proc.stdout)
            except ValueError:
                report = {}
            if proc.returncode != 0:
                failures.append(f"{tag}: 退出码 {proc.returncode}: {(proc.stdout or proc.stderr)[-500:]}")
                break
            run_dir = report.get("run_dir")
            if not run_dir:
                failures.append(f"{tag}: 报告缺 run_dir: {json.dumps(report, ensure_ascii=False)[:400]}")
                break
            count = rows_of(run_dir)
            if count < 37:
                failures.append(f"{tag}: raw/phase_response.csv 只有 {count} 行（应 37）")
                break
            try:
                sens, c0 = analyse(run_dir, handle)
            except Exception as exc:  # 分析失败同样停止，保留现场
                failures.append(f"{tag}: 离线分析失败 {type(exc).__name__}: {exc}")
                break
            record = {
                "order": order,
                "tag": tag,
                "role": row["role"],
                "position": row["position"],
                "z": float(row["z"]),
                "w": float(row["w"]),
                "sf": float(row["sf"]),
                "te": float(row["te"]),
                "area_total": float(row["area_total"]),
                "run_dir": str(run_dir),
                "S_mV_per_deg": sens,
                "c0_mV": c0,
                "raw_rows": count,
                "status": report.get("status"),
                "safety_shutdown": (report.get("safety_shutdown") or {}).get("completed"),
                "started_at": command_started.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            log(handle, f"OK {tag} 用时 {int((datetime.now() - command_started).total_seconds())} s")
            time.sleep(2)

        if failures:
            for item in failures:
                log(handle, "FAILED " + item)
            log(handle, f"===== {args.stage} 停止：失败 {len(failures)} 项，未自动重试")
            return 1
        log(handle, f"===== {args.stage} 完成 {len(rows)} 点")
        return 0


if __name__ == "__main__":
    sys.exit(main())
