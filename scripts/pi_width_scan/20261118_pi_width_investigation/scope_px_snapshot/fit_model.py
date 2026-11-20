"""示波器 CH1 (Px) 周期折叠波形 与 慢拖尾 Bloch 模型各项的线性拟合。
模型：pi_width_scan/_pi_width_investigation_20261118/08_even_2f_criterion/harmmodel.py（基准 rf_slow.json）。
用法：python fit_model.py → ../results/fit_slow_tilt.png、fit_summary.json
"""
from lab_workflows.common import data_root
import json, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = Path(__file__).resolve().parent
RUN = data_root() / "scope_px_snapshot/1118_135547"  # 采集运行目录
MODEL = Path(__file__).resolve().parents[1] / "08_even_2f_criterion"
sys.path.insert(0, str(MODEL)); import harmmodel as hm  # noqa: E402

D = np.load(RUN / "results" / "folded.npz")
tc, y = D["t_us"], D["ch1"] * 1e3; y = y - y.mean()
ts = (tc + 0.505) % hm.PERIOD   # 数据相位零点 = CH2 过 1 V；模型半高点在 0.505 µs
NT = hm.GAMMA_HZ_PER_NT * hm.HZ; e = 0.01
terms = {"bx (1 nT)": dict(bx=NT), "by (1 nT)": dict(by=NT), "tilt x (1%)": dict(tilt=(e, 0)), "tilt y (1%)": dict(tilt=(0, e)),
         "slow_tilt x (1%)": dict(slow_tilt=(e, 0)), "slow_tilt y (1%)": dict(slow_tilt=(0, e)),
         "pump x (1%)": dict(pump=(e, 0, 1)), "pump y (1%)": dict(pump=(0, e, 1))}
basis = {}
for n, kw in terms.items():
    kw = dict(kw); bx = kw.pop("bx", 0.0); by = kw.pop("by", 0.0)
    t, px = hm.simulate(bx, by, **{**hm.BASE, **kw}); b = np.interp(ts, t, px, period=hm.PERIOD); basis[n] = b - b.mean()
def fit(names):
    A = np.array([basis[n] for n in names]).T; c, *_ = np.linalg.lstsq(A, y, rcond=None)
    return A @ c, dict(zip(names, c)), 1 - np.var(y - A @ c) / np.var(y), float(np.linalg.cond(A))
sets = {"static B + slow_tilt": ["bx (1 nT)", "by (1 nT)", "slow_tilt x (1%)", "slow_tilt y (1%)"],
        "static B + tilt + pump (no slow_tilt)": ["bx (1 nT)", "by (1 nT)", "tilt x (1%)", "tilt y (1%)", "pump x (1%)", "pump y (1%)"],
        "static B only": ["bx (1 nT)", "by (1 nT)"]}
out = {}
fig, ax = plt.subplots(2, 1, figsize=(10, 7.5))
ax[0].plot(tc, y, "k", lw=1.4, label="CH1 folded (data)")
for k, (lab, names) in enumerate(sets.items()):
    f, c, r2, cond = fit(names); out[lab] = dict(R2=r2, cond=cond, coef_mV={n: float(v) for n, v in c.items()})
    ax[0].plot(tc, f, f"C{k}", lw=1.0, label=f"{lab}: R²={r2:.3f}, cond={cond:.0e}")
ax[0].set_xlabel("time since +pulse (µs)"); ax[0].set_ylabel("CH1 − mean (mV)"); ax[0].legend(fontsize=8)
ax[0].set_title("full period fit with model terms (gain absorbed in coefficients)")
# 自洽检查：慢拖尾横向分量在 2f 零点处要求的静态场
c = out["static B + slow_tilt"]["coef_mV"]
sh = {"x": np.array([6.5, 0.85]), "y": np.array([8.2, 3.65])}   # 每 1% slow_tilt 的 2f 零点位置 (nT)，见 08 结论表
pred = c["slow_tilt x (1%)"] * sh["x"] + c["slow_tilt y (1%)"] * sh["y"]
fitb = np.array([c["bx (1 nT)"], c["by (1 nT)"]])
out["self_consistency"] = dict(G_b_fit=fitb.tolist(), G_b_pred_from_slow_tilt_at_2f_null=pred.tolist(),
                               slow_tilt_ratio_y_over_x=c["slow_tilt y (1%)"] / c["slow_tilt x (1%)"])
ax[1].bar([0, 1], fitb / 1e3, 0.35, label="fitted static B (×G)"); ax[1].bar([0.38, 1.38], pred / 1e3, 0.35, label="predicted from fitted slow_tilt at 2f null")
ax[1].set_xticks([0.19, 1.19], ["Bx", "By"]); ax[1].set_ylabel("G·B (a.u. ×1e3)"); ax[1].legend(fontsize=8)
ax[1].set_title("self-consistency: static B in fit equals the 2f-null offset caused by slow_tilt")
fig.tight_layout(); fig.savefig(RUN / "results" / "fit_slow_tilt.png", dpi=120)
(RUN / "results" / "fit_summary.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(out, indent=1, ensure_ascii=False))
