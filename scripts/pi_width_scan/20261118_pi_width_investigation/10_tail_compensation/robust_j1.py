"""J1 判据的稳健性：τc 也作自由参数、工作点偏离零场、存在 slow_tilt、拖尾参数真值不同（s_f、τe ±15%）。"""
import sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tailcomp as tc
lines = []
def nj1(A, w, tau_c, **kw):
    c0, jx, jy = tc.response(w, A, tau_c, **kw); return np.linalg.norm([jx[0], jy[0]])
def opt(fun, x0):
    return minimize(fun, x0=x0, method="Nelder-Mead", options=dict(xatol=1e-4, fatol=1e-10, maxiter=400))
for lab, kw in [("baseline", {}),
                ("op. point B=(45,14) nT", dict(b0=(45.0, 14.0))),
                ("slow_tilt (0.025,0.033)", dict(slow_tilt=(0.025, 0.033))),
                ("true s_f 0.63 (+15%)", dict(s_f=0.63)),
                ("true tau_e 54 (+15%)", dict(tau_e=54.0)),
                ("true tau_e 40 (-15%)", dict(tau_e=40.0))]:
    sf = kw.get("s_f", tc.S_F); te = kw.get("tau_e", tc.TAU_E)
    A_ex, tc_ex = tc.exact_tail(7.93, sf, te)
    r2 = opt(lambda p: nj1(p[0], p[1], tc.S_F * tc.TAU_E, **kw), [0.12, 8.1])          # τc 固定在名义值
    r3 = opt(lambda p: nj1(p[0], p[1], p[2], **kw), [0.12, 8.1, 25.0])                  # τc 自由
    lines.append(f"{lab}: exact (w=7.93) A={A_ex:.4f} tau_c={tc_ex:.2f} | 2-knob min A={r2.x[0]:.4f} w={r2.x[1]:.3f} |J1|={r2.fun:.2e}"
                 f" | 3-knob min A={r3.x[0]:.4f} w={r3.x[1]:.3f} tau_c={r3.x[2]:.2f} |J1|={r3.fun:.2e}"
                 f" rot={tc.rotation(r3.x[1], r3.x[0], r3.x[2], s_f=sf, tau_e=te):.4f}pi")
    print(lines[-1], flush=True)
# 残余拖尾：J1 极小点处，脉冲后 z 场相对不补偿的大小
A_m, w_m = 0.1276, 8.225
_, z, _ = tc.fields(w_m, A_m, tc.S_F * tc.TAU_E); _, z0, _ = tc.fields(w_m)
m = (tc.TU > w_m + 0.2) & (tc.TU < 99)
lines.append(f"residual z after pulse at J1 min: max {np.abs(z[m]).max():.4f} vs uncomp {np.abs(z0[m]).max():.4f} "
             f"(area ratio {abs(np.trapezoid(z[m], tc.TU[m]))/abs(np.trapezoid(z0[m], tc.TU[m])):.3f})")
print(lines[-1])
Path(__file__).with_name("outputs").joinpath("robust_j1.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
