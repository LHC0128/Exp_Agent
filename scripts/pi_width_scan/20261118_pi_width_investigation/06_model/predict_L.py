"""样本外检验：用只拟合 c±1 得到的模型参数，预言 c0 对 X 偏置的线性响应 L(τ) 的形状。

只拟合两个实数：X 偏置 20 mV 在原子处产生的横场分量 (δx, δy)。
用法：python predict_L.py <fit.json> <03_phase_grid 目录> <输出目录> [label]
"""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
import fitmodel as fm

fit = json.load(open(sys.argv[1], encoding="utf-8-sig"))
grid_dir = Path(sys.argv[2]); fig_dir = Path(sys.argv[3])
label = sys.argv[4] if len(sys.argv) > 4 else Path(sys.argv[1]).stem
g0, gm, gp = (np.load(grid_dir / f"grid_{t}.npz") for t in ("bias0", "biasm20", "biasp20"))
w = g0["w"]
L = (gp["c0"] - gm["c0"]) / 2
lg, w0, bx, by, s_f, ltau, phi_off, lr = fit["x"]
g, tau = np.exp(lg), np.exp(ltau)
C = complex(*fit["C"])
h = 2 * np.pi * 2e-5                       # 线性响应的差分步长（rad/µs）
Lx, Ly = [], []
for wi in w:
    zx = (fm.fundamental(wi, g, w0, bx + h, by, s_f, tau, 0, 0) - fm.fundamental(wi, g, w0, bx - h, by, s_f, tau, 0, 0)) / (2 * h)
    zy = (fm.fundamental(wi, g, w0, bx, by + h, s_f, tau, 0, 0) - fm.fundamental(wi, g, w0, bx, by - h, s_f, tau, 0, 0)) / (2 * h)
    Lx.append(C * zx); Ly.append(C * zy)
Lx, Ly = np.array(Lx), np.array(Ly)
A = np.concatenate([np.stack([Lx.real, Ly.real], 1), np.stack([Lx.imag, Ly.imag], 1)])
y = np.concatenate([L.real, L.imag])
(dx, dy), *_ = np.linalg.lstsq(A, y, rcond=None)
pred = dx * Lx + dy * Ly
rms = np.sqrt(np.mean(abs(pred - L) ** 2))
zero_data = w[np.argmin(abs(L))]
zero_pred = w[np.argmin(abs(pred))]
txt = (f"{label}: fitted X-bias field per 20 mV: dx/2pi = {dx/2/np.pi*1e6:.0f} Hz, dy/2pi = {dy/2/np.pi*1e6:.0f} Hz;"
       f" rms {rms:.2f} mV (|L| up to {abs(L).max():.1f} mV); |L| min: data {zero_data:g} us, model {zero_pred:g} us")
print(txt)
(fig_dir / f"predict_L_{label}.txt").write_text(txt, encoding="utf-8")
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
ax[0].plot(w, abs(L), "ko", ms=3, label="data |L|"); ax[0].plot(w, abs(pred), "-", label="model (shape from RF-only fit)")
ax[0].set_xlabel("Pulse width (us)"); ax[0].set_ylabel("mV"); ax[0].legend(fontsize=8); ax[0].set_title(txt[:90], fontsize=8)
ax[1].plot(w, np.degrees(np.unwrap(np.angle(L))), "ko", ms=3); ax[1].plot(w, np.degrees(np.unwrap(np.angle(pred))), "-")
ax[1].set_xlabel("Pulse width (us)"); ax[1].set_ylabel("phase (deg)")
fig.tight_layout(); fig.savefig(fig_dir / f"predict_L_{label}.png", dpi=120)


# 追加检验：无偏置 c0 能否只用静态横场（x、y 两个实数分量）的模型响应解释
c0 = g0["c0"]
(ax_, ay_), *_ = np.linalg.lstsq(A, np.concatenate([c0.real, c0.imag]), rcond=None)
pred0 = ax_ * Lx + ay_ * Ly
rms0 = np.sqrt(np.mean(abs(pred0 - c0) ** 2))
txt0 = (f"{label}: c0(X bias 0) explained by static transverse field only: rms {rms0:.2f} mV "
        f"(|c0| up to {abs(c0).max():.1f} mV); field ratio relative to 20 mV X bias: x {ax_/dx:+.2f}, "
        f"y-component {ay_:.3g} (model units)")
print(txt0)
with open(fig_dir / f"predict_L_{label}.txt", "a", encoding="utf-8") as fh:
    fh.write("\n" + txt0)


