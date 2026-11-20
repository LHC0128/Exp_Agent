"""由示波器时域拟合 + bperp_null 锁相斜率，推算静态 B⊥=0 所需的 X/Y 偏置。

思路（全部在示波器折叠时间轴下计算，增益 G 与线圈常数 k 只以乘积 G·k 出现）：
1. 时域拟合给出当前状态 G·b（静态场）和 G·s（slow_tilt），单位 mV（峰值）。
2. 锁相 1f 读数 c1_LI 与示波器 1f（峰值/√2）之比 A1 = 复增益 + 相位约定。
3. 锁相 1f 对偏置斜率 S1 = A1/√2 · G·k · J1（J1 为模型 1f 对 1 nT 的斜率）→ G·k_x、G·k_y（应为实数）。
4. 所需偏置改变 ΔV = −(G·b)/(G·k)。2f 斜率用来交叉检验（A2 对 x、y 应相同）。
"""
from lab_workflows.common import data_root
import json, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
RUN = data_root() / "scope_px_snapshot/1118_135547"  # 采集运行目录
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "08_even_2f_criterion"))
import harmmodel as hm  # noqa: E402

D = np.load(RUN / "results" / "folded.npz"); tc, y = D["t_us"], D["ch1"] * 1e3
T = hm.PERIOD; ts = (tc + 0.505) % T; NT = hm.GAMMA_HZ_PER_NT * hm.HZ
def harm(v, n): return 2 * np.mean(v * np.exp(-1j * n * 2 * np.pi * tc / T))
def px(**kw):
    bx = kw.pop("bx", 0.0); by = kw.pop("by", 0.0)
    t, p = hm.simulate(bx, by, **{**hm.BASE, **kw}); return np.interp(ts, t, p, period=T)
fit = json.loads((RUN / "results" / "fit_summary.json").read_text(encoding="utf-8"))["static B + slow_tilt"]["coef_mV"]
Gb = np.array([fit["bx (1 nT)"], fit["by (1 nT)"]])
J = {n: np.array([harm(px(bx=NT), n), harm(px(by=NT), n)]) for n in (1, 2)}          # 模型单位 / nT
K = {n: np.array([harm(px(slow_tilt=(0.01, 0)), n), harm(px(slow_tilt=(0, 0.01)), n)]) for n in (1, 2)}
c1_scope = harm(y, 1); c2_scope = harm(y, 2)
Gs = np.array([fit["slow_tilt x (1%)"], fit["slow_tilt y (1%)"]])
c1_fit = Gb @ J[1] + Gs @ K[1]; c2_fit = Gb @ J[2] + Gs @ K[2]
print("scope c1 %.1f∠%.0f  fit c1 %.1f∠%.0f | scope c2 %.2f  fit c2 %.2f (mV peak)" % (abs(c1_scope), np.degrees(np.angle(c1_scope)), abs(c1_fit), np.degrees(np.angle(c1_fit)), abs(c2_scope), abs(c2_fit)))
# 锁相（1118_131716，rms，mV / mV 偏置）
c1_LI = -27.87 + 33.75j
S = {1: np.array([-0.2155 + 0.1723j, 0.0973 - 0.4425j]), 2: np.array([-0.0296 - 0.0986j, 0.4199 + 0.1912j])}
A1 = c1_LI / (c1_scope / np.sqrt(2))
print("A1 = %.3f∠%.1f°（幅度应≈1）" % (abs(A1), np.degrees(np.angle(A1))))
Gk = np.sqrt(2) * S[1] / (A1 * J[1])
print("G·k from 1f:", np.round(Gk, 1), "（虚部应≈0）")
gk = Gk.real
A2 = np.sqrt(2) * S[2] / (gk * J[2])
print("A2 (x, y):", ["%.3f∠%.1f°" % (abs(a), np.degrees(np.angle(a))) for a in A2], "（两者应相同）")
dV = -Gb / gk
print("ΔV (mV) =", np.round(dV, 1), " → 建议偏置 ≈", np.round(np.array([89.11, -15.07]) + dV, 1))
# 只用 x/y 各自 2f 斜率与 A2 平均值复核
A2m = np.mean(A2); gk2 = (np.sqrt(2) * S[2] / (A2m * J[2])).real
print("G·k from 2f (A2 mean):", np.round(gk2, 1), "→ ΔV", np.round(-Gb / gk2, 1))
# slow_tilt 绝对值不可得（G 未知），但可给出它把 2f 零点带偏的偏置量（=-ΔV）
out = dict(A1=[abs(A1), float(np.degrees(np.angle(A1)))], Gk_1f=[[z.real, z.imag] for z in Gk],
           A2=[[abs(a), float(np.degrees(np.angle(a)))] for a in A2], dV_mV=dV.tolist(),
           suggested_bias_mV=(np.array([89.11, -15.07]) + dV).tolist(), dV_from_2f_mV=(-Gb / gk2).tolist(),
           assumption="采集时偏置在 2f 零点 (89.11, −15.07) mV；X/Y 线圈轴与模型 x/y 对齐")
(RUN / "results" / "null_bias_estimate.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
