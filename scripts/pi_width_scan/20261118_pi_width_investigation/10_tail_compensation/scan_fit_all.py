"""两轮共 39 组波形文件运行（τc = 25.5、35、45 µs）联合反拟合 s_f、τe；每轮用本轮基线归一化。输出 outputs/scan_fit_all.txt。"""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares, brentq
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc
from lab_workflows.experiment_modules.bperp_null import z_waveform as zw
r1 = json.loads((HERE / "outputs" / "scan_results.json").read_text(encoding="utf-8"))
r2 = json.loads((HERE / "outputs" / "scan2_results.json").read_text(encoding="utf-8"))
pts = [(v["w"], v["A"], 25.5064, v["ratio_meas"]) for k, v in r1.items() if k != "base"]
pts += [(v["w"], v["A"], v["tau_c"] if v["tau_c"] != 25.5 else 25.5064, v["ratio_meas"]) for k, v in r2.items() if k not in ("t2_base", "t2_ref")]
SC = {(w, A, t): zw.tail_compensated_waveform(200.0, w, 1.0, A, t, points=16384)[1] for w, A, t, _ in pts}
def j1(w, A, t, sf, te, ma=190.0):
    c0, jx, jy = tc.response(w, A, t, current_ma=ma, s_f=sf, tau_e=te); return np.linalg.norm([jx[0], jy[0]])
def resid(p):
    sf, te = p; b = j1(6.6, 0.0, 25.0, sf, te)
    return np.array([np.log(j1(w, A, t, sf, te, 190.0 / SC[(w, A, t)]) / b) - np.log(r) for w, A, t, r in pts])
lines = [f"{len(pts)} points"]
r0 = resid([0.544, 46.9]); lines.append(f"nominal: rms {np.sqrt(np.mean(r0**2)):.3f}")
fit = least_squares(resid, [0.595, 60.0], bounds=([0.3, 15], [0.95, 150]), diff_step=1e-2)
J = fit.jac; cov = np.linalg.pinv(J.T @ J) * np.sum(fit.fun ** 2) / max(1, len(fit.fun) - 2)
sf, te = fit.x
lines.append(f"joint fit: s_f={sf:.3f}±{np.sqrt(cov[0,0]):.3f}, tau_e={te:.1f}±{np.sqrt(cov[1,1]):.1f} us, corr={cov[0,1]/np.sqrt(cov[0,0]*cov[1,1]):+.2f}, rms {np.sqrt(np.mean(fit.fun**2)):.3f}")
w_pi = brentq(lambda w: tc.rotation(w, *tc.exact_tail(w, sf, te), s_f=sf, tau_e=te) - 1.0, 6.0, 10.0)
A = tc.exact_tail(w_pi, sf, te)[0]
lines.append(f"physically exact tail (field after pulse = 0) at 190 mA: w_pi={w_pi:.3f} us, A={A:.4f} ({190*A:.1f} mA), tau_c={sf*te:.2f} us")
(HERE / "outputs" / "scan_fit_all.txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); print("\n".join(lines))
