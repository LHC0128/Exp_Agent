"""相位 × 脉宽网格：每个脉宽用多个射频相位解 Z(φ) = c0 + c+1 e^{iφ} + c-1 e^{-iφ}。

用法：python grid_analysis.py <输出目录> <标签> <run_dir:phase> [<run_dir:phase> ...]
同一相位出现两次时，第一次参与分解，最后一次只用于漂移检查。
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load(run_dir):
    groups = defaultdict(list)
    with open(Path(run_dir) / "raw" / "scan.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups[float(row["width_us"])].append(
                float(row["R_v"]) * np.exp(1j * np.radians(float(row["theta_deg"]))))
    w = np.array(sorted(groups))
    return w, np.array([np.mean(groups[k]) for k in w]) * 1e3


def main():
    out, tag = Path(sys.argv[1]), sys.argv[2]
    specs = [s.rsplit(":", 1) for s in sys.argv[3:]]
    data, seen, drift = {}, set(), None
    for run, ph in specs:
        ph = float(ph)
        w, z = load(run)
        if ph in seen:
            drift = (ph, w, z)
            continue
        seen.add(ph)
        data[ph] = (w, z)
    phases = sorted(data)
    w = data[phases[0]][0]
    for ph in phases:
        assert np.allclose(data[ph][0], w), "脉宽网格不一致"
    Z = np.stack([data[ph][1] for ph in phases], 1)            # (nw, nphi)
    ph_rad = np.radians(phases)
    A = np.stack([np.ones_like(ph_rad), np.exp(1j * ph_rad), np.exp(-1j * ph_rad)], 1)
    coef, *_ = np.linalg.lstsq(A, Z.T, rcond=None)              # (3, nw)
    c0, cp, cm = coef
    resid = np.sqrt(np.mean(abs(A @ coef - Z.T) ** 2, axis=0))

    lines = [f"# {tag}: phases {phases}", " w     |c0|  argc0   |c+1|  argc+1   |c-1|  argc-1  resid  (mV, deg)"]
    for k in range(len(w)):
        lines.append(f"{w[k]:5.2f} {abs(c0[k]):6.2f} {np.degrees(np.angle(c0[k])):7.1f} "
                     f"{abs(cp[k]):7.2f} {np.degrees(np.angle(cp[k])):7.1f} "
                     f"{abs(cm[k]):7.2f} {np.degrees(np.angle(cm[k])):7.1f} {resid[k]:6.2f}")
    if drift is not None:
        ph, wd, zd = drift
        first = data[ph][1]
        rel = abs(zd - first) / np.maximum(abs(first), 1e-9)
        lines.append(f"# drift check phase {ph}: median |dZ|/|Z| = {np.median(rel):.3f}, "
                     f"median |dZ| = {np.median(abs(zd - first)):.2f} mV, "
                     f"median d(arg) = {np.degrees(np.median(np.angle(zd / first))):.2f} deg")
    text = "\n".join(lines)
    print(text)
    (out / f"grid_{tag}.txt").write_text(text, encoding="utf-8")
    np.savez(out / f"grid_{tag}.npz", w=w, c0=c0, cp=cp, cm=cm, resid=resid, phases=phases, Z=Z)

    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    for c, lab, col in ((c0, "c0 (RF-independent, S)", "C3"), (cp, "c+1 (co-rotating)", "C0"),
                        (cm, "c-1 (counter-rotating, pulse-only)", "k")):
        ax[0, 0].plot(w, abs(c), "o-", ms=3, color=col, label=lab)
        ax[0, 1].plot(w, np.degrees(np.unwrap(np.angle(c))), "o-", ms=3, color=col, label=lab)
    ax[0, 0].plot(w, resid, ":", color="gray", label="fit residual")
    ax[0, 0].set_ylabel("magnitude (mV)"); ax[0, 1].set_ylabel("phase (deg, unwrapped)")
    for a in ax[0]:
        a.set_xlabel("Pulse width (us)"); a.legend(fontsize=8); a.axvline(5.122, color="gray", ls="--", lw=0.8)
    for c, lab, col in ((c0, "c0", "C3"), (cm, "c-1", "k")):
        ax[1, 0].plot(c.real, c.imag, ".-", color=col, label=lab)
        for k in range(0, len(w), 4):
            ax[1, 0].annotate(f"{w[k]:g}", (c.real[k], c.imag[k]), fontsize=7, color=col)
    ax[1, 0].plot(0, 0, "b+", ms=12); ax[1, 0].set_aspect("equal"); ax[1, 0].legend(fontsize=8)
    ax[1, 0].set_xlabel("Re (mV)"); ax[1, 0].set_ylabel("Im (mV)")
    for j, ph in enumerate(phases):
        ax[1, 1].plot(w, abs(Z[:, j]), "o-", ms=3, label=f"|Z| phase {ph:g} deg")
    ax[1, 1].set_xlabel("Pulse width (us)"); ax[1, 1].set_ylabel("mV"); ax[1, 1].legend(fontsize=8)
    fig.suptitle(tag)
    fig.tight_layout()
    fig.savefig(out / f"grid_{tag}.png", dpi=130)


if __name__ == "__main__":
    main()
