"""横向拖尾分量：形状判别（示波器 Px）与 X/Y 补偿判据（2f 零点处 c1 → 0）的模型验证。

1. 示波器折叠 Px（scope_px_snapshot/1118_135547，偏置在 2f 零点）线性拟合三种横向形状：
   A：横向场 ∝ 慢分量 (1−s_f)·x（屏蔽筒滞后磁化 / 06–08 模型的 slow_tilt）
   B：横向场 ∝ 涡流 (1−s_f)·(I − x)（= tilt·(1−s_f)/s_f 与 slow_tilt·(−1) 的组合）
   A+tilt：两者都放开
2. 精确 Z 补偿（7.93 µs、A=0.114）下，给定 slow_tilt，扫 X/Y 补偿系数 κ：2f 零点位置与该处 c1。
输出 outputs/transverse.txt、outputs/transverse.npz。
"""
from lab_workflows.common import data_root
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

OUT = HERE / "outputs"
SCOPE = (data_root() / "scope_px_snapshot/1118_135547/results/folded.npz")
lines = []


def scope_fit():
    D = np.load(SCOPE); t_d, y = D["t_us"], D["ch1"] * 1e3; y = y - y.mean()
    ts = (t_d + 0.505) % tc.PERIOD          # 与 fit_model.py 相同的对时（模型半高点 0.505 µs）
    e = 0.01
    def b(**kw):
        bx = kw.pop("bx", 0.0); by = kw.pop("by", 0.0)
        t, p = tc.simulate(bx, by, 6.6, **kw); v = np.interp(ts, t, p, period=tc.PERIOD); return v - v.mean()
    basis = {"bx": b(bx=tc.NT), "by": b(by=tc.NT), "stx": b(slow_tilt=(e, 0)), "sty": b(slow_tilt=(0, e)),
             "tx": b(tilt=(e, 0)), "ty": b(tilt=(0, e))}
    r = (1 - tc.S_F) / tc.S_F
    basis["eddyx"] = r * basis["tx"] - basis["stx"]; basis["eddyy"] = r * basis["ty"] - basis["sty"]
    sets = {"A: static + slow_tilt": ["bx", "by", "stx", "sty"],
            "B: static + eddy (I-x)": ["bx", "by", "eddyx", "eddyy"],
            "A+tilt free": ["bx", "by", "stx", "sty", "tx", "ty"]}
    out = {}
    for lab, names in sets.items():
        M = np.array([basis[n] for n in names]).T
        c, *_ = np.linalg.lstsq(M, y, rcond=None)
        res = y - M @ c
        out[lab] = (dict(zip(names, c)), 1 - res.var() / y.var(), np.sqrt(np.mean(res ** 2)))
        lines.append(f"scope fit {lab}: R2 = {out[lab][1]:.4f}, rms = {out[lab][2]:.2f} mV, coef = "
                     + ", ".join(f"{k}={v:.0f}" for k, v in out[lab][0].items()))
    c = out["A+tilt free"][0]
    lines.append(f"  A+tilt: tilt/slow_tilt ratio x {c['tx']/c['stx']:+.3f}, y {c['ty']/c['sty']:+.3f} "
                 f"(picture A -> 0, picture B -> {-r:+.3f})")
    return out


def null2f(w, A, tau_c, b_start=(0.0, 0.0), **kw):
    """Newton 求 c2(bx,by)=0（nT）。"""
    b = np.array(b_start, float)
    for _ in range(8):
        c0, jx, jy = tc.response(w, A, tau_c, b0=tuple(b), d_nt=1.0, **kw)
        Jr = np.array([[jx[1].real, jy[1].real], [jx[1].imag, jy[1].imag]])
        step = np.linalg.solve(Jr, -np.array([c0[1].real, c0[1].imag]))
        b += step
        if np.hypot(*step) < 1e-4:
            break
    c0, jx, jy = tc.response(w, A, tau_c, b0=tuple(b), d_nt=1.0, **kw)
    return b, c0


def comp_check():
    w = 7.930; A, tau_c = tc.exact_tail(w)
    st = (0.025, 0.033)             # 示波器拟合量级（G≈0.9–1 V/P 时 2.3–3.3%）
    rows = []
    for lab, kw in [("uncompensated 6.6 us, slow_tilt", dict(w=6.6, A=0.0, slow_tilt=st)),
                    ("Z comp, slow_tilt, no XY comp", dict(w=w, A=A, slow_tilt=st)),
                    ("Z comp, slow_tilt, XY comp = -slow_tilt", dict(w=w, A=A, slow_tilt=st, xy_comp=(-st[0], -st[1]))),
                    ("Z comp, slow_tilt, XY comp half", dict(w=w, A=A, slow_tilt=st, xy_comp=(-st[0] / 2, -st[1] / 2))),
                    ("Z comp, tilt 0.02 x (no slow_tilt)", dict(w=w, A=A, tilt=(0.02, 0.0))),
                    ("Z comp, pump 0.02 x (no slow_tilt)", dict(w=w, A=A, pump=(0.02, 0.0, 1.0))),
                    ("Z comp, slow_tilt + tilt 0.02 x, XY comp = -slow_tilt", dict(w=w, A=A, slow_tilt=st, tilt=(0.02, 0.0), xy_comp=(-st[0], -st[1])))]:
        kw = dict(kw); ww = kw.pop("w"); AA = kw.pop("A")
        b, c0 = null2f(ww, AA, tau_c, **kw)
        rows.append((lab, b, c0[0]))
        lines.append(f"{lab}: 2f null at ({b[0]:+.2f}, {b[1]:+.2f}) nT, |c1| there = {abs(c0[0]):.3e} P")
    # 唯一性：在 κ 平面上 |c1(2f 零点)| 的分布
    ks = np.linspace(-0.05, 0.01, 7); kys = np.linspace(-0.06, 0.0, 7)
    grid = np.zeros((len(ks), len(kys))); nulls = np.zeros((len(ks), len(kys), 2))
    for i, kx in enumerate(ks):
        for j, ky in enumerate(kys):
            b, c0 = null2f(w, A, tau_c, slow_tilt=st, xy_comp=(kx, ky))
            grid[i, j] = abs(c0[0]); nulls[i, j] = b
    i, j = np.unravel_index(np.argmin(grid), grid.shape)
    lines.append(f"kappa scan: min |c1 at 2f null| = {grid[i,j]:.3e} at kappa = ({ks[i]:+.3f}, {kys[j]:+.3f}); "
                 f"true -slow_tilt = ({-st[0]:+.3f}, {-st[1]:+.3f})")
    np.savez(OUT / "transverse.npz", ks=ks, kys=kys, grid=grid, nulls=nulls, st=st)


if __name__ == "__main__":
    scope_fit()
    comp_check()
    (OUT / "transverse.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
