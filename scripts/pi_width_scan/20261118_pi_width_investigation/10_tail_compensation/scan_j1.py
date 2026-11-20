"""判据验证：1f 对横场的斜率 J1 在 (尾幅 A, 脉宽 w) 平面上的分布，检查极小是否唯一、是否落在精确补偿点。

输出 outputs/j1_map.npz、outputs/j1_map.txt；图由 make_figs.py 画。
J1 = [∂c1/∂Bx, ∂c1/∂By]（复数，P 单位/nT），取 Frobenius 范数；同时记录 2f 雅可比 J2 的奇异值。
"""
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tailcomp as tc  # noqa: E402

OUT = HERE / "outputs"; OUT.mkdir(exist_ok=True)


def metrics(w, A, tau_c=None, **kw):
    tau_c = tc.S_F * tc.TAU_E if tau_c is None else tau_c
    c0, jx, jy = tc.response(w, A, tau_c, **kw)
    j1 = np.array([jx[0], jy[0]]); j2 = np.array([jx[1], jy[1]])
    r2 = np.array([[j2[0].real, j2[1].real], [j2[0].imag, j2[1].imag]])
    return np.linalg.norm(j1), np.linalg.svd(r2, compute_uv=False), c0


def main():
    lines = []
    w_pi = brentq(lambda w: tc.rotation(w, *tc.exact_tail(w)) - 1.0, 6.0, 10.0)
    A_pi, tau_c = tc.exact_tail(w_pi)
    lines.append(f"exact compensation at 190 mA: w_pi = {w_pi:.3f} us, A = {A_pi:.4f} (= {190*A_pi:.1f} mA), tau_c = {tau_c:.2f} us")
    n0, s0, _ = metrics(6.6, 0.0)
    n1, s1, _ = metrics(w_pi, A_pi)
    lines.append(f"|J1| uncompensated 6.6 us: {n0:.3e}  J2 sv {s0}")
    lines.append(f"|J1| exact comp {w_pi:.2f} us: {n1:.3e}  J2 sv {s1}")
    # 无拖尾理想（s_f=1）矩形 π：有限脉宽的下限
    w_ideal = brentq(lambda w: tc.rotation(w, s_f=1.0) - 1.0, 3.0, 9.0)
    n2, s2, _ = metrics(w_ideal, 0.0, s_f=1.0)
    lines.append(f"|J1| no-tail physics (s_f=1) at its pi width {w_ideal:.3f} us: {n2:.3e}  J2 sv {s2}")

    ws = np.linspace(6.6, 9.4, 15); As = np.linspace(0.0, 0.22, 12)
    t0 = time.time()
    J = np.zeros((len(As), len(ws))); ROT = np.zeros_like(J)
    for a, A in enumerate(As):
        for b, w in enumerate(ws):
            J[a, b] = metrics(w, A)[0]
            ROT[a, b] = tc.rotation(w, A, tau_c)
    lines.append(f"map {J.shape} in {time.time()-t0:.0f}s")
    a, b = np.unravel_index(np.argmin(J), J.shape)
    lines.append(f"grid min |J1| = {J[a,b]:.3e} at A = {As[a]:.3f}, w = {ws[b]:.2f}")
    # 局部精确极小
    res = minimize(lambda p: metrics(p[1], p[0])[0], x0=[As[a], ws[b]], method="Nelder-Mead",
                   options=dict(xatol=1e-4, fatol=1e-9, maxiter=200))
    A_m, w_m = res.x
    lines.append(f"refined min |J1| = {res.fun:.3e} at A = {A_m:.4f}, w = {w_m:.3f}; "
                 f"rotation there = {tc.rotation(w_m, A_m, tau_c):.4f} pi; exact tail for that w: A = {tc.exact_tail(w_m)[0]:.4f}")
    # 多起点检查唯一性
    starts = [(0.02, 7.0), (0.2, 7.0), (0.02, 9.2), (0.2, 9.2), (0.1, 8.0)]
    for s in starts:
        r = minimize(lambda p: metrics(p[1], p[0])[0], x0=list(s), method="Nelder-Mead",
                     options=dict(xatol=1e-3, fatol=1e-9, maxiter=150))
        lines.append(f"  start A={s[0]}, w={s[1]} -> A={r.x[0]:.4f}, w={r.x[1]:.3f}, |J1|={r.fun:.3e}")
    np.savez(OUT / "j1_map.npz", ws=ws, As=As, J=J, ROT=ROT, w_pi=w_pi, A_pi=A_pi, A_m=A_m, w_m=w_m,
             n_uncomp=n0, n_exact=n1, n_ideal=n2)
    (OUT / "j1_map.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
