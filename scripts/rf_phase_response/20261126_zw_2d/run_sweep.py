"""Z 脉冲宽度 × GS200 主磁场电流二维扫描 runner（客户端型，不直连仪器）。

每次经 ``tools/lab_client.py`` 运行已发布的 ``rf-phase-response``：A01→A02→A03，
实验自动完成 A04 安全收尾；随后在开发目录离线跑 B01，读取 K0。任一点失败立即停止。

在开发目录直接运行（client 型脚本不走 script-run）：

    agent_exp_env\\Scripts\\python.exe scripts\\rf_phase_response\\20261126_zw_2d\\run_sweep.py --pilot
    agent_exp_env\\Scripts\\python.exe scripts\\rf_phase_response\\20261126_zw_2d\\run_sweep.py

数据：运行目录 ``data/rf_phase_response/<run>/``；本脚本记录与日志写到
``data/rf_phase_response/_studies/20261126_zw_2d/``（points.jsonl、run.log）。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parents[3]
PYTHON = DEV_ROOT / "agent_exp_env" / "Scripts" / "python.exe"
LAB_CLIENT = DEV_ROOT / "tools" / "lab_client.py"
EXPERIMENT_ID = "rf-phase-response"

WIDTHS_US = (8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
CURRENTS_MA = (-0.090, -0.0775, -0.0650, -0.0525, -0.0400)
REF_WIDTH_US, REF_CURRENT_MA = 8.6, -0.0655
PHASE_STEP_DEG = 15.0
REF_EVERY = 6
SEED = 20261126
# 36 点基准运行：1126_174209 为 0.0120848、1126_180050 为 0.0117417（18 分钟漂移 −2.8%）。
# pilot 用较近的 18:00 复测值判断 15° 网格是否可用。
BASELINE_K0 = 0.0117417

sys.path.insert(0, str(DEV_ROOT))
os.environ.setdefault("LAB_DATA_ROOT", r"D:\Code\exp_agent_run\data")

from lab_workflows.experiment_modules.rf_phase_response import analysis  # noqa: E402

# 运行目录数据根：客户端脚本不依赖开发目录的 data_root()（它解析到开发仓库根）。
RUN_DATA_ROOT = Path(r"D:\Code\exp_agent_run\data")


def _log(handle, message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    if handle is not None:
        handle.write(line + "\n")
        handle.flush()


def _run_point(width_us: float, current_ma: float, rf_vpp: float | None = None) -> dict:
    """调用 lab_client 跑一次 A01-A03，返回任务 JSON；失败抛异常。"""
    args = [
        str(PYTHON), str(LAB_CLIENT), "run", EXPERIMENT_ID, "--allow", EXPERIMENT_ID,
        "--steps", "A01,A02,A03", "--timeout", "600", "--cleanup-timeout", "180",
        f"--allow-param=Z_PULSE_WIDTH_US={width_us}:{width_us}",
        f"--allow-param=MAIN_FIELD_CURRENT_MA={current_ma}:{current_ma}",
        f"--allow-param=Y_RF_PHASE_STEP_DEG=10.0:30.0",
        f"--param=Z_PULSE_WIDTH_US={width_us}",
        f"--param=MAIN_FIELD_CURRENT_MA={current_ma}",
        f"--param=Y_RF_PHASE_STEP_DEG={PHASE_STEP_DEG}",
    ]
    if rf_vpp is not None:
        args += [f"--allow-param=Y_RF_VPP={rf_vpp}:{rf_vpp}", f"--param=Y_RF_VPP={rf_vpp}"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.run(args, cwd=DEV_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        raise RuntimeError(
            f"lab_client 退出码 {proc.returncode}；stdout 末尾：{proc.stdout[-1500:]!r}；"
            f"stderr 末尾：{proc.stderr[-1500:]!r}")
    text = proc.stdout.strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"lab_client 未输出 JSON：{text[-1500:]!r}")
    payload = json.loads(text[start:])
    for key in ("session_error", "step_error"):
        if payload.get(key):
            raise RuntimeError(f"任务失败 {key}={payload[key]}；payload={json.dumps(payload, ensure_ascii=False)[:1200]}")
    states = payload.get("step_states") or {}
    bad = [key for key, value in states.items() if (value or {}).get("status") != "completed"]
    if bad:
        raise RuntimeError(f"任务步骤未完成：{bad}；payload={json.dumps(payload, ensure_ascii=False)[:1500]}")
    shutdown = payload.get("safety_shutdown") or {}
    if not shutdown.get("completed"):
        raise RuntimeError(f"安全收尾未确认：{json.dumps(shutdown, ensure_ascii=False)[:800]}")
    payload["elapsed_s"] = round(elapsed, 1)
    return payload


def _analyze(run_dir: str) -> dict:
    """离线跑 B01 并读取摘要。"""
    from pathlib import Path as _Path
    analysis.analyze(_Path(run_dir))
    summary = json.loads((_Path(run_dir) / "results" / "phase_response_summary.json").read_text(encoding="utf-8"))
    return {
        "k0_v_per_rad": summary.get("k0_v_per_rad"),
        "k0_defined": summary.get("k0_defined"),
        "k0_degenerate_reason": summary.get("k0_degenerate_reason"),
        "response_v_per_rad": summary.get("response_v_per_rad"),
        "fit_quality": summary.get("fit_quality"),
        "rms_v": summary.get("rms_v"),
        "fit_mode": summary.get("fit_mode"),
        "work_point_deg": summary.get("work_point_deg"),
        "y_ptp_v": summary.get("y_peak_to_peak_v"),
    }


def _record(jsonl: Path, record: dict) -> None:
    with jsonl.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_done(jsonl: Path) -> set[tuple[float, float, bool, float | None]]:
    done: set[tuple[float, float, bool, float | None]] = set()
    if not jsonl.exists():
        return done
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "completed":
            rf = row.get("rf_vpp")
            done.add((float(row["width_us"]), float(row["current_ma"]), bool(row["is_ref"]),
                      None if rf is None else float(rf)))
    return done


def _plan(widths, currents, ref_width, ref_current, rf_vpps=None) -> list[tuple[float, float, bool, float | None]]:
    # 只有宽度/电流都只有一个点时才是「射频幅度扫描」；否则按宽度×电流网格，
    # 射频幅度由 --rf-vpp（单值）统一覆盖，避免多值 --rf-vpps 误把网格压成一行。
    if rf_vpps and len(widths) == 1 and len(currents) == 1:
        return [(widths[0], currents[0], False, float(rf)) for rf in rf_vpps]
    grid = [(w, i, False, None) for w in widths for i in currents]
    random.Random(SEED).shuffle(grid)
    refs = [(ref_width, ref_current, True, None)] * (len(grid) // REF_EVERY)
    points: list[tuple[float, float, bool, float | None]] = []
    for index, item in enumerate(grid, start=1):
        points.append(item)
        if index % REF_EVERY == 0 and refs:
            points.append(refs.pop())
    return points


def _parse_list(text: str) -> list[float]:
    return [float(item) for item in text.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Z 宽度 × 主磁场电流二维扫描")
    parser.add_argument("--pilot", action="store_true", help="只跑一次 15° 基准点并与 36 点基准 K0 对比")
    parser.add_argument("--dry-run", action="store_true", help="只打印网格，不运行")
    parser.add_argument("--resume", action="store_true", help="跳过 points.jsonl 中已完成的点")
    parser.add_argument("--cooldown", type=float, default=0.0,
                        help="相邻运行之间的冷却秒数，等 LabOne 释放 MFLI（避免 In use 竞争）")
    parser.add_argument("--study", default="20261126_zw_2d", help="研究记录子目录名")
    parser.add_argument("--widths", default=",".join(str(value) for value in WIDTHS_US),
                        help="逗号分隔的宽度网格（µs）")
    parser.add_argument("--currents", default=",".join(str(value) for value in CURRENTS_MA),
                        help="逗号分隔的主磁场电流网格（mA）")
    parser.add_argument("--ref-width", type=float, default=REF_WIDTH_US, help="基准点宽度（µs）")
    parser.add_argument("--ref-current", type=float, default=REF_CURRENT_MA, help="基准点主磁场电流（mA）")
    parser.add_argument("--rf-vpp", type=float, default=None,
                        help="本次扫描的 Y_RF_VPP 覆盖值（Vpp）；不给则用实验默认")
    parser.add_argument("--rf-vpps", default="",
                        help="逗号分隔 Y_RF_VPP 列表；给出时做一维射频幅度扫描（宽度/电流取列表首值，无基准点）")
    args = parser.parse_args()

    widths = _parse_list(args.widths)
    currents = _parse_list(args.currents)
    rf_vpps = _parse_list(args.rf_vpps) if args.rf_vpps.strip() else None
    study_dir = RUN_DATA_ROOT / "rf_phase_response" / "_studies" / args.study
    study_dir.mkdir(parents=True, exist_ok=True)
    jsonl = study_dir / "points.jsonl"
    log_path = study_dir / "run.log"
    points = _plan(widths, currents, args.ref_width, args.ref_current, rf_vpps)

    if args.dry_run:
        print(f"共 {len(points)} 次运行")
        print("widths_us:", widths)
        print("currents_ma:", currents)
        if rf_vpps:
            print("rf_vpps:", rf_vpps)
        print("顺序:", [(w, i, ref, rf) for w, i, ref, rf in points])
        return 0

    done = _load_done(jsonl) if args.resume else set()
    with log_path.open("a", encoding="utf-8") as handle:
        if args.pilot:
            _log(handle, f"pilot：15° 网格基准点 width={args.ref_width} µs, I={args.ref_current} mA")
            payload = _run_point(args.ref_width, args.ref_current, args.rf_vpp)
            result = _analyze(payload["run_dir"])
            k0 = result.get("k0_v_per_rad")
            _log(handle, f"pilot 结果：{result}；run_dir={payload['run_dir']}")
            if k0 is None:
                _log(handle, "pilot 失败：没有 K0")
                return 1
            delta = (k0 - BASELINE_K0) / BASELINE_K0
            _log(handle, f"15° K0={k0:.6g}，36° 基准 K0={BASELINE_K0:.6g}，相对差 {delta:+.2%}")
            return 0 if abs(delta) <= 0.10 and result.get("fit_quality") != "poor" else 1

        started = time.monotonic()
        ran_in_session = 0
        for index, (width_us, current_ma, is_ref, point_rf) in enumerate(points, start=1):
            rf_vpp = point_rf if point_rf is not None else args.rf_vpp
            key = (width_us, current_ma, is_ref, rf_vpp)
            if key in done:
                _log(handle, f"{index}/{len(points)} 跳过已完成 {key}")
                continue
            if ran_in_session and args.cooldown > 0:
                _log(handle, f"冷却 {args.cooldown:g} s（等 MFLI 释放）")
                time.sleep(args.cooldown)
            ran_in_session += 1
            tag = "基准" if is_ref else "网格"
            _log(handle, f"{index}/{len(points)} 开始 {tag}：width={width_us} µs, I={current_ma} mA"
                         + (f", Y_RF_VPP={rf_vpp:g}" if rf_vpp is not None else ""))
            record = {"order": index, "width_us": width_us, "current_ma": current_ma,
                      "y_rf_vpp": rf_vpp,
                      "is_ref": is_ref, "phase_step_deg": PHASE_STEP_DEG,
                      "started_at": datetime.now().isoformat(timespec="seconds")}
            try:
                payload = _run_point(width_us, current_ma, rf_vpp)
                result = _analyze(payload["run_dir"])
                record.update(status="completed", run_dir=payload["run_dir"],
                              elapsed_s=payload.get("elapsed_s"), **result)
                _record(jsonl, record)
                _log(handle, f"{index}/{len(points)} 完成 K0={record.get('k0_v_per_rad')} "
                             f"quality={record.get('fit_quality')} rms={record.get('rms_v')} "
                             f"（累计 {(time.monotonic() - started) / 60:.1f} min）")
            except BaseException as exc:  # 失败即停，保留现场
                record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                _record(jsonl, record)
                _log(handle, f"{index}/{len(points)} 失败，停止扫描：{record['error']}")
                return 1
        _log(handle, f"扫描完成，共 {len(points)} 点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