# 再追加：静态横场 + 泵浦方向倾斜（x、y）共 4 个实数，能否解释无偏置 c0
h2 = 1e-3
Px_, Py_ = [], []
for wi in w:
    base = fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0)
    Px_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, pump=(h2, 0, 1)) - base) / h2)
    Py_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, pump=(0, h2, 1)) - base) / h2)
Px_, Py_ = np.array(Px_), np.array(Py_)
cols = [Lx, Ly, Px_, Py_]
A4 = np.concatenate([np.stack([c.real for c in cols], 1), np.stack([c.imag for c in cols], 1)])
coef4, *_ = np.linalg.lstsq(A4, np.concatenate([c0.real, c0.imag]), rcond=None)
pred4 = sum(k * c for k, c in zip(coef4, cols))
rms4 = np.sqrt(np.mean(abs(pred4 - c0) ** 2))
# 三个偏置一起：c0(b) = 基础(4 参数) + b·(dx Lx + dy Ly)，b = -1, 0, +1（单位 20 mV）
rows_A, rows_y = [], []
for b, gg in ((-1, gm), (0, g0), (1, gp)):
    cols_b = [Lx, Ly, Px_, Py_, b * Lx, b * Ly]
    rows_A.append(np.concatenate([np.stack([c.real for c in cols_b], 1), np.stack([c.imag for c in cols_b], 1)]))
    rows_y.append(np.concatenate([gg["c0"].real, gg["c0"].imag]))
coef6, *_ = np.linalg.lstsq(np.concatenate(rows_A), np.concatenate(rows_y), rcond=None)
res6 = np.concatenate(rows_A) @ coef6 - np.concatenate(rows_y)
rms6 = np.sqrt(np.mean(res6 ** 2) * 2)
txt4 = (f"{label}: c0(bias 0) with static field + pump tilt (4 real): rms {rms4:.2f} mV; "
        f"all three biases jointly (6 real): rms {rms6:.2f} mV")
print(txt4)
with open(fig_dir / f"predict_L_{label}.txt", "a", encoding="utf-8") as fh:
    fh.write("\n" + txt4)
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
ax[0].plot(w, abs(c0), "ko", ms=3, label="data |c0| (X bias 0)")
ax[0].plot(w, abs(pred0), "--", label="static transverse field only")
ax[0].plot(w, abs(pred4), "-", label="static field + pump tilt")
ax[0].set_xlabel("Pulse width (us)"); ax[0].set_ylabel("mV"); ax[0].legend(fontsize=8)
ax[1].plot(w, np.degrees(np.unwrap(np.angle(c0))), "ko", ms=3)
ax[1].plot(w, np.degrees(np.unwrap(np.angle(pred0))), "--"); ax[1].plot(w, np.degrees(np.unwrap(np.angle(pred4))), "-")
ax[1].set_xlabel("Pulse width (us)"); ax[1].set_ylabel("phase (deg)")
fig.tight_layout(); fig.savefig(fig_dir / f"predict_c0_{label}.png", dpi=120)


# 再追加：静态横场 + 线圈轴倾斜（随脉冲与慢拖尾一起反号的横向分量）
h3 = 1e-3
Tx_, Ty_ = [], []
for wi in w:
    base = fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0)
    Tx_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, tilt=(h3, 0)) - base) / h3)
    Ty_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, tilt=(0, h3)) - base) / h3)
Tx_, Ty_ = np.array(Tx_), np.array(Ty_)
rows_A, rows_y = [], []
for b, gg in ((-1, gm), (0, g0), (1, gp)):
    cols_b = [Lx, Ly, Tx_, Ty_, b * Lx, b * Ly]
    rows_A.append(np.concatenate([np.stack([c.real for c in cols_b], 1), np.stack([c.imag for c in cols_b], 1)]))
    rows_y.append(np.concatenate([gg["c0"].real, gg["c0"].imag]))
AA = np.concatenate(rows_A); yy = np.concatenate(rows_y)
coefT, *_ = np.linalg.lstsq(AA, yy, rcond=None)
rmsT = np.sqrt(np.mean((AA @ coefT - yy) ** 2) * 2)
predT = sum(k * c for k, c in zip(coefT[:4], [Lx, Ly, Tx_, Ty_]))
tilt_rad = np.hypot(coefT[2], coefT[3])
txtT = (f"{label}: c0 at three biases with static field + coil-axis tilt (6 real): rms {rmsT:.2f} mV; "
        f"implied coil tilt |t| = {tilt_rad*1e3:.1f} mrad ({np.degrees(tilt_rad):.2f} deg)")
