"""先看模型的定性形状：S(τ) 与 c±1(τ) 随脉宽的变化，以及在 α=π 是否为零。"""
import sys
import time

import numpy as np

sys.path.insert(0, sys.argv[1])
from blochsim import TWO_PI, harmonics, steady_fundamental

G = TWO_PI * 0.0004          # Γ/2π = 400 Hz
W0 = TWO_PI * 0.0013         # 残余 Bz：1.3 kHz
BX, BY = TWO_PI * 0.0005, TWO_PI * 0.0003
RF = TWO_PI * 0.0001

t0 = time.time()
print("timing one eval ...")
steady_fundamental(5.0, gamma=G, w0=W0, bx=BX, by=BY)
print(f"{time.time()-t0:.2f}s")

for w0 in (0.0, W0):
    print(f"\n== w0/2pi = {w0/TWO_PI*1e6:.0f} Hz  (scale=1 -> pi at 5.025 us)")
    print(" w    alpha/pi  |S|   argS   |c+1|  arg   |c-1|  arg")
    for w in (2.0, 3.0, 4.0, 5.025, 6.0, 7.0, 8.0, 9.05, 10.0):
        alpha = (w - 1) / 4.025
        c0, cp, cm = harmonics(w, gamma=G, w0=w0, bx=BX, by=BY, rf=RF)
        print(f"{w:5.2f} {alpha:6.2f}  {abs(c0)*1e3:7.3f} {np.degrees(np.angle(c0)):6.1f}  "
              f"{abs(cp)*1e3:7.3f} {np.degrees(np.angle(cp)):6.1f}  {abs(cm)*1e3:7.3f} {np.degrees(np.angle(cm)):6.1f}")
    c0, cp, cm = harmonics(1.0, gamma=G, w0=w0, bx=BX, by=BY, rf=RF, pulses=False)
    print(f"no pulse: |c+1| {abs(cp)*1e3:.3f} @ {np.degrees(np.angle(cp)):.1f}, |c-1| {abs(cm)*1e3:.4f}, |S| {abs(c0)*1e3:.4f}")

