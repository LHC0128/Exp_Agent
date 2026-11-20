"""D 判据核对：Z 精确补偿后，各种横向形状下，X/Y 叠加 κ·x(t) 能否让 2f 零点处 c1 → 0，此时零点离 B⊥=0 多远。"""
import sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tailcomp as tc
from transverse import null2f
w = 7.930; A, tau_c = tc.exact_tail(w); r = (1 - tc.S_F) / tc.S_F
cases = {"coil tilt tc=0.02 x (fast only)": dict(tilt=(0.02, 0.0)),
         "eddy picture eps=0.03 x,y": dict(tilt=(0.03 * r, 0.03 * r), slow_tilt=(-0.03, -0.03)),
         "image picture ts=(0.025,0.033)": dict(slow_tilt=(0.025, 0.033)),
         "image + coil tilt 0.02 y": dict(slow_tilt=(0.025, 0.033), tilt=(0.0, 0.02)),
         "pump tilt 0.02 x": dict(pump=(0.02, 0.0, 1.0))}
lines = []
for lab, kw in cases.items():
    b0, c0 = null2f(w, A, tau_c, **kw)
    def f(k):
        b, c = null2f(w, A, tau_c, xy_comp=tuple(k), **kw); return [c[0].real * 1e3, c[0].imag * 1e3]
    sol = least_squares(f, x0=[0.0, 0.0], diff_step=1e-2, xtol=1e-10)
    b1, c1 = null2f(w, A, tau_c, xy_comp=tuple(sol.x), **kw)
    lines.append(f"{lab}: no XY comp -> 2f null ({b0[0]:+.2f},{b0[1]:+.2f}) nT |c1| {abs(c0[0]):.2e}; "
                 f"kappa*=({sol.x[0]:+.4f},{sol.x[1]:+.4f}) -> 2f null ({b1[0]:+.2f},{b1[1]:+.2f}) nT |c1| {abs(c1[0]):.1e}")
Path(__file__).with_name("outputs").joinpath("check_d.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
