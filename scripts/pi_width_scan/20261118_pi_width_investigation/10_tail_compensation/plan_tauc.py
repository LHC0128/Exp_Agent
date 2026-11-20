"""τc 扫描设计：固定 τc ∈ {25.5, 35, 45} µs，在两组屏蔽参数（名义 0.544/46.9；反拟合 0.595/60）下求 |J1| 的 (A, w) 极小。
文件的归一化（max|v|=1）按实际生成方式计入峰值电流。输出 outputs/plan_tauc.txt。"""
import sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc
from lab_workflows.experiment_modules.bperp_null import z_waveform as zw
def scale(w, A, t):
    return zw.tail_compensated_waveform(200.0, w, 1.0, A, t, points=16384)[1]
def j1(A, w, t, sf, te):
    c0, jx, jy = tc.response(w, A, t, current_ma=190.0 / scale(w, A, t), s_f=sf, tau_e=te); return np.linalg.norm([jx[0], jy[0]])
lines = []
for sf, te in ((0.544, 46.9), (0.595, 60.0)):
    base = j1(0.0, 6.6, 25.0, sf, te)
    for t in (25.5, 35.0, 45.0):
        r = minimize(lambda p: j1(p[0], p[1], t, sf, te), x0=[0.11, 8.2], method="Nelder-Mead", options=dict(xatol=2e-3, fatol=1e-9, maxiter=120))
        A, w = r.x
        lines.append(f"s_f={sf} tau_e={te}: tau_c={t}: min A={A:.3f} ({190*A:.1f} mA) w={w:.3f} us |J1|/base={r.fun/base:.3f} rot={tc.rotation(w, A, t, s_f=sf, tau_e=te):.3f}pi")
        print(lines[-1], flush=True)
(HERE / "outputs" / "plan_tauc.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
