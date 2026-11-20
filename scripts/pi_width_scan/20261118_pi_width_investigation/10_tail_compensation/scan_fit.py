"""用 20 组实测 |J1|/基线 反拟合屏蔽模型参数 s_f、τe（Γ、w0 取 rf_slow.json）；尾参数按实际上传的文件（τc 25.5）。
输出 outputs/scan_fit.txt。"""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc
res = json.loads((HERE / "outputs" / "scan_results.json").read_text(encoding="utf-8"))
pts = [(v["w"], v["A"], v["scale"], v["ratio_meas"]) for k, v in res.items() if k != "base"]
TC = 25.5064
def j1(w, A, scale, sf, te):
    c0, jx, jy = tc.response(w, A, TC, current_ma=190.0 / scale, s_f=sf, tau_e=te); return np.linalg.norm([jx[0], jy[0]])
def resid(p):
    sf, te = p
    b = j1(6.6, 0.0, 1.0, sf, te)
    return np.array([np.log(j1(w, A, s, sf, te) / b) - np.log(r) for w, A, s, r in pts])
lines = []
r0 = resid([tc.S_F, tc.TAU_E]); lines.append(f"nominal s_f={tc.S_F:.3f} tau_e={tc.TAU_E:.1f}: rms log-resid {np.sqrt(np.mean(r0**2)):.3f}")
fit = least_squares(resid, [tc.S_F, tc.TAU_E], bounds=([0.3, 15], [0.95, 150]), diff_step=1e-2)
J = fit.jac; cov = np.linalg.pinv(J.T @ J) * np.mean(fit.fun ** 2) * len(fit.fun) / max(1, len(fit.fun) - 2)
sf, te = fit.x
lines.append(f"fit s_f={sf:.3f}±{np.sqrt(cov[0,0]):.3f}, tau_e={te:.1f}±{np.sqrt(cov[1,1]):.1f} us: rms log-resid {np.sqrt(np.mean(fit.fun**2)):.3f}")
A_ex, tc_ex = tc.exact_tail(7.93, sf, te)
from scipy.optimize import brentq
w_pi = brentq(lambda w: tc.rotation(w, *tc.exact_tail(w, sf, te), s_f=sf, tau_e=te) - 1.0, 6.0, 10.0)
lines.append(f"with fitted params: exact tail at w_pi={w_pi:.3f} us: A={tc.exact_tail(w_pi, sf, te)[0]:.4f} ({190*tc.exact_tail(w_pi, sf, te)[0]:.1f} mA), tau_c={sf*te:.2f} us")
(HERE / "outputs" / "scan_fit.txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); print("\n".join(lines))
