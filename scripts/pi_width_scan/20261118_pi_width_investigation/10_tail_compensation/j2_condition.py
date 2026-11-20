"""2f 判据的条件：精确补偿下 2f 雅可比较弱方向的灵敏度随峰值电流（即 π 宽度）的变化；与不补偿 6.6 µs 对比。
输出 outputs/j2_condition.txt。"""
import sys
from pathlib import Path
import numpy as np
from scipy.optimize import brentq
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import tailcomp as tc
def sv(w, A, ma):
    c0, jx, jy = tc.response(w, A, tc.S_F * tc.TAU_E, current_ma=ma)
    M = np.array([[jx[1].real, jy[1].real], [jx[1].imag, jy[1].imag]]); return np.linalg.svd(M, compute_uv=False)
lines = []
s0 = sv(6.6, 0.0, 190.0); lines.append(f"uncompensated 6.6 us 190 mA: J2 sv {s0[0]:.2e}/{s0[1]:.2e}")
for ma in (190, 200, 210, 220):
    w = brentq(lambda w: tc.rotation(w, *tc.exact_tail(w), current_ma=ma) - 1.0, 5.0, 10.0)
    A = tc.exact_tail(w)[0]; s = sv(w, A, ma)
    lines.append(f"exact comp {ma} mA: w_pi={w:.3f} us A={A:.4f} -> J2 sv {s[0]:.2e}/{s[1]:.2e} (weak/uncomp = {s[1]/s0[1]:.2f})")
for w in (6.0, 6.6, 7.0, 7.5, 8.0):
    s = sv(w, tc.exact_tail(w)[0], 190.0); lines.append(f"  190 mA exact tail at w={w}: rot {tc.rotation(w, *tc.exact_tail(w)):.3f}pi J2 sv {s[0]:.2e}/{s[1]:.2e}")
(HERE / "outputs" / "j2_condition.txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); print("\n".join(lines))
