"""J1 极小点（A 偏大约 8%）上做 D 补偿后，2f 零点离真正 B⊥=0 的距离；与精确补偿点、不补偿对比。"""
import sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tailcomp as tc
from transverse import null2f
st = (0.025, 0.033); tau_c = tc.S_F * tc.TAU_E
lines = []
for lab, w, A in (("exact comp", 7.930, tc.exact_tail(7.930)[0]), ("J1 minimum", 8.225, 0.1276),
                  ("A -20% on pi line", None, 0.8 * tc.exact_tail(7.93)[0]), ("A +20% on pi line", None, 1.2 * tc.exact_tail(7.93)[0])):
    if w is None:
        from scipy.optimize import brentq
        w = brentq(lambda ww: tc.rotation(ww, A, tau_c) - 1.0, 6.0, 10.0)
    def f(k):
        b, c = null2f(w, A, tau_c, slow_tilt=st, xy_comp=tuple(k)); return [c[0].real * 1e3, c[0].imag * 1e3]
    sol = least_squares(f, x0=[-st[0], -st[1]], diff_step=1e-2, xtol=1e-10)
    b, c = null2f(w, A, tau_c, slow_tilt=st, xy_comp=tuple(sol.x))
    c0, jx, jy = tc.response(w, A, tau_c)
    lines.append(f"{lab}: w={w:.3f} A={A:.4f} ({190*A:.1f} mA) |J1|={np.linalg.norm([jx[0], jy[0]]):.2e} -> kappa*=({sol.x[0]:+.4f},{sol.x[1]:+.4f}), "
                 f"2f null after D at ({b[0]:+.2f},{b[1]:+.2f}) nT")
    print(lines[-1], flush=True)
Path(__file__).with_name("outputs").joinpath("check_bias.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
