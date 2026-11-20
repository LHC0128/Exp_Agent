"""上机 (A, w) 扫描的离线分析：每次 bperp_null 运行的 1f/2f 复平面斜率、2f 零点与零点处 c1，并与模型对照。

输入：本目录 lab_client_out/out_<label>.json（lab_client 输出，含运行目录）；只读运行目录 raw/。
输出：outputs/scan_results.json、outputs/scan_results.txt、figures/fig4_scan_j1.png。
斜率单位 mV/mV（锁相 rms 读数 / 偏置电压）；模型 |J1| 以 P/nT 计，只比较相对基线的比值。
"""
from lab_workflows.common import data_root
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

CACHE = (Path(__file__).resolve().parent / "lab_client_out")
WAVES = (data_root() / "z_waveforms")


def run_dir(label):
    text = (CACHE / f"out_{label}.json").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"run_dir":\s*"([^"]+)"', text) or re.search(r'(D:[\\/]+Code[\\/]+exp_agent_run[\\/]+data[\\/]+bperp_null[\\/]+[0-9_]+bperp_null)', text)
    return Path(m.group(1).replace("\\\\", "\\")) if m else None


def fit(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    pts = {}
    for r in rows:
        key = (float(r["x_bias_v"]) * 1e3, float(r["y_bias_v"]) * 1e3)
        pts.setdefault(key, []).append((complex(float(r["x1_v"]), float(r["y1_v"])), complex(float(r["x2_v"]), float(r["y2_v"]))))
    X = np.array([[1, x, y] for (x, y) in pts]); c = np.array([np.mean(v, axis=0) for v in pts.values()]) * 1e3   # mV
    rep = np.array([np.std(v, axis=0) for v in pts.values()]).mean(0) * 1e3
    out = {}
    for n, name in ((0, "c1"), (1, "c2")):
        coef, *_ = np.linalg.lstsq(X.astype(complex), c[:, n], rcond=None)
        out[name] = coef                     # a (mV), slope x, slope y (mV/mV)
        out[name + "_res_mV"] = float(np.sqrt(np.mean(np.abs(X @ coef - c[:, n]) ** 2)))
        out[name + "_rep_mV"] = float(rep[n])
    a2, bx2, by2 = out["c2"]
    M = np.array([[bx2.real, by2.real], [bx2.imag, by2.imag]])
    null = np.linalg.solve(M, -np.array([a2.real, a2.imag]))
    a1, bx1, by1 = out["c1"]
    c1_null = a1 + bx1 * null[0] + by1 * null[1]
    return dict(J1=float(np.linalg.norm([bx1, by1])), J1x=[bx1.real, bx1.imag], J1y=[by1.real, by1.imag],
                J2_sv=np.linalg.svd(M, compute_uv=False).tolist(), null_2f_mV=null.tolist(),
                c1_at_null_mV=[c1_null.real, c1_null.imag], fit_res_mV=[out["c1_res_mV"], out["c2_res_mV"]],
                repeat_std_mV=[out["c1_rep_mV"], out["c2_rep_mV"]], n_points=len(pts))


def model_j1(w, A, scale):
    c0, jx, jy = tc.response(w, A, tc.S_F * tc.TAU_E, current_ma=190.0 / scale)
    return float(np.linalg.norm([jx[0], jy[0]]))


def main():
    labels = ["base"] + [f"w{w}_A{a}" for w in ("7.40", "7.70", "7.93", "8.20", "8.50") for a in ("0.000", "0.060", "0.114", "0.160")]
    res = {}
    for lab in labels:
        d = run_dir(lab) if (CACHE / f"out_{lab}.json").exists() else None
        if d is None or not (d / "raw" / "bias_grid.csv").exists():
            continue
        r = fit(d / "raw" / "bias_grid.csv"); r["run_dir"] = str(d)
        if lab == "base":
            r.update(w=6.6, A=0.0, scale=1.0)
        else:
            w, a = float(lab[1:5]), float(lab.split("_A")[1])
            head = dict(l[2:].split(": ", 1) for l in (WAVES / f"z_tail_w{lab[1:5]}_A{lab.split('_A')[1]}_tc25.5.csv").read_text(encoding="utf-8").splitlines() if l.startswith("# "))
            r.update(w=w, A=a, scale=float(head["normalization_scale"]))
        r["model_J1"] = model_j1(r["w"], r["A"], r["scale"])
        r["model_rot_pi"] = tc.rotation(r["w"], r["A"], tc.S_F * tc.TAU_E, current_ma=190.0 / r["scale"])
        res[lab] = r
    base = res.get("base")
    lines = ["label     w     A      rot(model)  |J1| meas (mV/mV)  ratio  model ratio   2f null (mV)        c1@null (mV)       J2 sv"]
    for lab, r in res.items():
        rm = r["J1"] / base["J1"] if base else float("nan"); mm = r["model_J1"] / base["model_J1"] if base else float("nan")
        r["ratio_meas"], r["ratio_model"] = rm, mm
        lines.append(f"{lab:14s} {r['w']:5.2f} {r['A']:5.3f}  {r['model_rot_pi']:6.3f}pi   {r['J1']:.4f}   {rm:6.3f}  {mm:6.3f}   "
                     f"({r['null_2f_mV'][0]:+7.2f},{r['null_2f_mV'][1]:+7.2f})  ({r['c1_at_null_mV'][0]:+6.2f},{r['c1_at_null_mV'][1]:+6.2f})  "
                     f"{r['J2_sv'][0]:.3f}/{r['J2_sv'][1]:.3f}")
    (HERE / "outputs" / "scan_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "scan_results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))
    scans = {k: v for k, v in res.items() if k != "base"}
    if not scans or base is None:
        return
    ws = sorted({v["w"] for v in scans.values()}); As = sorted({v["A"] for v in scans.values()})
    meas = np.full((len(As), len(ws)), np.nan); mod = np.full_like(meas, np.nan)
    for v in scans.values():
        i, j = As.index(v["A"]), ws.index(v["w"]); meas[i, j] = v["ratio_meas"]; mod[i, j] = v["ratio_model"]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
    for k, (M, title) in enumerate(((meas, "measured |J1| / baseline (6.6 us, no tail)"), (mod, "model |J1| / model baseline"))):
        im = ax[k].pcolormesh(ws, 190 * np.array(As), M, shading="nearest", cmap="viridis", vmin=0, vmax=max(1.2, np.nanmax(meas)))
        fig.colorbar(im, ax=ax[k]); ax[k].set_title(title, fontsize=9)
        ax[k].set_xlabel("pulse width w (us)"); ax[k].set_ylabel("tail amplitude (mA)")
        for i in range(len(As)):
            for j in range(len(ws)):
                ax[k].text(ws[j], 190 * As[i], f"{M[i, j]:.2f}", ha="center", va="center", fontsize=7, color="w")
    for i, A in enumerate(As):
        ax[2].plot(ws, meas[i], "o-", color=f"C{i}", label=f"meas, tail {190*A:.0f} mA")
        ax[2].plot(ws, mod[i], "--", color=f"C{i}", lw=1)
    ax[2].axhline(1, color="k", lw=0.5); ax[2].set_xlabel("pulse width w (us)"); ax[2].set_ylabel("|J1| / baseline")
    ax[2].set_title("solid: measured, dashed: model (tau_c = 25.5 us)", fontsize=9); ax[2].legend(fontsize=7)
    fig.tight_layout(); fig.savefig(HERE / "figures" / "fig4_scan_j1.png", dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
