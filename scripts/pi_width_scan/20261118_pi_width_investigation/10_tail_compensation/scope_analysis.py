"""示波器 Px 快照（三种 Z 波形 × 两个 X 偏置）的折叠与奇偶分解。

输入：scope_px_snapshot/<时间>_tail_comp/<label>/waveforms.npz（CH1 = Px，CH2 = 10 Ω 采样电阻电压）。
- 折叠：CH2 向上穿过 +1 V（+100 mA）处为 t = 0，按 200 µs 周期、0.1 µs 分箱平均。
- 静态 Bx 响应 d(t) = Px(X+40 mV) − Px(X)。理想 π 脉冲下 d 的两个半周期相同；
  奇部 [d(t) − d(t+100 µs)]/2 即时域版的 J1。
输出：outputs/scope_px_summary.txt、outputs/scope_px_folded.npz、figures/fig7_scope_px.png。
用法：python scope_analysis.py <快照根目录>
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
PERIOD, BIN = 200.0, 0.1
WAVES = [("builtin66", "built-in 6.6 us (no tail comp.)"), ("cand1_w8.20_tc25.5", "cand. 1: 8.20 us, 21.6 mA, tau_c 25.5 us"),
         ("cand2_w7.50_tc35.6", "cand. 2: 7.50 us, 13.2 mA, tau_c 35.6 us")]


def fold(path):
    d = np.load(path)
    t1, v1, t2, v2 = d["t1"] * 1e6, d["v1"] * 1e3, d["t2"] * 1e6, d["v2"]
    up = np.where((v2[:-1] < 1.0) & (v2[1:] >= 1.0))[0]
    edges = t2[up] + (1.0 - v2[up]) / (v2[up + 1] - v2[up]) * (t2[up + 1] - t2[up])
    edges = edges[np.concatenate([[True], np.diff(edges) > PERIOD * 0.6])]
    nb = int(round(PERIOD / BIN)); tb = (np.arange(nb) + 0.5) * BIN - 2.0
    acc1, acc2, n = np.zeros(nb), np.zeros(nb), 0
    for e in edges:
        if e - 2.0 < t1[0] or e - 2.0 + PERIOD > t1[-1]:
            continue
        acc1 += np.interp(e + tb, t1, v1); acc2 += np.interp(e + tb, t2, v2) * 100.0   # mA（10 Ω）
        n += 1
    if n == 0:
        raise RuntimeError(f"{path}: 没有完整周期")
    return tb, acc1 / n, acc2 / n, n


def halves(x, tb):
    h = int(round(PERIOD / 2 / BIN))
    odd = (x[:h] - x[h:2 * h]) / 2; even = (x[:h] + x[h:2 * h]) / 2
    return tb[:h], odd, even


def main(root):
    root = Path(root)
    data, lines = {}, []
    for w, _ in WAVES:
        for b in ("b0", "bx40"):
            p = root / f"{w}_{b}" / "waveforms.npz"
            if p.exists():
                data[(w, b)] = fold(p)
    save = {}
    fig, ax = plt.subplots(3, 1, figsize=(10, 10.5), sharex=False)
    for k, (w, title) in enumerate(WAVES):
        if (w, "b0") not in data:
            continue
        tb, p0, i0, n0 = data[(w, "b0")]
        ax[0].plot(tb, p0 - p0.mean(), color=f"C{k}", label=f"{title} (n={n0})")
        save[f"{w}_t"], save[f"{w}_px_b0"], save[f"{w}_i"] = tb, p0, i0
        line = f"{w}: Px p2p at 2f-null bias {np.ptp(p0):.1f} mV"
        if (w, "bx40") in data:
            _, p1, _, n1 = data[(w, "bx40")]
            dlt = p1 - p0; th, odd, even = halves(dlt, tb)
            save[f"{w}_px_bx40"] = p1
            ax[1].plot(tb, dlt, color=f"C{k}", label=title)
            ax[2].plot(th, odd, color=f"C{k}", label=f"{title}: odd rms {np.sqrt(np.mean(odd**2)):.2f} mV")
            ax[2].plot(th, even, color=f"C{k}", ls=":", lw=0.9)
            line += (f"; static-Bx response (40 mV): even rms {np.sqrt(np.mean(even**2)):.2f} mV, odd rms {np.sqrt(np.mean(odd**2)):.2f} mV, "
                     f"odd/even {np.sqrt(np.mean(odd**2)) / np.sqrt(np.mean(even**2)):.3f}")
        lines.append(line)
    ax[0].set_ylabel("Px - mean (mV)"); ax[0].set_title("Px at the 2f-null bias (89.1, -15.1) mV, folded on the +pulse", fontsize=9)
    ax[1].set_ylabel("Px(X+40 mV) - Px(X) (mV)"); ax[1].set_title("static-Bx response", fontsize=9)
    ax[2].set_ylabel("mV"); ax[2].set_xlabel("time since +pulse (us)")
    ax[2].set_title("half-period odd part (solid) and even part (dotted) of the static-Bx response; odd = time-domain J1", fontsize=9)
    for a in ax:
        a.axhline(0, color="k", lw=0.4); a.legend(fontsize=7)
    ax[0].set_xlim(-2, 198); ax[1].set_xlim(-2, 198); ax[2].set_xlim(-2, 98)
    fig.tight_layout(); fig.savefig(HERE / "figures" / "fig7_scope_px.png", dpi=130); plt.close(fig)
    np.savez(HERE / "outputs" / "scope_px_folded.npz", **save)
    lines.insert(0, f"snapshot root: {root}")
    (HERE / "outputs" / "scope_px_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
