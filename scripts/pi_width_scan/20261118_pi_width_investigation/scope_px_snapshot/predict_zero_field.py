"""用 fit_model.py 的“静态 B + slow_tilt”系数，预测把静态 B 补到 0 后示波器上的 Px（只剩 slow_tilt 项）。"""
from lab_workflows.common import data_root
import json, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = Path(__file__).resolve().parent
RUN = data_root() / "scope_px_snapshot/1118_135547"  # 采集运行目录
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "08_even_2f_criterion"))
import harmmodel as hm  # noqa: E402
D = np.load(RUN / "results" / "folded.npz"); tc, y = D["t_us"], D["ch1"] * 1e3
c = json.loads((RUN / "results" / "fit_summary.json").read_text(encoding="utf-8"))["static B + slow_tilt"]["coef_mV"]
ts = (tc + 0.505) % hm.PERIOD; NT = hm.GAMMA_HZ_PER_NT * hm.HZ
def px(**kw):
    bx = kw.pop("bx", 0.0); by = kw.pop("by", 0.0); t, p = hm.simulate(bx, by, **{**hm.BASE, **kw})
    v = np.interp(ts, t, p, period=hm.PERIOD); return v - v.mean()
st = c["slow_tilt x (1%)"] * px(slow_tilt=(0.01, 0)) + c["slow_tilt y (1%)"] * px(slow_tilt=(0, 0.01))
sb = c["bx (1 nT)"] * px(bx=NT) + c["by (1 nT)"] * px(by=NT)
def h(v, n): return 2 * np.mean(v * np.exp(-1j * n * 2 * np.pi * tc / hm.PERIOD))
for lab, v in (("now (fit)", st + sb), ("static B = 0", st), ("data", y - y.mean())):
    print(lab, "1f %.1f  2f %.2f  3f %.1f mV peak, p2p %.0f" % (abs(h(v, 1)), abs(h(v, 2)), abs(h(v, 3)), np.ptp(v)))
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(tc, y - y.mean(), "k", lw=1.2, label="data now (bias at 2f null)")
ax.plot(tc, st + sb, "C0", lw=0.9, label="fit: static B + slow_tilt")
ax.plot(tc, st, "C3", lw=1.4, label="prediction: static B = 0 (slow_tilt only)")
ax.plot(tc, sb, "C2--", lw=0.9, label="static-B part alone")
ax.set_xlabel("time since +pulse (µs)"); ax.set_ylabel("CH1 − mean (mV)"); ax.legend(fontsize=8)
ax.set_title("predicted Px after nulling static B (model, gain from fit)")
fig.tight_layout(); fig.savefig(RUN / "results" / "predict_zero_field.png", dpi=120)
