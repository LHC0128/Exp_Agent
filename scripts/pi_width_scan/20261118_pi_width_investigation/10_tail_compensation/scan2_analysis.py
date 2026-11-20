"""第二轮 τc 扫描（τc = 35、45 µs 各 3×3，外加基线与第一轮最好点）的离线分析与两组模型参数对照。

输入：本目录 lab_client_out/out2_<label>.json；只读运行目录 raw/。
输出：outputs/scan2_results.{json,txt}、figures/fig5_tauc_scan.png。
"""
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
from scan_analysis import fit  # noqa: E402
from lab_workflows.experiment_modules.bperp_null import z_waveform as zw  # noqa: E402

CACHE = (Path(__file__).resolve().parent / "lab_client_out")
PARAMS = {"nominal (0.544, 46.9 us)": (0.544, 46.9), "refit (0.595, 60 us)": (0.595, 60.0)}


def run_dir(label):
    text = (CACHE / f"out2_{label}.json").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"run_dir":\s*"([^"]+)"', text)
    return Path(m.group(1).replace("\\\\", "\\")) if m else None


def model_j1(w, A, t, sf, te):
    scale = zw.tail_compensated_waveform(200.0, w, 1.0, A, t, points=16384)[1] if A else 1.0
    c0, jx, jy = tc.response(w, A, t, current_ma=190.0 / scale, s_f=sf, tau_e=te)
    return float(np.linalg.norm([jx[0], jy[0]]))


def main():
    grid = {35.0: ((7.55, 7.80, 8.05), (0.060, 0.085, 0.110)), 45.0: ((7.35, 7.60, 7.85), (0.050, 0.070, 0.090))}
    labels = {"t2_base": (6.6, 0.0, 25.5), "t2_ref": (8.2, 0.114, 25.5)}
    for t, (ws, As) in grid.items():
        for w in ws:
            for a in As:
                labels[f"t2_tc{t:.1f}_w{w:.2f}_A{a:.3f}"] = (w, a, t)
    res = {}
    for lab, (w, a, t) in labels.items():
        if not (CACHE / f"out2_{lab}.json").exists():
            continue
        d = run_dir(lab)
        if d is None or not (d / "raw" / "bias_grid.csv").exists():
            continue
        r = fit(d / "raw" / "bias_grid.csv"); r.update(run_dir=str(d), w=w, A=a, tau_c=t)
        for name, (sf, te) in PARAMS.items():
            r[f"model_{name}"] = model_j1(w, a, t, sf, te)
        res[lab] = r
    base = res["t2_base"]
    lines = [f"{'label':28s} w     A     tau_c  |J1|meas  ratio  " + "  ".join(f"{n[:7]}" for n in PARAMS) + "   J2 weak"]
    for lab, r in res.items():
        r["ratio_meas"] = r["J1"] / base["J1"]
        for name in PARAMS:
            r[f"ratio_{name}"] = r[f"model_{name}"] / base[f"model_{name}"]
        lines.append(f"{lab:28s} {r['w']:4.2f} {r['A']:5.3f} {r['tau_c']:5.1f}  {r['J1']:.4f}   {r['ratio_meas']:5.3f}  "
                     + "  ".join(f"{r[f'ratio_{n}']:7.3f}" for n in PARAMS) + f"   {r['J2_sv'][1]:.3f}")
    for name in PARAMS:
        lr = [np.log(r["ratio_meas"] / r[f"ratio_{name}"]) for k, r in res.items() if k != "t2_base"]
        lines.append(f"rms log(meas/model) for {name}: {np.sqrt(np.mean(np.square(lr))):.3f}")
    (HERE / "outputs" / "scan2_results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "outputs" / "scan2_results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(lines))

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for k, (t, (ws, As)) in enumerate(grid.items()):
        for i, a in enumerate(As):
            m = [res.get(f"t2_tc{t:.1f}_w{w:.2f}_A{a:.3f}") for w in ws]
            xs = [w for w, v in zip(ws, m) if v]; ys = [v["ratio_meas"] for v in m if v]
            ax[k].plot(xs, ys, "o-", color=f"C{i}", label=f"meas, tail {190*a:.1f} mA")
            for ls, name in zip(("--", ":"), PARAMS):
                ax[k].plot(xs, [v[f"ratio_{name}"] for v in m if v], ls, color=f"C{i}", lw=1,
                           label=f"model {name}" if i == 0 else None)
        ref = res.get("t2_ref")
        if ref:
            ax[k].axhline(ref["ratio_meas"], color="k", lw=0.8, ls="-.", label=f"ref tau_c 25.5 (8.20 us, 21.6 mA): {ref['ratio_meas']:.2f}")
        ax[k].set_title(f"tau_c = {t:g} us", fontsize=10); ax[k].set_xlabel("pulse width w (us)")
        ax[k].legend(fontsize=7)
    ax[0].set_ylabel("|J1| / baseline (6.6 us, no tail)")
    fig.suptitle("Tail time-constant scan: 1f slope criterion, measured vs two shield-parameter sets", fontsize=10)
    fig.tight_layout(); fig.savefig(HERE / "figures" / "fig5_tauc_scan.png", dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