print(txtT)
with open(fig_dir / f"predict_L_{label}.txt", "a", encoding="utf-8") as fh:
    fh.write("\n" + txtT)
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for b, gg, col in ((-1, gm, "C0"), (0, g0, "k"), (1, gp, "C3")):
    pb = predT + b * (coefT[4] * Lx + coefT[5] * Ly)
    ax[0].plot(w, abs(gg["c0"]), "o", ms=3, color=col, label=f"data, X bias {b*20:+d} mV")
    ax[0].plot(w, abs(pb), "-", color=col)
    ax[1].plot(w, np.degrees(np.unwrap(np.angle(gg["c0"]))), "o", ms=3, color=col)
    ax[1].plot(w, np.degrees(np.unwrap(np.angle(pb))), "-", color=col)
ax[0].set_title(txtT[:100], fontsize=8); ax[0].legend(fontsize=8)
for a in ax: a.set_xlabel("Pulse width (us)")
ax[0].set_ylabel("|c0| (mV)"); ax[1].set_ylabel("phase (deg)")
fig.tight_layout(); fig.savefig(fig_dir / f"predict_c0_tilt_{label}.png", dpi=120)


# 再追加：静态横场 + 慢拖尾自带横向分量（只作用在慢分量上）
Sx_, Sy_ = [], []
for wi in w:
    base = fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0)
    Sx_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, slow_tilt=(h3, 0)) - base) / h3)
    Sy_.append(C * (fm.fundamental(wi, g, w0, bx, by, s_f, tau, 0, 0, slow_tilt=(0, h3)) - base) / h3)
Sx_, Sy_ = np.array(Sx_), np.array(Sy_)
rows_A, rows_y = [], []
for b, gg in ((-1, gm), (0, g0), (1, gp)):
    cols_b = [Lx, Ly, Sx_, Sy_, b * Lx, b * Ly]
    rows_A.append(np.concatenate([np.stack([c.real for c in cols_b], 1), np.stack([c.imag for c in cols_b], 1)]))
    rows_y.append(np.concatenate([gg["c0"].real, gg["c0"].imag]))
AA = np.concatenate(rows_A); yy = np.concatenate(rows_y)
coefS, *_ = np.linalg.lstsq(AA, yy, rcond=None)
rmsS = np.sqrt(np.mean((AA @ coefS - yy) ** 2) * 2)
txtS = f"{label}: c0 at three biases with static field + slow-tail transverse component (6 real): rms {rmsS:.2f} mV"
print(txtS)
with open(fig_dir / f"predict_L_{label}.txt", "a", encoding="utf-8") as fh:
    fh.write("\n" + txtS)
predS = sum(k * c for k, c in zip(coefS[:4], [Lx, Ly, Sx_, Sy_]))
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for b, gg, col in ((-1, gm, "C0"), (0, g0, "k"), (1, gp, "C3")):
    pb = predS + b * (coefS[4] * Lx + coefS[5] * Ly)
    ax[0].plot(w, abs(gg["c0"]), "o", ms=3, color=col, label=f"data, X bias {b*20:+d} mV")
    ax[0].plot(w, abs(pb), "-", color=col)
    ax[1].plot(w, np.degrees(np.unwrap(np.angle(gg["c0"]))), "o", ms=3, color=col)
    ax[1].plot(w, np.degrees(np.unwrap(np.angle(pb))), "-", color=col)
ax[0].set_title("S = static transverse field x slow tail + transverse component of slow tail (lines)", fontsize=8)
ax[0].legend(fontsize=8)
for a in ax: a.set_xlabel("Pulse width (us)")
ax[0].set_ylabel("|c0| (mV)"); ax[1].set_ylabel("phase (deg)")
fig.tight_layout(); fig.savefig(fig_dir / f"predict_c0_slowtilt_{label}.png", dpi=120)


# 存档：供 07_summary_figures 绘图
np.savez(fig_dir / f"predict_L_{label}.npz", w=w, L=L, L_model=pred, L_rms=rms,
         c0_m20=gm["c0"], c0_0=g0["c0"], c0_p20=gp["c0"],
         S_model_base=predS, S_model_xlin=coefS[4] * Lx + coefS[5] * Ly, S_rms=rmsS)
